from pathlib import Path


WEB_DECISION = Path("docs/web-interface-decision.md")
OPERATIONS_GUIDE = Path("docs/second-brain-operations.md")
README = Path("README.md")


def test_web_decision_has_explicit_evidence_gate():
    text = WEB_DECISION.read_text(encoding="utf-8")

    for factor in (
        "Cross-device access need",
        "Obsidian review workflow gap",
        "Visual relation review need",
        "Nontechnical user need",
        "Maintenance budget",
        "Remote-access security implications",
    ):
        assert factor in text

    assert "Observed" in text
    assert "Unknown" in text
    assert "Speculation" in text
    assert text.rstrip().endswith("decision: defer")
    assert text.count("decision: defer") == 1


def test_operations_guide_covers_required_workflows():
    text = OPERATIONS_GUIDE.read_text(encoding="utf-8")

    for heading in (
        "## Build the Index",
        "## Search",
        "## Derive a Small Batch",
        "## Export the Vault",
        "## Daily Review",
        "## Recover Expired Jobs",
        "## Rebuild Projections",
        "## Back Up Human State",
    ):
        assert heading in text

    for safety_term in (
        "--dry-run",
        "--limit",
        "data/raw",
        "data/frozen",
        "vault/user",
        "reading_state",
        "normalization",
        "stdio",
    ):
        assert safety_term in text


def test_operations_guide_covers_bounded_douyin_trial_and_recovery():
    text = OPERATIONS_GUIDE.read_text(encoding="utf-8")

    for required in (
        "## Import Douyin Favorites Transcripts",
        'python -m pip install -e ".[dev,search,douyin]"',
        "pkb export douyin-favorites --limit 20 --request-delay 7",
        "pkb index build --raw-dir data/raw --db data/index/knowledge.db --strict",
        'pkb search "口述内容中的短语" --db data/index/knowledge.db --source douyin',
        "auth_required",
        "captcha",
        "http_403",
        "http_429",
        "cleanup_pending",
        "30-second chunks",
        "completed chunks",
        "media_url_unavailable",
        "two consecutive runs",
        "five speech-bearing transcripts",
    ):
        assert required in text


def test_operations_guide_covers_full_douyin_sync_and_recovery():
    text = OPERATIONS_GUIDE.read_text(encoding="utf-8")

    assert "pkb export douyin-favorites --all --reclassify --request-delay 7" in text
    assert "three consecutive" in text
    assert "knowledge-value" in text
    assert "folder names are not used" in text
    assert "ambiguous metadata is excluded" in text
    assert "data/backups/douyin-favorites" in text


def test_readme_links_shipped_guides_and_has_clean_workflow():
    text = README.read_text(encoding="utf-8")

    assert "second-brain-architecture-design.md" in text
    assert "second-brain-operations.md" in text
    assert "index build -> search/derive small batch -> wiki export -> review today" in text
    assert "�" not in text
    assert "鈹" not in text
