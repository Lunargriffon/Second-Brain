from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pkb.cli import main
from pkb.derive.prompts import ARTICLE_PROMPT_VERSION
from pkb.knowledge.fingerprint import derivation_input_hash
from pkb.knowledge.migrations import migrate


def _database(path: Path, *, accepted: bool = True) -> Path:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    migrate(connection)
    connection.execute(
        """INSERT INTO documents
           (identity_key, source_type, title, plain_content, canonical_url,
            source_content_hash, normalized_content_hash, normalization_version,
            schema_version)
           VALUES ('zhihu:answer:42', 'zhihu', '学习方法', '原文片段',
                   'https://www.zhihu.com/question/1/answer/42', 'source-hash',
                   'normalized-hash', 1, 1)"""
    )
    connection.execute(
        """INSERT INTO source_memberships
           (document_id, source, source_item_id, collection_id, collection_title,
            source_url)
           VALUES (1, 'zhihu', '42', '7', '学习收藏',
                   'https://www.zhihu.com/question/1/answer/42')"""
    )
    connection.execute(
        "INSERT INTO tags(normalized_name, display_name) VALUES ('manual', '手工标签')"
    )
    connection.execute(
        "INSERT INTO document_tags(document_id, tag_id, origin) VALUES (1, 1, 'user')"
    )
    input_hash = derivation_input_hash('source-hash', 'normalized-hash', 1)
    status = 'accepted' if accepted else 'rejected'
    payload = {
        'summary': '先理解再练习',
        'key_points': ['原文片段'],
        'topics': [{'name': '学习', 'confidence': 0.9}],
        'tags': [{'name': '方法', 'confidence': 0.8}],
        'source_citations': [{'claim': '要练习', 'excerpt': '原文片段'}],
    }
    connection.execute(
        """INSERT INTO derivations
           (id, document_id, kind, payload_json, input_hash, source_content_hash,
            normalized_content_hash, normalization_version, schema_version,
            prompt_version, status)
           VALUES ('current', 1, 'article', ?, ?, 'source-hash', 'normalized-hash',
                   1, 1, ?, ?)""",
        (json.dumps(payload, ensure_ascii=False), input_hash, ARTICLE_PROMPT_VERSION, status),
    )
    # Accepted but stale derivation must never leak into the current article.
    connection.execute(
        """INSERT INTO derivations
           (id, document_id, kind, payload_json, input_hash, source_content_hash,
            normalized_content_hash, normalization_version, schema_version,
            prompt_version, status)
           VALUES ('stale', 1, 'article', ?, 'old-input', 'old-source', 'old-normalized',
                   1, 1, ?, 'accepted')""",
        (json.dumps({**payload, 'summary': 'STALE SECRET'}), ARTICLE_PROMPT_VERSION),
    )
    connection.commit()
    connection.close()
    return path


def test_wiki_export_builds_deterministic_projection_without_stale_derivation(tmp_path):
    db = _database(tmp_path / 'knowledge.db')
    vault = tmp_path / 'vault'

    assert main(['wiki', 'export', '--db', str(db), '--vault', str(vault)]) == 0
    articles = list((vault / 'articles').glob('*.md'))
    assert len(articles) == 1
    text = articles[0].read_text(encoding='utf-8')
    assert '先理解再练习' in text
    assert 'STALE SECRET' not in text
    assert (vault / 'sources' / 'zhihu.md').exists()
    assert len(list((vault / 'topics').glob('*.md'))) == 1
    assert len(list((vault / 'tags').glob('*.md'))) == 2
    assert len(list((vault / 'collections').glob('*.md'))) == 1
    assert main(['wiki', 'check', '--vault', str(vault)]) == 0


def test_rejected_current_derivation_renders_empty_sections(tmp_path):
    db = _database(tmp_path / 'knowledge.db', accepted=False)
    vault = tmp_path / 'vault'
    assert main(['wiki', 'export', '--db', str(db), '--vault', str(vault)]) == 0
    text = next((vault / 'articles').glob('*.md')).read_text(encoding='utf-8')
    assert '先理解再练习' not in text
    assert '尚未生成' in text


def test_wiki_export_report_contains_only_aggregate_counts(tmp_path):
    db = _database(tmp_path / 'knowledge.db')
    vault = tmp_path / 'vault'
    report = tmp_path / 'report.json'
    assert main(['wiki', 'export', '--db', str(db), '--vault', str(vault), '--report', str(report)]) == 0
    payload = json.loads(report.read_text(encoding='utf-8'))
    assert payload['written'] > 0
    assert 'paths' not in payload
    assert '学习方法' not in report.read_text(encoding='utf-8')


def test_wiki_check_json_exit_code_tracks_ok(tmp_path, capsys):
    db = _database(tmp_path / 'knowledge.db')
    vault = tmp_path / 'vault'
    main(['wiki', 'export', '--db', str(db), '--vault', str(vault)])
    article = next((vault / 'articles').glob('*.md'))
    article.write_text(article.read_text(encoding='utf-8') + 'changed', encoding='utf-8')
    assert main(['wiki', 'check', '--vault', str(vault), '--format', 'json']) == 1
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload['ok'] is False
    assert payload['modified'] == 1


