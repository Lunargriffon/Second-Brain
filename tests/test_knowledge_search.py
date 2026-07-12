import hashlib
import sqlite3

import pytest

from pkb.knowledge.migrations import migrate
from pkb.knowledge.repository import KnowledgeRepository
from pkb.knowledge.search import SearchIndex, SearchResult


def add_document(connection, identity, title, content, url="", *, source="zhihu", item="1", collection="c1"):
    cursor = connection.execute(
        """INSERT INTO documents
           (identity_key, title, plain_content, canonical_url, source_content_hash,
            normalized_content_hash, normalization_version, schema_version)
           VALUES (?, ?, ?, ?, ?, ?, 1, 1)""",
        (identity, title, content, url, identity + "-s", identity + "-n"),
    )
    document_id = cursor.lastrowid
    connection.execute(
        """INSERT INTO source_memberships
           (document_id, source, source_item_id, collection_id, collection_title,
            source_url, raw_path, raw_line)
           VALUES (?, ?, ?, ?, ?, ?, 'raw.jsonl', 1)""",
        (document_id, source, item, collection, collection, url),
    )
    connection.commit()
    return document_id


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "knowledge.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    migrate(connection)
    yield path, connection
    connection.close()


def test_search_returns_immutable_ranked_source_context(database):
    path, connection = database
    document_id = add_document(
        connection, "zhihu:1", "学习新知识", "这是一套高效学习方法", "https://example.test/1"
    )
    index = SearchIndex(path)
    index.rebuild()

    result = index.search("学习方法")[0]

    assert isinstance(result, SearchResult)
    assert result.document_id == document_id
    assert result.identity == "zhihu:1"
    assert result.title == "学习新知识"
    assert result.url == "https://example.test/1"
    assert isinstance(result.rank, float)
    assert "<mark>" in result.snippet
    assert result.memberships[0].source == "zhihu"
    with pytest.raises((AttributeError, TypeError)):
        result.title = "changed"


def test_search_supports_chinese_mixed_language_and_safe_metacharacters(database):
    path, connection = database
    ids = [
        add_document(connection, "a", "C++ 中文教程", "使用 vector 与机器学习", item="a"),
        add_document(connection, "b", '他说 "知识管理"', "引号也能搜索", item="b"),
    ]
    index = SearchIndex(path)
    index.rebuild()

    assert [r.document_id for r in index.search("中文 C++")] == [ids[0]]
    assert [r.document_id for r in index.search('"知识管理"')] == [ids[1]]


def test_search_validates_query_and_limit(database):
    index = SearchIndex(database[0])
    for query in ("", "  "):
        with pytest.raises(ValueError, match="query"):
            index.search(query)
    for limit in (0, 51):
        with pytest.raises(ValueError, match="limit"):
            index.search("内容", limit=limit)


def test_filters_require_source_and_collection_on_same_membership_and_deduplicate(database):
    path, connection = database
    wanted = add_document(connection, "wanted", "知识", "知识管理", source="zhihu", item="z1", collection="10")
    other = add_document(connection, "other", "知识", "知识管理", source="zhihu", item="z2", collection="20")
    connection.execute(
        "INSERT INTO source_memberships (document_id, source, source_item_id, collection_id) VALUES (?, 'x', 'x1', '10')",
        (other,),
    )
    connection.execute(
        "INSERT INTO source_memberships (document_id, source, source_item_id, collection_id) VALUES (?, 'zhihu', 'z3', '10')",
        (wanted,),
    )
    connection.commit()
    index = SearchIndex(path)
    index.rebuild()

    results = index.search("知识", source="zhihu", collection_id="10")

    assert [result.document_id for result in results] == [wanted]
    assert len(results[0].memberships) == 2


def test_index_document_replaces_content_and_delete_trigger_removes_it(database):
    path, connection = database
    document_id = add_document(connection, "edit", "旧标题", "legacyuniquetoken")
    index = SearchIndex(path)
    index.index_document(document_id)
    assert index.search("legacyuniquetoken")

    connection.execute("UPDATE documents SET title='新标题', plain_content='replacementtoken' WHERE id=?", (document_id,))
    connection.commit()
    index.index_document(document_id)
    assert index.search("legacyuniquetoken") == []
    assert index.search("replacementtoken")[0].document_id == document_id

    connection.execute("DELETE FROM documents WHERE id=?", (document_id,))
    connection.commit()
    assert index.search("replacementtoken") == []


