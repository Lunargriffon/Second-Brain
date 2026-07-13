"""Pure, deterministic Markdown rendering for the generated Obsidian vault."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from collections.abc import Sequence
from dataclasses import dataclass


NOT_GENERATED = "尚未生成"
_LEADING_MARKDOWN = re.compile(r"^(\s*)(#{1,6}\s|[-*+]\s|>\s?|\d+[.)]\s|---+$)")


@dataclass(frozen=True)
class LabelView:
    name: str
    normalized_name: str
    path_id: str | None = None


@dataclass(frozen=True)
class CitationView:
    claim: str
    excerpt: str


@dataclass(frozen=True)
class DerivationView:
    summary: str
    key_points: tuple[str, ...]
    topics: tuple[LabelView, ...]
    tags: tuple[LabelView, ...]
    source_citations: tuple[CitationView, ...]


@dataclass(frozen=True)
class ArticleView:
    id: str
    title: str
    source: str
    source_url: str
    source_content_hash: str
    normalized_content_hash: str
    normalization_version: int
    derivation_id: str | None
    collections: tuple[LabelView, ...] = ()
    manual_tags: tuple[LabelView, ...] = ()
    derivation: DerivationView | None = None
    image_links: tuple[str, ...] = ()


@dataclass(frozen=True)
class IndexEntryView:
    id: str
    title: str
    normalized_title: str


def _yaml(value: object) -> str:
    """JSON strings/scalars are a safe, deterministic subset of YAML."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def stable_document_id(identity_key: str) -> str:
    """Project a repository identity key to a path-safe, rebuild-stable ID."""

    if not isinstance(identity_key, str) or not identity_key:
        raise ValueError("identity_key must be a non-empty string")
    digest = sha256(identity_key.encode("utf-8")).hexdigest()
    return f"document-{digest[:32]}"


def stable_label_id(kind: str, normalized_name: str) -> str:
    if not kind or not normalized_name:
        raise ValueError("kind and normalized_name must be non-empty")
    digest = sha256(f"{kind}\0{normalized_name}".encode("utf-8")).hexdigest()
    return f"{kind}-{digest[:24]}"


def _stable_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"unsafe stable id: {value!r}")
    identifier = value
    if (
        not identifier
        or identifier in {".", ".."}
        or not identifier[0].isalnum()
        or any(not (character.isalnum() or character in "._-") for character in identifier)
    ):
        raise ValueError(f"unsafe stable id: {identifier!r}")
    return identifier


def _inline(value: object) -> str:
    text = " ".join(str(value).splitlines())
    for character in ("\\", "`", "[", "]", "|", "*", "_", "<", ">"):
        text = text.replace(character, "\\" + character)
    return text


def _block(value: object) -> str:
    lines = []
    for raw_line in str(value).splitlines() or [""]:
        line = raw_line.replace("\\", "\\\\")
        line = line.replace("`", "\\`").replace("[", "\\[").replace("]", "\\]")
        line = line.replace("<", "\\<").replace(">", "\\>")
        line = _LEADING_MARKDOWN.sub(lambda match: match[1] + "\\" + match[2], line)
        lines.append(line)
    return "\n".join(lines)


def _label(label: LabelView) -> tuple[str, str, str]:
    return label.normalized_name, _stable_id(label.path_id or label.normalized_name), label.name


def _sorted_labels(labels: Sequence[LabelView]) -> list[tuple[str, str, str]]:
    unique = {_label(label) for label in labels}
    return sorted(unique, key=lambda item: (item[0], item[2].casefold(), item[2], item[1]))


def _wikilink(directory: str, identifier: str, label: str) -> str:
    return f"[[../{directory}/{_stable_id(identifier)}|{_inline(label)}]]"


def _frontmatter(fields: Sequence[tuple[str, object]]) -> str:
    return "---\n" + "\n".join(f"{key}: {_yaml(value)}" for key, value in fields) + "\n---"


