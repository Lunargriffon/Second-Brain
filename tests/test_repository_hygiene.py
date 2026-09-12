from pathlib import Path


def test_gitignore_excludes_personal_and_generated_data():
    text = Path(".gitignore").read_text(encoding="utf-8")
    required = {
        ".env",
        "data/raw/",
        "data/images/",
        "data/frozen/",
        "data/index/",
        "data/derived/",
        "vault/articles/",
        "vault/_index/",
        ".superpowers/",
    }
    assert required <= set(text.splitlines())


def test_agent_guidance_uses_codex_neutral_filename():
    guidance = Path("AGENTS.md")

    assert guidance.is_file()
    assert guidance.read_text(encoding="utf-8").startswith("# AGENTS.md\n")
    assert not Path("CLAUDE.md").exists()
