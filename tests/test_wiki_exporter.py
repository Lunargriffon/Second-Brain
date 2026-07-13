import json
from pathlib import Path

import pytest

from pkb.wiki.exporter import GeneratedFile, VaultExporter


def page(document_id="doc-1", links=""):
    return (
        "---\n"
        f'id: "{document_id}"\n'
        'generated_by: "pkb"\n'
        "---\n\n"
        f"# Page\n\n{links}\n"
    )


def exporter(*files):
    return VaultExporter(files, renderer_version="renderer-v1")


def test_exporter_refuses_to_overwrite_unmarked_human_file(tmp_path):
    target = tmp_path / "articles" / "doc-1.md"
    target.parent.mkdir(parents=True)
    target.write_text("human text", encoding="utf-8")

    result = exporter(GeneratedFile("articles/doc-1.md", page(), "doc-1")).export(tmp_path)

    assert result.conflicts == ("articles/doc-1.md",)
    assert target.read_text(encoding="utf-8") == "human text"
    assert not (tmp_path / ".pkb-generated.json").exists()


def test_export_is_deterministic_and_manifest_records_ownership(tmp_path):
    files = [
        GeneratedFile("topics/study.md", page("study"), "study"),
        GeneratedFile("articles/doc-1.md", page(), "doc-1"),
    ]
    first = exporter(*files).export(tmp_path)
    manifest = (tmp_path / ".pkb-generated.json").read_bytes()
    manifest_mtime = (tmp_path / ".pkb-generated.json").stat().st_mtime_ns
    article_mtime = (tmp_path / "articles" / "doc-1.md").stat().st_mtime_ns
    second = exporter(*reversed(files)).export(tmp_path)

    assert first.written == ("articles/doc-1.md", "topics/study.md")
    assert second.unchanged == ("articles/doc-1.md", "topics/study.md")
    assert manifest == (tmp_path / ".pkb-generated.json").read_bytes()
    assert (tmp_path / ".pkb-generated.json").stat().st_mtime_ns == manifest_mtime
    assert (tmp_path / "articles" / "doc-1.md").stat().st_mtime_ns == article_mtime
    payload = json.loads(manifest)
    assert payload == {
        "renderer_version": "renderer-v1",
        "files": [
            {
                "path": "articles/doc-1.md",
                "fingerprint": payload["files"][0]["fingerprint"],
                "renderer_version": "renderer-v1",
                "document_id": "doc-1",
            },
            {
                "path": "topics/study.md",
                "fingerprint": payload["files"][1]["fingerprint"],
                "renderer_version": "renderer-v1",
                "document_id": "study",
            },
        ],
    }
    assert "generated_at" not in manifest.decode()


def test_export_never_writes_user_directory(tmp_path):
    with pytest.raises(ValueError, match="user"):
        exporter(GeneratedFile("user/doc-1.md", page(), "doc-1"))

    assert not (tmp_path / "user" / "doc-1.md").exists()


def test_export_rejects_content_without_generated_marker(tmp_path):
    with pytest.raises(ValueError, match="generated marker"):
        exporter(GeneratedFile("articles/doc-1.md", "# Human", "doc-1")).export(tmp_path)
    assert not (tmp_path / "articles" / "doc-1.md").exists()


def test_modified_owned_file_is_not_overwritten(tmp_path):
    exp = exporter(GeneratedFile("articles/doc-1.md", page(), "doc-1"))
    exp.export(tmp_path)
    target = tmp_path / "articles" / "doc-1.md"
    target.write_text(page() + "human edit", encoding="utf-8")

    result = exp.export(tmp_path)

    assert result.conflicts == ("articles/doc-1.md",)
    assert target.read_text(encoding="utf-8").endswith("human edit")


@pytest.mark.parametrize(
    ("document_id", "renderer_version"),
    [("different", "renderer-v1"), ("doc-1", "renderer-v2")],
)
def test_manifest_identity_and_renderer_version_must_match_to_overwrite(
    tmp_path, document_id, renderer_version
):
    exporter(GeneratedFile("articles/doc-1.md", page(), "doc-1")).export(tmp_path)
    target = tmp_path / "articles" / "doc-1.md"
    original = target.read_bytes()

    result = VaultExporter(
        [GeneratedFile("articles/doc-1.md", page() + "changed", document_id)],
        renderer_version=renderer_version,
    ).export(tmp_path)

    assert result.conflicts == ("articles/doc-1.md",)
    assert target.read_bytes() == original


def test_stale_owned_file_requires_explicit_removal(tmp_path):
    initial = exporter(GeneratedFile("articles/old.md", page("old"), "old"))
    initial.export(tmp_path)
    empty = exporter()

    kept = empty.export(tmp_path)
    assert kept.stale == ("articles/old.md",)
    assert (tmp_path / "articles" / "old.md").exists()

    removed = empty.export(tmp_path, remove_stale=True)
    assert removed.removed == ("articles/old.md",)
    assert not (tmp_path / "articles" / "old.md").exists()


def test_check_classifies_user_note_and_broken_generated_link(tmp_path):
    article = page("doc-1", "[[../user/doc-1]] [[../topics/missing|Missing]]")
    exp = exporter(GeneratedFile("articles/doc-1.md", article, "doc-1"))
    exp.export(tmp_path)

    report = exp.check(tmp_path)

    assert report.creatable_user_notes == ("user/doc-1.md",)
    assert report.broken_links == ("topics/missing.md",)
    assert not report.ok