def test_derived_projection_refreshes_summary_and_tags(database):
    path, connection = database
    document_id = add_document(connection, "derived", "文章", "正文")
    index = SearchIndex(path)
    index.index_document(document_id)

    index.update_derived_projection(document_id, summary="费曼学习法", tags=("认知科学", "教育"))

    assert index.search("费曼")[0].document_id == document_id
    assert index.search("认知科学")[0].document_id == document_id
    row = connection.execute(
        "SELECT summary, tags FROM documents_search_content WHERE document_id=?", (document_id,)
    ).fetchone()
    assert tuple(row) == ("费曼学习法", "认知科学 教育")


def test_snippet_escapes_html_and_only_adds_controlled_marks(database):
    path, connection = database
    add_document(connection, "xss", "安全", '<script>alert(1)</script> 学习方法 <mark>伪造</mark>')
    index = SearchIndex(path)
    index.rebuild()

    snippet = index.search("学习方法")[0].snippet

    assert "<script>" not in snippet
    assert "&lt;script&gt;" in snippet
    assert "<mark>学习方法</mark>" in snippet
    assert "<mark>伪造</mark>" not in snippet
    assert "学 习 方 法" not in snippet


def test_title_weight_wins_and_ties_are_stable_by_document_id(database):
    path, connection = database
    title_match = add_document(connection, "title", "量子计算", "普通正文", item="1")
    content_match = add_document(connection, "content", "普通标题", "量子计算", item="2")
    tie_a = add_document(connection, "tie-a", "稳定排序", "内容", item="3")
    tie_b = add_document(connection, "tie-b", "稳定排序", "内容", item="4")
    index = SearchIndex(path)
    index.rebuild()

    ranked = index.search("量子计算")
    assert [row.document_id for row in ranked[:2]] == [title_match, content_match]
    assert [row.document_id for row in index.search("稳定排序")] == [tie_a, tie_b]


def test_search_accepts_repository_connection(database):
    path, connection = database
    connection.close()
    with KnowledgeRepository(path) as repository:
        document_id = add_document(repository.connection, "repo", "知识库", "搜索")
        index = SearchIndex(repository)
        index.index_document(document_id)
        assert index.search("搜索")[0].document_id == document_id


def test_search_uses_the_or_token_semantics_accepted_by_the_benchmark(database):
    path, connection = database
    document_id = add_document(
        connection,
        "partial-token-match",
        "机器学习实践",
        "只包含查询中的一个概念",
    )
    index = SearchIndex(path)
    index.rebuild()

    results = index.search("机器 智能")

    assert [result.document_id for result in results] == [document_id]


def test_snippet_windows_around_a_match_late_in_the_source(database):
    path, connection = database
    add_document(
        connection,
        "late-match",
        "长文",
        "前置内容" * 100 + "关键命中词" + "后置内容" * 20,
    )
    index = SearchIndex(path)
    index.rebuild()

    snippet = index.search("关键命中词")[0].snippet

    assert "<mark>关键命中词</mark>" in snippet
    assert len(snippet) < 500


def test_dictionary_fingerprint_hashes_dictionary_contents(database):
    index = SearchIndex(database[0])
    dictionary = index._dictionary_file()
    expected = hashlib.sha256(dictionary.read_bytes()).hexdigest()

    assert index.dictionary_fingerprint == expected


def test_rebuild_rolls_back_when_tokenization_fails(database, monkeypatch):
    path, connection = database
    first = add_document(connection, "first", "原有标题", "原有正文")
    index = SearchIndex(path)
    index.rebuild()
    before = connection.execute(
        "SELECT document_id, title, content FROM documents_search_content ORDER BY document_id"
    ).fetchall()
    connection.execute(
        "UPDATE documents SET title='尚未提交的新标题' WHERE id=?",
        (first,),
    )
    connection.commit()
    add_document(connection, "second", "触发失败", "新正文", item="2")
    original_terms = index._terms

    def failing_terms(text):
        if "触发失败" in text:
            raise RuntimeError("tokenization failed")
        return original_terms(text)

    monkeypatch.setattr(index, "_terms", failing_terms)

    with pytest.raises(RuntimeError, match="tokenization failed"):
        index.rebuild()

    after = connection.execute(
        "SELECT document_id, title, content FROM documents_search_content ORDER BY document_id"
    ).fetchall()
    assert [tuple(row) for row in after] == [tuple(row) for row in before]
    assert index.search("原有正文")[0].document_id == first
