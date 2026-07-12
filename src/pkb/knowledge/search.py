"""Jieba-backed external-content FTS5 search projection."""

from __future__ import annotations

import hashlib
import html
import logging
import re
import sqlite3
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API.*")
    import jieba

jieba.setLogLevel(logging.WARNING)

from .migrations import migrate

if TYPE_CHECKING:
    from .repository import KnowledgeRepository


@dataclass(frozen=True)
class SearchMembership:
    source: str
    source_item_id: str
    collection_id: str
    collection_title: str | None
    source_url: str | None
    raw_path: str | None
    raw_line: int | None


@dataclass(frozen=True)
class SearchResult:
    document_id: int
    identity: str
    title: str
    url: str
    rank: float
    snippet: str
    memberships: tuple[SearchMembership, ...]


class SearchIndex:
    """Maintain and query the rebuildable full-text projection."""

    strategy = "jieba"

    def __init__(self, database: str | Path | KnowledgeRepository):
        if hasattr(database, "connection"):
            self.connection = database.connection
            self._owns_connection = False
        else:
            self.connection = sqlite3.connect(database)
            self._owns_connection = True
        self.connection.row_factory = sqlite3.Row
        migrate(self.connection)
        self.tokenizer_version = getattr(jieba, "__version__", "unknown")
        self.dictionary_fingerprint = self._hash_file(self._dictionary_file())

    @staticmethod
    def _dictionary_file() -> Path:
        handle = jieba.dt.get_dict_file()
        try:
            return Path(handle.name)
        finally:
            handle.close()

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def close(self) -> None:
        if self._owns_connection:
            self.connection.close()

    @staticmethod
    def _tokens(text: str) -> tuple[str, ...]:
        seen: set[str] = set()
        tokens: list[str] = []
        for value in jieba.cut_for_search(text or ""):
            token = value.strip().casefold()
            if not token or not any(character.isalnum() or character in "+#" for character in token):
                continue
            if token not in seen:
                seen.add(token)
                tokens.append(token)
        return tuple(tokens)

    @classmethod
    def _terms(cls, text: str) -> str:
        return " ".join(cls._tokens(text))

    def index_document(self, document_id: int) -> None:
        with self.connection:
            self._index_document(document_id)

    def _index_document(self, document_id: int) -> None:
        document = self.connection.execute(
            "SELECT id, title, plain_content FROM documents WHERE id=?", (document_id,)
        ).fetchone()
        if document is None:
            raise KeyError(f"document {document_id} does not exist")
        existing = self.connection.execute(
            "SELECT summary, tags FROM documents_search_content WHERE document_id=?",
            (document_id,),
        ).fetchone()
        summary, tags = (existing["summary"], existing["tags"]) if existing else ("", "")
        title = document["title"] or ""
        content = document["plain_content"] or ""
        self.connection.execute(
                """INSERT INTO documents_search_content
                   (document_id, title, content, summary, tags, title_terms,
                    content_terms, summary_terms, tags_terms, strategy,
                    tokenizer_version, dictionary_fingerprint)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(document_id) DO UPDATE SET
                     title=excluded.title, content=excluded.content,
                     summary=excluded.summary, tags=excluded.tags,
                     title_terms=excluded.title_terms,
                     content_terms=excluded.content_terms,
                     summary_terms=excluded.summary_terms,
                     tags_terms=excluded.tags_terms, strategy=excluded.strategy,
                     tokenizer_version=excluded.tokenizer_version,
                     dictionary_fingerprint=excluded.dictionary_fingerprint""",
                (
                    document_id, title, content, summary, tags,
                    self._terms(title), self._terms(content), self._terms(summary),
                    self._terms(tags), self.strategy, self.tokenizer_version,
                    self.dictionary_fingerprint,
                ),
        )

    def rebuild(self) -> int:
        document_ids = [row[0] for row in self.connection.execute("SELECT id FROM documents ORDER BY id")]
        with self.connection:
            self.connection.execute("DELETE FROM documents_search_content")
            for document_id in document_ids:
                self._index_document(document_id)
        return len(document_ids)

    def update_derived_projection(
        self, document_id: int, *, summary: str = "", tags: tuple[str, ...] | list[str] = ()
    ) -> None:
        if self.connection.execute(
            "SELECT 1 FROM documents_search_content WHERE document_id=?", (document_id,)
        ).fetchone() is None:
            self.index_document(document_id)
        tags_text = " ".join(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
        with self.connection:
            self.connection.execute(
                """UPDATE documents_search_content
                   SET summary=?, tags=?, summary_terms=?, tags_terms=?,
                       strategy=?, tokenizer_version=?, dictionary_fingerprint=?
                   WHERE document_id=?""",
                (
                    summary, tags_text, self._terms(summary), self._terms(tags_text),
                    self.strategy, self.tokenizer_version, self.dictionary_fingerprint,
                    document_id,
                ),
            )

    def search(
        self,
        query: str,
        *,
        source: str | None = None,
        collection_id: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        tokens = self._tokens(query)
        if not tokens:
            return []
        match = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
        membership_predicates: list[str] = []
        parameters: list[object] = [match]
        if source is not None:
            membership_predicates.append("m.source=?")
            parameters.append(source)
        if collection_id is not None:
            membership_predicates.append("m.collection_id=?")
            parameters.append(collection_id)
        membership_filter = ""
        if membership_predicates:
            membership_filter = (
                " AND EXISTS (SELECT 1 FROM source_memberships m WHERE m.document_id=d.id AND "
                + " AND ".join(membership_predicates)
                + ")"
            )
        parameters.append(limit)
        rows = self.connection.execute(
            """SELECT d.id, d.identity_key, d.title, d.canonical_url,
                      s.title AS original_title, s.content, s.summary, s.tags,
                      bm25(documents_fts, 10.0, 1.0, 3.0, 5.0) AS score
               FROM documents_fts
               JOIN documents_search_content s ON s.document_id=documents_fts.rowid
               JOIN documents d ON d.id=s.document_id
               WHERE documents_fts MATCH ?"""
            + membership_filter
            + " ORDER BY score ASC, d.id ASC LIMIT ?",
            parameters,
        ).fetchall()
        memberships = self._load_memberships([int(row["id"]) for row in rows])
        return [
            SearchResult(
                document_id=int(row["id"]),
                identity=row["identity_key"],
                title=row["title"] or "",
                url=row["canonical_url"] or "",
                rank=float(row["score"]),
                snippet=self._snippet(row, query, tokens),
                memberships=memberships.get(int(row["id"]), ()),
            )
            for row in rows
        ]

    def _load_memberships(self, document_ids: list[int]) -> dict[int, tuple[SearchMembership, ...]]:
        if not document_ids:
            return {}
        placeholders = ",".join("?" for _ in document_ids)
        rows = self.connection.execute(
            f"""SELECT document_id, source, source_item_id, collection_id,
                       collection_title, source_url, raw_path, raw_line
                FROM source_memberships WHERE document_id IN ({placeholders})
                ORDER BY document_id, id""",
            document_ids,
        ).fetchall()
        grouped: dict[int, list[SearchMembership]] = {}
        for row in rows:
            grouped.setdefault(int(row["document_id"]), []).append(
                SearchMembership(*tuple(row)[1:])
            )
        return {key: tuple(value) for key, value in grouped.items()}

    @classmethod
    def _snippet(cls, row: sqlite3.Row, query: str, tokens: tuple[str, ...]) -> str:
        fields = (row["original_title"] or "", row["content"] or "", row["summary"] or "", row["tags"] or "")
        source = next((field for field in fields if any(token in field.casefold() for token in tokens)), fields[0])
        phrase = query.strip().strip('"').strip()
        candidates = sorted({phrase, *tokens}, key=len, reverse=True)
        candidates = [candidate for candidate in candidates if candidate]
        if not candidates:
            return html.escape(source[:300])
        folded = source.casefold()
        positions = [folded.find(candidate.casefold()) for candidate in candidates]
        positions = [position for position in positions if position >= 0]
        if positions:
            match_position = min(positions)
            start = max(0, match_position - 100)
            source = source[start : start + 300]
        else:
            source = source[:300]
        pattern = re.compile("(" + "|".join(re.escape(item) for item in candidates) + ")", re.IGNORECASE)
        pieces = pattern.split(source)
        return "".join(
            f"<mark>{html.escape(piece)}</mark>" if index % 2 else html.escape(piece)
            for index, piece in enumerate(pieces)
        )