def test_only_single_safe_user_note_target_is_creatable(tmp_path):
    article = page("doc-1", "[[../user/sub/note]] [[../user/../outside]]")
    exp = exporter(GeneratedFile("articles/doc-1.md", article, "doc-1"))
    exp.export(tmp_path)

    report = exp.check(tmp_path)

    assert report.creatable_user_notes == ()
    assert "user/sub/note.md" in report.broken_links


def test_check_reports_modified_unmarked_manifest_mismatch_and_missing_id(tmp_path):
    exp = exporter(GeneratedFile("articles/doc-1.md", page(), "doc-1"))
    exp.export(tmp_path)
    target = tmp_path / "articles" / "doc-1.md"
    target.write_text('---\ngenerated_by: "pkb"\n---\n', encoding="utf-8")
    unmarked = tmp_path / "topics" / "human.md"
    unmarked.parent.mkdir(parents=True)
    unmarked.write_text("human", encoding="utf-8")

    report = exporter(
        GeneratedFile("articles/doc-1.md", page(), "doc-1"),
        GeneratedFile("topics/human.md", page("human"), "human"),
    ).check(tmp_path)

    assert report.modified == ("articles/doc-1.md",)
    assert report.missing_source_ids == ("articles/doc-1.md",)
    assert report.unmarked_collisions == ("topics/human.md",)
    assert report.manifest_mismatches == ("topics/human.md",)


def test_check_reports_desired_fingerprint_different_from_manifest(tmp_path):
    exporter(GeneratedFile("articles/doc-1.md", page(), "doc-1")).export(tmp_path)

    report = exporter(
        GeneratedFile("articles/doc-1.md", page() + "new render", "doc-1")
    ).check(tmp_path)

    assert report.manifest_mismatches == ("articles/doc-1.md",)


@pytest.mark.parametrize(
    "article",
    [
        page("wrong-id"),
        page("doc-1").replace('id: "doc-1"', 'id: "doc-1"\nid: "doc-1"'),
    ],
)
def test_check_rejects_mismatched_or_duplicate_article_source_id(tmp_path, article):
    exp = exporter(GeneratedFile("articles/doc-1.md", article, "doc-1"))
    exp.export(tmp_path)

    report = exp.check(tmp_path)

    assert report.manifest_mismatches == ("articles/doc-1.md",)
    assert not report.ok


@pytest.mark.parametrize("unsafe", ["../escape.md", "/absolute.md", "user/../escape.md", ".pkb-generated.json"])
def test_rejects_unsafe_generated_paths(tmp_path, unsafe):
    with pytest.raises(ValueError, match="unsafe|reserved"):
        exporter(GeneratedFile(unsafe, page(), "doc-1"))


def test_rejects_manifest_traversal_and_symlink_escape(tmp_path):
    (tmp_path / ".pkb-generated.json").write_text(
        json.dumps({"renderer_version": "x", "files": [{"path": "../outside.md", "fingerprint": "x", "renderer_version": "x", "document_id": "x"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsafe"):
        exporter().check(tmp_path)

    outside = tmp_path.parent / "outside-vault"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "articles"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="escape|symlink"):
        exporter(GeneratedFile("articles/doc.md", page("doc"), "doc")).export(tmp_path)
    assert not (outside / "doc.md").exists()


def test_rejects_target_file_symlink(tmp_path):
    outside = tmp_path.parent / "outside-note.md"
    outside.write_text("secret", encoding="utf-8")
    target = tmp_path / "articles" / "doc.md"
    target.parent.mkdir()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")

    with pytest.raises(ValueError, match="symlink"):
        exporter(GeneratedFile("articles/doc.md", page("doc"), "doc")).export(tmp_path)
    assert outside.read_text(encoding="utf-8") == "secret"


def test_failed_replace_leaves_original_and_no_temp_files(tmp_path, monkeypatch):
    exp = exporter(GeneratedFile("articles/doc-1.md", page(), "doc-1"))
    exp.export(tmp_path)
    original = (tmp_path / "articles" / "doc-1.md").read_bytes()
    changed = exporter(GeneratedFile("articles/doc-1.md", page() + "changed", "doc-1"))
    import pkb.wiki.exporter as module

    real_replace = module.os.replace
    monkeypatch.setattr(module.os, "replace", lambda src, dst: (_ for _ in ()).throw(OSError("boom")) if Path(dst).name != ".pkb-generated.json" else real_replace(src, dst))
    with pytest.raises(OSError, match="boom"):
        changed.export(tmp_path)

    assert (tmp_path / "articles" / "doc-1.md").read_bytes() == original
    assert not list(tmp_path.rglob("*.tmp"))


def test_failure_on_second_page_rolls_back_first_page(tmp_path, monkeypatch):
    exp = exporter(
        GeneratedFile("articles/a.md", page("a"), "a"),
        GeneratedFile("articles/b.md", page("b"), "b"),
    )
    import pkb.wiki.exporter as module

    real_replace = module.os.replace
    page_replaces = 0

    def fail_second_page(src, dst):
        nonlocal page_replaces
        if Path(dst).name != ".pkb-generated.json":
            page_replaces += 1
            if page_replaces == 2:
                raise OSError("second page failed")
        return real_replace(src, dst)

    monkeypatch.setattr(module.os, "replace", fail_second_page)
    with pytest.raises(OSError, match="second page failed"):
        exp.export(tmp_path)

    assert not (tmp_path / "articles" / "a.md").exists()
    assert not (tmp_path / "articles" / "b.md").exists()
    assert not (tmp_path / ".pkb-generated.json").exists()
    assert not list(tmp_path.rglob("*.tmp"))
