"""Local-only MCP adapter over the stable knowledge service contracts.

This module intentionally owns no persistence queries.  Callers compose it with
the existing search, repository, relation, and review services, which keeps the
MCP boundary as a transport adapter rather than a second domain layer.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import date as current_date
from pathlib import Path
from typing import Any, Callable, Mapping


_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "config",
    "cookie",
    "cookies",
    "password",
    "secret",
    "token",
}


def _sensitive_key(value: object) -> bool:
    key = str(value).strip().casefold().replace("-", "_")
    return key in _SENSITIVE_KEYS or any(
        key.endswith(f"_{suffix}")
        for suffix in (
            "api_key", "config", "cookie", "cookies", "password", "secret", "token",
        )
    )


def _public_value(value: Any) -> Any:
    """Convert service results to JSON values while removing secret-bearing fields."""
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    elif hasattr(value, "keys") and not isinstance(value, Mapping):
        value = {key: value[key] for key in value.keys()}

    if isinstance(value, Mapping):
        return {
            str(key): _public_value(item)
            for key, item in value.items()
            if not _sensitive_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_public_value(item) for item in value]
    return value


def _bounded(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50:
        raise ValueError(f"{name} must be an integer in 1..50")
    return value


class KnowledgeMCPService:
    """JSON-shaped operations backed entirely by injected domain services."""

    def __init__(
        self,
        *,
        search_service: Any,
        document_service: Any,
        relation_service: Any,
        review_service: Any,
        reading_state_service: Any,
    ) -> None:
        self._search = search_service
        self._documents = document_service
        self._relations = relation_service
        self._reviews = review_service
        self._reading_state = reading_state_service
        self._owned_services: tuple[Any, ...] = ()

    @classmethod
    def from_database(cls, database: str | Path) -> "KnowledgeMCPService":
        """Compose the adapter from existing SQLite-backed domain services."""
        from pkb.knowledge.repository import KnowledgeRepository
        from pkb.knowledge.search import SearchIndex
        from pkb.review import DailyReviewService

        repository = KnowledgeRepository(database)
        search = SearchIndex(database)
        reviews = DailyReviewService(database)
        service = cls(
            search_service=search,
            document_service=repository,
            relation_service=repository,
            review_service=reviews,
            reading_state_service=repository,
        )
        service._owned_services = (search, reviews, repository)
        return service

    def __enter__(self) -> "KnowledgeMCPService":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        services, self._owned_services = self._owned_services, ()
        for service in services:
            service.close()

    def search_knowledge(
        self,
        query: str,
        source: str | None = None,
        collection: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        result = self._search.search(
            query,
            source=source,
            collection_id=collection,
            limit=_bounded(limit, "limit"),
        )
        return _public_value(result)

    def read_document(self, document_id: int) -> dict[str, Any]:
        result = self._documents.document(document_id)
        if result is None:
            raise KeyError(f"document {document_id} does not exist")
        return _public_value(result)

    def get_related(self, document_id: int, limit: int = 10) -> list[dict[str, Any]]:
        result = self._relations.get_related(document_id, limit=_bounded(limit, "limit"))
        return _public_value(result)

    def get_daily_review(
        self, date: str | None = None, count: int = 5
    ) -> list[dict[str, Any]]:
        result = self._reviews.select(
            date=date or current_date.today().isoformat(),
            count=_bounded(count, "count"),
        )
        return _public_value(result)

    def set_reading_status(self, document_id: int, status: str) -> dict[str, Any]:
        self._reading_state.set_reading_state(document_id, status=status)
        result = self._reading_state.get_reading_state(document_id)
        if result is None:
            raise RuntimeError(f"reading state for document {document_id} was not persisted")
        return _public_value(result)


def create_stdio_server(
    service: KnowledgeMCPService,
    *,
    server_factory: Callable[[str], Any] | None = None,
) -> Any:
    """Register the five approved tools on a local stdio-capable MCP server."""
    if server_factory is None:
        try:
            from mcp.server.fastmcp import FastMCP
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError("install the 'mcp' optional dependency to run the MCP server") from exc
        server_factory = FastMCP

    server = server_factory("pkb")
    for name in (
        "search_knowledge",
        "read_document",
        "get_related",
        "get_daily_review",
        "set_reading_status",
    ):
        server.tool(name=name)(getattr(service, name))
    return server


def run_stdio(service: KnowledgeMCPService) -> None:
    """Run only the SDK's local stdio transport; no HTTP listener is created."""
    create_stdio_server(service).run(transport="stdio")


def main(
    argv: list[str] | None = None,
    *,
    service_runner: Callable[[KnowledgeMCPService], Any] = run_stdio,
) -> int:
    """Start the local MCP stdio transport for one knowledge database."""
    parser = argparse.ArgumentParser(description="Run the local PKB MCP stdio server")
    parser.add_argument("--db", required=True, type=Path)
    args = parser.parse_args(argv)
    with KnowledgeMCPService.from_database(args.db) as service:
        service_runner(service)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())


__all__ = ["KnowledgeMCPService", "create_stdio_server", "main", "run_stdio"]