def test_wiki_check_does_not_hide_article_identity_mismatch(tmp_path, capsys):
    db = _database(tmp_path / 'knowledge.db')
    vault = tmp_path / 'vault'
    main(['wiki', 'export', '--db', str(db), '--vault', str(vault)])
    article = next((vault / 'articles').glob('*.md'))
    original = article.read_text(encoding='utf-8')
    article.write_text(original.replace('id: "document-', 'id: "wrong-document-', 1), encoding='utf-8')
    # Update the ownership fingerprint to isolate semantic identity checking.
    manifest_path = vault / '.pkb-generated.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    import hashlib
    for item in manifest['files']:
        if item['path'].startswith('articles/'):
            item['fingerprint'] = hashlib.sha256(article.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    assert main(['wiki', 'check', '--vault', str(vault), '--format', 'json']) == 1
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload['manifest_mismatches'] == 1


def test_wiki_clean_dry_run_then_removes_only_unmodified_stale(tmp_path):
    db = _database(tmp_path / 'knowledge.db')
    vault = tmp_path / 'vault'
    main(['wiki', 'export', '--db', str(db), '--vault', str(vault)])
    collection = next((vault / 'collections').glob('*.md'))
    with sqlite3.connect(db) as connection:
        connection.execute('DELETE FROM source_memberships')
    main(['wiki', 'export', '--db', str(db), '--vault', str(vault)])
    assert collection.exists()
    assert main(['wiki', 'clean', '--vault', str(vault), '--stale-only', '--dry-run']) == 0
    assert collection.exists()
    assert main(['wiki', 'clean', '--vault', str(vault), '--stale-only']) == 0
    assert not collection.exists()


def test_wiki_clean_preserves_modified_stale_file(tmp_path):
    db = _database(tmp_path / 'knowledge.db')
    vault = tmp_path / 'vault'
    main(['wiki', 'export', '--db', str(db), '--vault', str(vault)])
    collection = next((vault / 'collections').glob('*.md'))
    with sqlite3.connect(db) as connection:
        connection.execute('DELETE FROM source_memberships')
    main(['wiki', 'export', '--db', str(db), '--vault', str(vault)])
    collection.write_text('human modification', encoding='utf-8')
    assert main(['wiki', 'clean', '--vault', str(vault), '--stale-only']) == 1
    assert collection.read_text(encoding='utf-8') == 'human modification'


def test_images_link_in_place_by_default_and_copy_only_on_opt_in(tmp_path):
    db = _database(tmp_path / 'knowledge.db')
    image = tmp_path / 'data' / 'images' / 'photo space.jpg'
    image.parent.mkdir(parents=True)
    image.write_bytes(b'image')
    with sqlite3.connect(db) as connection:
        connection.execute(
            "INSERT INTO media(document_id, remote_url, local_path, media_order) VALUES (1, 'https://img/x', ?, 0)",
            ('data/images/photo space.jpg',),
        )
    vault = tmp_path / 'vault'
    main(['wiki', 'export', '--db', str(db), '--vault', str(vault)])
    article = next((vault / 'articles').glob('*.md'))
    assert '../attachments/' not in article.read_text(encoding='utf-8')
    assert 'data/images/photo%20space.jpg' in article.read_text(encoding='utf-8').replace('\\', '/')
    assert not (vault / 'attachments').exists()

    assert main(['wiki', 'export', '--db', str(db), '--vault', str(vault), '--copy-attachments']) == 0
    assert (vault / 'attachments').glob('*')
    assert '../attachments/' in article.read_text(encoding='utf-8')


def test_missing_or_remote_media_is_not_rendered_or_copied(tmp_path):
    db = _database(tmp_path / 'knowledge.db')
    with sqlite3.connect(db) as connection:
        connection.execute(
            "INSERT INTO media(document_id, remote_url, local_path) VALUES (1, 'https://secret.example/image', ?)",
            (str(tmp_path / 'missing.jpg'),),
        )
    vault = tmp_path / 'vault'
    assert main(['wiki', 'export', '--db', str(db), '--vault', str(vault), '--copy-attachments']) == 0
    article = next((vault / 'articles').glob('*.md')).read_text(encoding='utf-8')
    assert 'secret.example' not in article
    assert not (vault / 'attachments').exists()


def test_absolute_and_traversing_image_paths_are_rejected(tmp_path):
    db = _database(tmp_path / 'knowledge.db')
    outside = tmp_path / 'evil' / 'data' / 'images' / 'secret.jpg'
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b'secret')
    with sqlite3.connect(db) as connection:
        connection.executemany(
            "INSERT INTO media(document_id, remote_url, local_path, media_order) VALUES (1, ?, ?, ?)",
            [('https://img/absolute', str(outside), 0), ('https://img/traversal', '../data/images/secret.jpg', 1)],
        )
    vault = tmp_path / 'vault'
    assert main(['wiki', 'export', '--db', str(db), '--vault', str(vault), '--copy-attachments']) == 0
    assert not (vault / 'attachments').exists()
    assert 'secret.jpg' not in next((vault / 'articles').glob('*.md')).read_text(encoding='utf-8')


def test_unsafe_label_names_use_safe_deterministic_index_paths(tmp_path):
    db = _database(tmp_path / 'knowledge.db')
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO tags(normalized_name, display_name) VALUES ('a/b : 中文', 'A/B : 中文')")
        connection.execute("INSERT INTO document_tags(document_id, tag_id, origin) VALUES (1, 2, 'user')")
    vault = tmp_path / 'vault'
    assert main(['wiki', 'export', '--db', str(db), '--vault', str(vault)]) == 0
    paths = [path.relative_to(vault).as_posix() for path in vault.rglob('*.md')]
    assert all(':' not in path for path in paths)
    assert not (vault / 'tags' / 'a').exists()
    assert 'A/B : 中文' in next((vault / 'articles').glob('*.md')).read_text(encoding='utf-8')
