from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import sqlite3

import pytest

from pkb.interfaces.mcp import KnowledgeMCPService, create_stdio_server, main
from pkb.knowledge.migrations import migrate
from pkb.knowledge.repository import KnowledgeRepository
from pkb.knowledge.search import SearchIndex


@dataclass(frozen=True)
class _Result:
    document_id: int
    title: str
    config: dict[str, str] | None = None


class _SearchService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def search(self, query: str, **options: object) -> list[_Result]:
        self.calls.append({"query": query, **options})
        return [_Result(7, "学习方法")]


class _DocumentService:
    def document(self, document_id: int) -> dict[str, object]:
        return {
            "document_id": document_id,
            "title": "本地文档",
            "cookie": "must-not-leak",
            "nested": {
                "api_key": "must-not-leak",
                "provider_config": {"endpoint": "private"},
                "session_cookies": ["must-not-leak"],
                "safe": "yes",
            },
        }


class _RelationService:
    def get_related(self, document_id: int, *, limit: int) -> list[dict[str, object]]:
        return [{"document_id": document_id + 1, "limit": limit}]


class _ReviewService:
    def select(self, *, date: str, count: int) -> tuple[dict[str, object], ...]:
        return ({"date": date, "count": count},)


class _ReadingStateService:
    def __init__(self) -> None:
        self.updated: tuple[int, str] | None = None

    def set_reading_state(self, document_id: int, **values: object) -> None:
        self.updated = (document_id, str(values["status"]))

    def get_reading_state(self, document_id: int) -> dict[str, object]:
        assert self.updated is not None
        return {"document_id": document_id, "status": self.updated[1]}


@pytest.fixture
def services():
    search = _SearchService()
    state = _ReadingStateService()
    adapter = KnowledgeMCPService(
        search_service=search,
        document_service=_DocumentService(),
        relation_service=_RelationService(),
        review_service=_ReviewService(),
        reading_state_service=state,
    )
    return adapter, search, state


def test_search_returns_cli_compatible_shape_and_forwards_filters(services):
    adapter, search, _ = services

    assert adapter.search_knowledge(
        query="学习", source="zhihu", collection="reading", limit=5
    ) == [{"document_id": 7, "title": "学习方法"}]
    assert search.calls == [{
        "query": "学习", "source": "zhihu", "collection_id": "reading", "limit": 5,
    }]


@pytest.mark.parametrize("operation, value", [
    ("search", 0), ("search", 51), ("related", 0), ("related", 51),
    ("review", 0), ("review", 51),
])
def test_bounded_arguments_are_limited_to_one_through_fifty(services, operation, value):
    adapter, _, _ = services

    with pytest.raises(ValueError, match=r"1\.\.50"):
        if operation == "search":
            adapter.search_knowledge(query="学习", limit=value)
        elif operation == "related":
            adapter.get_related(7, limit=value)
        else:
            adapter.get_daily_review(count=value)


def test_read_document_and_nested_results_redact_secrets(services):
    adapter, _, _ = services

    assert adapter.read_document(7) == {
        "document_id": 7,
        "title": "本地文档",
        "nested": {"safe": "yes"},
    }


def test_related_daily_review_and_reading_status_use_service_layer(services):
    adapter, _, state = services

    assert adapter.get_related(7, limit=3) == [{"document_id": 8, "limit": 3}]
    assert adapter.get_daily_review(date="2026-07-13", count=4) == [
        {"date": "2026-07-13", "count": 4}
    ]
    assert adapter.set_reading_status(7, "read") == {"document_id": 7, "status": "read"}
    assert state.updated == (7, "read")


def test_daily_review_defaults_to_local_iso_date(services):
    adapter, _, _ = services

    assert adapter.get_daily_review(count=1) == [{"date": date.today().isoformat(), "count": 1}]


class _FakeMCP:
    def __init__(self, name: str) -> None:
        self.name = name
        self.tools: dict[str, object] = {}

    def tool(self, name: str):
        def register(function):
            self.tools[name] = function
            return function

        return register


def test_stdio_server_registers_only_the_five_approved_tools(services):
    adapter, _, _ = services
    server = create_stdio_server(adapter, server_factory=_FakeMCP)

    assert server.name == "pkb"
    assert set(server.tools) == {
        "search_knowledge",
        "read_document",
        "get_related",
        "get_daily_review",
        "set_reading_status",
    }


def _real_database(tmp_path):
    database = tmp_path / "mcp.sqlite3"
    connection = sqlite3.connect(database)
    migrate(connection)
    for document_id, identity, title, content in (
        (1, "doc:learning", "学习系统", "间隔复习帮助长期记忆。"),
        (2, "doc:notes", "笔记方法", "原子笔记连接相关概念。"),
    ):
        connection.execute(
            """INSERT INTO documents
               (id, identity_key, title, plain_content, canonical_url,
                source_content_hash, normalized_content_hash,
                normalization_version, schema_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)""",
            (document_id, identity, title, content, f"https://example.test/{document_id}",
             f"source-{document_id}", f"normalized-{document_id}"),
        )
    connection.execute(
        """INSERT INTO relations
           (source_document_id, target_document_id, relation_type, score,
            evidence, candidate_evidence_json, explanation)
           VALUES (1, 2, 'supports', 0.8, '[]', '[]', 'grounded explanation')"""
    )
    connection.commit()
    connection.close()
    index = SearchIndex(database)
    try:
        index.rebuild()
    finally:
        index.close()
    return database


def test_repository_exposes_related_documents_as_a_query_service(tmp_path):
    database = _real_database(tmp_path)

    with KnowledgeRepository(database) as repository:
        assert repository.get_related(1, limit=1) == [{
            "document_id": 2,
            "identity": "doc:notes",
            "title": "笔记方法",
            "url": "https://example.test/2",
            "relation_type": "supports",
            "score": 0.8,
            "evidence": [],
            "candidate_evidence": [],
            "explanation": "grounded explanation",
        }]


def test_database_composition_can_execute_all_five_operations(tmp_path):
    database = _real_database(tmp_path)

    with KnowledgeMCPService.from_database(database) as adapter:
        assert adapter.search_knowledge(query="学习", limit=5)[0]["document_id"] == 1
        assert adapter.read_document(1)["title"] == "学习系统"
        assert adapter.get_related(1, limit=5)[0]["document_id"] == 2
        assert len(adapter.get_daily_review(date="2026-07-13", count=1)) == 1
        assert adapter.set_reading_status(1, "read")["status"] == "read"


def test_module_entrypoint_composes_local_stdio_service_and_has_no_http_flags(tmp_path):
    database = _real_database(tmp_path)
    captured = []

    def inspect(adapter):
        captured.append(adapter)
        assert adapter.read_document(1)["title"] == "学习系统"

    assert main(["--db", str(database)], service_runner=inspect) == 0
    assert len(captured) == 1

    with pytest.raises(SystemExit):
        main(["--db", str(database), "--host", "0.0.0.0"], service_runner=inspect)
