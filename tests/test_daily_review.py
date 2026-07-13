from __future__ import annotations

import json
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from pkb.cli import main
from pkb.derive.prompts import ARTICLE_PROMPT_VERSION
from pkb.knowledge.fingerprint import derivation_input_hash
from pkb.knowledge.repository import KnowledgeRepository
from pkb.review import DailyReviewService
from pkb.wiki.renderer import render_daily_review, stable_document_id


def _payload(topic: str, *, reading: int, evergreen: int) -> dict[str, object]:
    return {
        "summary": f"Summary for {topic}",
        "key_points": ["Point"],
        "topics": [{"name": topic, "confidence": 0.9}],
        "tags": [],
        "content_type": "reference",
        "evergreen_score": evergreen,
        "reading_priority": reading,
        "priority_reason": "Useful now",
        "source_citations": [{"claim": "Point", "excerpt": "source text"}],
    }


def _add_document(
    repository: KnowledgeRepository,
    number: int,
    topic: str,
    *,
    reading: int = 3,
    evergreen: int = 3,
    status: str | None = None,
    manual_priority: int | None = None,
    last_reviewed: str | None = None,
    stale_ai: bool = False,
) -> str:
    identity = f"fixture:{number}"
    source_hash, normalized_hash = f"source-{number}", f"normalized-{number}"
    repository.connection.execute(
        """INSERT INTO documents
           (identity_key, title, plain_content, canonical_url, source_content_hash,
            normalized_content_hash, normalization_version, schema_version,
            source_created_at)
           VALUES (?, ?, 'source text', ?, ?, ?, 1, 1, ?)""",
        (identity, f"Article {number}", f"https://example.test/{number}", source_hash,
         normalized_hash, f"2025-01-{number:02d}T00:00:00Z"),
    )
    document_id = int(repository.connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    expected = derivation_input_hash(source_hash, normalized_hash, 1)
    repository.connection.execute(
        """INSERT INTO derivations
           (id, document_id, kind, payload_json, input_hash, source_content_hash,
            normalized_content_hash, normalization_version, schema_version,
            prompt_version, status)
           VALUES (?, ?, 'article', ?, ?, ?, ?, 1, 1, ?, 'accepted')""",
        (f"derivation-{number}", document_id,
         json.dumps(_payload(topic, reading=reading, evergreen=evergreen)),
         "stale" if stale_ai else expected, source_hash, normalized_hash,
         ARTICLE_PROMPT_VERSION),
    )
    if status is not None or manual_priority is not None or last_reviewed is not None:
        repository.set_reading_state(
            document_id,
            status=status or "unread",
            priority=manual_priority,
            last_reviewed=last_reviewed,
        )
    repository.connection.commit()
    return stable_document_id(identity)


@pytest.fixture
def review_database(tmp_path: Path) -> Path:
    database = tmp_path / "knowledge.db"
    with KnowledgeRepository(database) as repository:
        _add_document(repository, 1, "Python", reading=5, evergreen=5)
        _add_document(repository, 2, "Databases", reading=4, evergreen=5)
        _add_document(repository, 3, "Writing", reading=3, evergreen=4)
        _add_document(repository, 4, "Python", reading=5, evergreen=4)
        _add_document(repository, 5, "Databases", reading=3, evergreen=3)
        _add_document(repository, 6, "Writing", reading=2, evergreen=2)
        _add_document(repository, 7, "Other", reading=1, evergreen=1, status="read")
        _add_document(repository, 8, "Other", reading=5, evergreen=5, status="ignored")
        _add_document(repository, 9, "Other", reading=5, evergreen=5,
                      last_reviewed="2026-07-01T00:00:00Z")
    return database


def test_daily_review_is_bounded_diverse_and_reproducible(review_database: Path):
    with DailyReviewService(review_database) as service:
        first = service.select(date="2026-07-12", count=5)
        second = service.select(date="2026-07-12", count=5)
    assert first == second
    assert len(first) == 5
    assert len({item.primary_topic for item in first}) >= 3
    assert all(item.status in {"unread", "queued"} for item in first)
    assert all(item.stable_id.startswith("document-") for item in first)


def test_manual_priority_overrides_ai_and_stale_ai_payload_is_not_trusted(tmp_path: Path):
    database = tmp_path / "knowledge.db"
    with KnowledgeRepository(database) as repository:
        manual = _add_document(repository, 1, "Manual", reading=0, evergreen=0,
                               manual_priority=5)
        _add_document(repository, 2, "AI", reading=5, evergreen=5)
        stale = _add_document(repository, 3, "Untrusted", reading=5, evergreen=5,
                              stale_ai=True)
    with DailyReviewService(database) as service:
        items = service.select(date="2026-07-12", count=3)
    assert items[0].stable_id == manual
    stale_item = next(item for item in items if item.stable_id == stale)
    assert stale_item.ai_reading_priority == 0
    assert stale_item.evergreen_score == 0
    assert stale_item.primary_topic == "Uncategorized"


def test_diversity_is_best_effort_and_fills_requested_count(tmp_path: Path):
    database = tmp_path / "knowledge.db"
    with KnowledgeRepository(database) as repository:
        for number in range(1, 7):
            _add_document(repository, number, "Only topic", reading=6 - number, evergreen=3)
    with DailyReviewService(database) as service:
        items = service.select(date="2026-07-12", count=5)
    assert len(items) == 5
    assert {item.primary_topic for item in items} == {"Only topic"}


def test_diversity_never_crosses_manual_priority_layer(tmp_path: Path):
    database = tmp_path / "knowledge.db"
    with KnowledgeRepository(database) as repository:
        top_one = _add_document(repository, 1, "Same", reading=0, evergreen=0,
                                manual_priority=5)
        top_two = _add_document(repository, 2, "Same", reading=0, evergreen=0,
                                manual_priority=5)
        _add_document(repository, 3, "Different", reading=5, evergreen=5,
                      manual_priority=4)
    with DailyReviewService(database) as service:
        ids = {item.stable_id for item in service.select(date="2026-07-12", count=2)}
    assert ids == {top_one, top_two}


def test_review_window_exact_boundary_offsets_and_future(tmp_path: Path):
    database = tmp_path / "knowledge.db"
    with KnowledgeRepository(database) as repository:
        boundary = _add_document(repository, 1, "A",
                                 last_reviewed="2026-06-12T00:00:00+00:00")
        _add_document(repository, 2, "B",
                      last_reviewed="2026-06-12T08:00:01+08:00")
        _add_document(repository, 3, "C",
                      last_reviewed="2026-07-13T00:00:00+00:00")
    with DailyReviewService(database) as service:
        ids = {item.stable_id for item in service.select(date="2026-07-12", count=10)}
    assert ids == {boundary}


@pytest.mark.parametrize("bad_date", ["20260712", "2026-7-12", "2026-02-30", "nope"])
def test_review_date_is_strict_iso(review_database: Path, bad_date: str):
    with DailyReviewService(review_database) as service:
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            service.select(date=bad_date, count=1)


def test_select_is_read_only(review_database: Path):
    with DailyReviewService(review_database) as service:
        before = service.repository.connection.total_changes
        service.select(date="2026-07-12", count=5)
        after = service.repository.connection.total_changes
        states = service.repository.connection.execute(
            "SELECT COUNT(*) FROM reading_state"
        ).fetchone()[0]
    assert after == before
    assert states == 3


def test_primary_topic_uses_confidence_then_normalized_name(tmp_path: Path):
    database = tmp_path / "knowledge.db"
    with KnowledgeRepository(database) as repository:
        _add_document(repository, 1, "ignored", reading=3, evergreen=3)
        payload = _payload("ignored", reading=3, evergreen=3)
        payload["topics"] = [
            {"name": "Zulu", "confidence": 0.8},
            {"name": "alpha", "confidence": 0.9},
            {"name": "Alpha", "confidence": 0.9},
        ]
        repository.connection.execute(
            "UPDATE derivations SET payload_json=?", (json.dumps(payload),)
        )
        repository.connection.commit()
    with DailyReviewService(database) as service:
        item = service.select(date="2026-07-12", count=1)[0]
    assert item.primary_topic == "Alpha"


def test_equal_scores_use_date_seeded_sha256_tie_break(tmp_path: Path):
    database = tmp_path / "knowledge.db"
    with KnowledgeRepository(database) as repository:
        ids = [_add_document(repository, number, "Same", reading=3, evergreen=3)
               for number in (1, 2)]
        repository.connection.execute(
            "UPDATE documents SET source_created_at='2025-01-01T00:00:00Z'"
        )
        repository.connection.commit()
    day = "2026-07-02"
    expected = max(ids, key=lambda stable_id: sha256(
        f"{day}\0{stable_id}".encode("utf-8")
    ).hexdigest())
    with DailyReviewService(database) as service:
        selected = service.select(date=day, count=1)[0]
    assert selected.stable_id == expected


def test_equal_score_ranking_is_independent_of_database_insertion_order(tmp_path: Path):
    day = "2026-07-02"
    selections: list[str] = []
    stable_ids: list[str] = []
    for database_number, insertion_order in enumerate(((1, 2), (2, 1)), 1):
        database = tmp_path / f"knowledge-{database_number}.db"
        with KnowledgeRepository(database) as repository:
            ids = {
                number: _add_document(repository, number, "Same", reading=3, evergreen=3)
                for number in insertion_order
            }
            repository.connection.execute(
                "UPDATE documents SET source_created_at='2025-01-01T00:00:00Z'"
            )
            repository.connection.commit()
        stable_ids = list(ids.values())
        with DailyReviewService(database) as service:
            selections.append(service.select(date=day, count=1)[0].stable_id)

    expected = max(stable_ids, key=lambda stable_id: sha256(
        f"{day}\0{stable_id}".encode("utf-8")
    ).hexdigest())
    assert selections == [expected, expected]


def test_uncategorized_candidates_fill_only_after_real_topic_diversity(tmp_path: Path):
    database = tmp_path / "knowledge.db"
    with KnowledgeRepository(database) as repository:
        uncategorized = {
            _add_document(repository, number, "Ignored", reading=5, evergreen=5)
            for number in (1, 2)
        }
        uncategorized_payload = _payload("Ignored", reading=5, evergreen=5)
        uncategorized_payload["topics"] = []
        repository.connection.execute(
            "UPDATE derivations SET payload_json=? WHERE document_id IN (1, 2)",
            (json.dumps(uncategorized_payload),),
        )
        repository.connection.commit()
        topic_a = _add_document(repository, 3, "Topic A", reading=2, evergreen=2)
        topic_b = _add_document(repository, 4, "Topic B", reading=1, evergreen=1)

    with DailyReviewService(database) as service:
        selected = service.select(date="2026-07-12", count=2)

    selected_ids = {item.stable_id for item in selected}
    assert selected_ids == {topic_a, topic_b}
    assert not selected_ids & uncategorized


def test_review_vault_preserves_complete_wiki_manifest(review_database: Path, tmp_path: Path):
    vault = tmp_path / "vault"
    assert main(["wiki", "export", "--db", str(review_database),
                 "--vault", str(vault)]) == 0
    article = next((vault / "articles").glob("*.md"))
    article_content = article.read_bytes()

    assert main(["review", "today", "--db", str(review_database), "--count", "2",
                 "--date", "2026-07-12", "--vault", str(vault)]) == 0
    assert article.read_bytes() == article_content
    assert main(["wiki", "check", "--vault", str(vault), "--format", "json"]) == 0
    manifest = json.loads((vault / ".pkb-generated.json").read_text(encoding="utf-8"))
    article_entry = next(item for item in manifest["files"] if item["path"] == article.relative_to(vault).as_posix())
    assert article_entry.get("stale") is not True


@pytest.mark.parametrize("count", [0, -1, 101, True])
def test_count_must_be_positive_and_bounded(review_database: Path, count: int):
    with DailyReviewService(review_database) as service:
        with pytest.raises(ValueError, match="count"):
            service.select(date="2026-07-12", count=count)


def test_mark_uses_stable_id_and_is_a_human_state_change(review_database: Path):
    with DailyReviewService(review_database) as service:
        item = service.select(date="2026-07-12", count=1)[0]
        state = service.mark(item.stable_id, status="read", date="2026-07-12")
        assert state.status == "read"
        assert state.last_reviewed == "2026-07-12T00:00:00Z"
        assert service.select(date="2026-07-13", count=100)
        assert item.stable_id not in {candidate.stable_id for candidate in service.select(date="2026-07-13", count=100)}
    with pytest.raises(ValueError, match="stable document ID"):
        with DailyReviewService(review_database) as service:
            service.mark("1", status="read", date="2026-07-12")


def test_renderer_has_stable_article_links_prompts_and_generated_marker(review_database: Path):
    with DailyReviewService(review_database) as service:
        items = service.select(date="2026-07-12", count=2)
    text = render_daily_review("2026-07-12", items)
    assert 'generated_by: "pkb"' in text
    assert f"[[../articles/{items[0].stable_id}|Article" in text
    assert "Why review:" in text
    assert "After reading:" in text
    assert text == render_daily_review("2026-07-12", items)


def test_cli_today_writes_atomically_without_overwriting_human_daily_file(
    review_database: Path, tmp_path: Path, capsys
):
    vault = tmp_path / "vault"
    assert main(["review", "today", "--db", str(review_database), "--count", "2",
                 "--date", "2026-07-12", "--vault", str(vault)]) == 0
    daily = vault / "daily" / "2026-07-12.md"
    original = daily.read_text(encoding="utf-8")
    assert original.count("[[../articles/") == 2
    assert main(["review", "today", "--db", str(review_database), "--count", "2",
                 "--date", "2026-07-12", "--vault", str(vault)]) == 0
    assert daily.read_text(encoding="utf-8") == original
    modified = original + "\nmanual addition\n"
    daily.write_text(modified, encoding="utf-8")
    assert main(["review", "today", "--db", str(review_database), "--count", "2",
                 "--date", "2026-07-12", "--vault", str(vault)]) == 1
    assert daily.read_text(encoding="utf-8") == modified

    other_vault = tmp_path / "human-vault"
    human_daily = other_vault / "daily" / "2026-07-12.md"
    human_daily.parent.mkdir(parents=True)
    human_daily.write_text("human notes\n", encoding="utf-8")
    assert main(["review", "today", "--db", str(review_database), "--count", "2",
                 "--date", "2026-07-12", "--vault", str(other_vault)]) == 1
    assert human_daily.read_text(encoding="utf-8") == "human notes\n"


def test_cli_mark_accepts_stable_document_id(review_database: Path, capsys):
    with DailyReviewService(review_database) as service:
        stable_id = service.select(date="2026-07-12", count=1)[0].stable_id
    assert main(["review", "mark", stable_id, "--db", str(review_database),
                 "--status", "queued", "--date", "2026-07-12"]) == 0
    output = capsys.readouterr().out
    assert stable_id in output
    with DailyReviewService(review_database) as service:
        assert service.state(stable_id).status == "queued"


def test_simulated_seven_days_never_returns_marked_items(tmp_path: Path):
    database = tmp_path / "knowledge.db"
    with KnowledgeRepository(database) as repository:
        for number in range(1, 15):
            _add_document(repository, number, f"Topic {number % 4}", reading=number % 6,
                          evergreen=(number * 2) % 6)
    seen: set[str] = set()
    start = date(2026, 7, 1)
    with DailyReviewService(database) as service:
        for offset in range(7):
            day = (start + timedelta(days=offset)).isoformat()
            items = service.select(date=day, count=2)
            assert not seen.intersection(item.stable_id for item in items)
            for index, item in enumerate(items):
                service.mark(item.stable_id, status="read" if index == 0 else "ignored", date=day)
                seen.add(item.stable_id)
    assert len(seen) == 14
