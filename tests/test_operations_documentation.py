from pathlib import Path


WEB_DECISION = Path("docs/web-interface-decision.md")


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
