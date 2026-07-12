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