def _label_section(
    title: str, labels: Sequence[LabelView], directory: str
) -> str:
    rendered = [f"- {_wikilink(directory, identifier, name)}" for _, identifier, name in _sorted_labels(labels)]
    return f"## {title}\n\n" + ("\n".join(rendered) if rendered else NOT_GENERATED)


def render_article(view: ArticleView) -> str:
    """Render one article page without consulting clocks, files, or databases."""

    document_id = _stable_id(view.id)
    derivation = view.derivation

    collections = list(view.collections)
    collection_ids = [identifier for _, identifier, _ in _sorted_labels(collections)]
    fields = [
        ("id", document_id),
        ("source", view.source),
        ("source_url", view.source_url),
        ("collections", collection_ids),
        ("source_content_hash", view.source_content_hash),
        ("normalized_content_hash", view.normalized_content_hash),
        ("normalization_version", view.normalization_version),
        ("derivation_id", view.derivation_id),
        ("generated_by", "pkb"),
    ]

    if derivation is None:
        summary = key_points = citations = NOT_GENERATED
        topics: Sequence[LabelView] = ()
        ai_tags: Sequence[LabelView] = ()
    else:
        summary = _block(derivation.summary)
        points = derivation.key_points
        key_points = "\n".join(f"- {_block(point)}" for point in points) or NOT_GENERATED
        citation_items = derivation.source_citations
        citations = (
            "\n\n".join(
                f"- **Claim:** {_block(item.claim)}\n  **Excerpt:** {_block(item.excerpt)}"
                for item in citation_items
            )
            or NOT_GENERATED
        )
        topics = derivation.topics
        ai_tags = derivation.tags

    manual_tags = view.manual_tags
    source_url = _inline(view.source_url) or NOT_GENERATED
    source = _inline(view.source) or NOT_GENERATED
    title = _inline(view.title)
    sections = [
        _frontmatter(fields),
        f"# {title}",
        f"## Summary\n\n{summary}",
        f"## Key points\n\n{key_points}",
        _label_section("Topics", topics, "topics"),
        _label_section("Tags", [*ai_tags, *manual_tags], "tags"),
        _label_section("Collections", collections, "collections"),
        "## Images\n\n" + ("\n".join(f"![]({_inline(link)})" for link in view.image_links) or NOT_GENERATED),
        f"## Source citations\n\n{citations}",
        (
            "## Source\n\n"
            f"- Source: {source}\n"
            f"- URL: {source_url}\n"
            f"- Source content hash: `{_inline(view.source_content_hash)}`\n"
            f"- Normalized content hash: `{_inline(view.normalized_content_hash)}`"
        ),
        f"## Personal notes\n\nSee [[../user/{document_id}]]",
    ]
    return "\n\n".join(sections) + "\n"


def _render_index(
    kind: str, name: str, entries: Sequence[IndexEntryView]
) -> str:
    ordered = sorted(
        entries,
        key=lambda entry: (
            entry.normalized_title.casefold(),
            _stable_id(entry.id),
        ),
    )
    links = "\n".join(
        f"- {_wikilink('articles', _stable_id(entry.id), entry.title)}"
        for entry in ordered
    ) or NOT_GENERATED
    return (
        _frontmatter((("index_kind", kind), ("name", name), ("generated_by", "pkb")))
        + f"\n\n# {_inline(name)}\n\n{links}\n"
    )


def render_source_index(name: str, entries: Sequence[IndexEntryView]) -> str:
    return _render_index("source", name, entries)


def render_topic_index(name: str, entries: Sequence[IndexEntryView]) -> str:
    return _render_index("topic", name, entries)


def render_tag_index(name: str, entries: Sequence[IndexEntryView]) -> str:
    return _render_index("tag", name, entries)


def render_collection_index(name: str, entries: Sequence[IndexEntryView]) -> str:
    return _render_index("collection", name, entries)
