"""Read-only construction of the generated vault projection."""

from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

from pkb.derive.prompts import ARTICLE_PROMPT_VERSION
from pkb.knowledge.fingerprint import derivation_input_hash
from pkb.wiki.exporter import GeneratedFile
from pkb.wiki.renderer import (
    ArticleView, CitationView, DerivationView, IndexEntryView, LabelView,
    render_article, render_collection_index, render_source_index,
    render_tag_index, render_topic_index, stable_document_id, stable_label_id,
)

RENDERER_VERSION = "wiki-v1"


def _label(name: str, normalized: str | None = None, *, kind: str) -> LabelView:
    normalized_name = normalized if normalized is not None else name.strip().casefold()
    return LabelView(name, normalized_name, stable_label_id(kind, normalized_name))


def _safe_local_image(raw: str | None, database: Path) -> Path | None:
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts or path.parts[:2] != ("data", "images"):
        return None
    bases = [Path.cwd(), database.parent, *database.parents]
    for base in dict.fromkeys(bases):
        candidate = base / path
        current = base
        unsafe = False
        for part in path.parts:
            current /= part
            if current.is_symlink():
                unsafe = True
                break
        if unsafe:
            continue
        try:
            resolved = candidate.resolve(strict=True)
            image_root = (base / "data" / "images").resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file() and resolved.is_relative_to(image_root):
            return resolved
    return None


def _url_path(path: str) -> str:
    return quote(path.replace("\\", "/"), safe="/.-_~")


def build_generated_files(database: Path, vault: Path, *, copy_attachments: bool = False) -> list[GeneratedFile]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        documents = connection.execute("SELECT * FROM documents ORDER BY identity_key").fetchall()
        files: list[GeneratedFile] = []
        indexes: dict[tuple[str, str, str], list[IndexEntryView]] = defaultdict(list)
        for document in documents:
            stable_id = stable_document_id(document["identity_key"])
            entry = IndexEntryView(stable_id, document["title"] or "Untitled", (document["title"] or "").casefold())
            memberships = connection.execute(
                "SELECT * FROM source_memberships WHERE document_id=? ORDER BY source, collection_id, source_item_id",
                (document["id"],),
            ).fetchall()
            collections: dict[str, LabelView] = {}
            for row in memberships:
                indexes[("sources", row["source"], row["source"])].append(entry)
                if row["collection_id"]:
                    title = row["collection_title"] or row["collection_id"]
                    label = _label(title, row["collection_id"], kind="collection")
                    collections[row["collection_id"]] = label
                    indexes[("collections", label.path_id, title)].append(entry)
            manual = [_label(row["display_name"], row["normalized_name"], kind="tag") for row in connection.execute(
                """SELECT t.display_name, t.normalized_name FROM document_tags dt
                   JOIN tags t ON t.id=dt.tag_id WHERE dt.document_id=? AND dt.origin='user'
                   ORDER BY t.normalized_name""", (document["id"],)
            )]
            for tag in manual:
                indexes[("tags", tag.path_id, tag.name)].append(entry)
            expected = derivation_input_hash(document["source_content_hash"], document["normalized_content_hash"], document["normalization_version"])
            derived_row = connection.execute(
                """SELECT id, payload_json FROM derivations
                   WHERE document_id=? AND kind='article' AND status='accepted'
                     AND input_hash=? AND source_content_hash=? AND normalized_content_hash=?
                     AND normalization_version=? AND prompt_version=?
                   ORDER BY id LIMIT 1""",
                (document["id"], expected, document["source_content_hash"], document["normalized_content_hash"],
                 document["normalization_version"], ARTICLE_PROMPT_VERSION),
            ).fetchone()
            derivation = None
            derivation_id = None
            if derived_row:
                payload = json.loads(derived_row["payload_json"])
                topics = tuple(_label(item["name"], kind="topic") for item in payload["topics"])
                tags = tuple(_label(item["name"], kind="tag") for item in payload["tags"])
                derivation = DerivationView(
                    payload["summary"], tuple(payload["key_points"]), topics, tags,
                    tuple(CitationView(item["claim"], item["excerpt"]) for item in payload["source_citations"]),
                )
                derivation_id = derived_row["id"]
                for directory, labels in (("topics", topics), ("tags", tags)):
                    for label in labels:
                        indexes[(directory, label.path_id, label.name)].append(entry)
            image_links: list[str] = []
            attachments: list[GeneratedFile] = []
            for media in connection.execute("SELECT local_path FROM media WHERE document_id=? ORDER BY media_order, id", (document["id"],)):
                image = _safe_local_image(media["local_path"], Path(database))
                if image is None:
                    continue
                if copy_attachments:
                    digest = __import__("hashlib").sha256(str(image).encode()).hexdigest()[:16]
                    relative = f"attachments/{stable_id}-{digest}{image.suffix.lower()}"
                    image_links.append(f"../{relative}")
                    attachments.append(GeneratedFile(relative, image.read_bytes(), stable_id))
                else:
                    relative = os.path.relpath(image, vault / "articles")
                    image_links.append(_url_path(relative))
            source_url = next((row["source_url"] for row in memberships if row["source_url"]), document["canonical_url"] or "")
            article = ArticleView(
                stable_id, document["title"] or "Untitled", document["source_type"] or "", source_url,
                document["source_content_hash"], document["normalized_content_hash"], document["normalization_version"],
                derivation_id, tuple(collections.values()), tuple(manual), derivation, tuple(image_links),
            )
            files.append(GeneratedFile(f"articles/{stable_id}.md", render_article(article), stable_id))
            files.extend(attachments)
        renderers = {"sources": render_source_index, "topics": render_topic_index, "tags": render_tag_index, "collections": render_collection_index}
        collapsed: dict[tuple[str, str], tuple[set[str], list[IndexEntryView]]] = {}
        for (directory, identifier, name), entries in indexes.items():
            names, combined = collapsed.setdefault((directory, identifier), (set(), []))
            names.add(name)
            combined.extend(entries)
        for (directory, identifier), (names, entries) in sorted(collapsed.items()):
            name = sorted(names, key=lambda value: (value.casefold(), value))[0]
            unique_entries = {entry.id: entry for entry in entries}
            files.append(GeneratedFile(
                f"{directory}/{identifier}.md",
                renderers[directory](name, tuple(unique_entries.values())), identifier,
            ))
        return files
    finally:
        connection.close()
