import pytest

from pkb.wiki.renderer import (
    ArticleView,
    CitationView,
    DerivationView,
    IndexEntryView,
    LabelView,
    render_article,
    render_source_index,
    render_tag_index,
    render_topic_index,
    stable_document_id,
)


def article_view(**changes):
    view = dict(
        id="doc-1",
        title="先理解，再练习",
        source="zhihu",
        source_url="https://www.zhihu.com/question/1/answer/2",
        source_content_hash="source-sha256",
        normalized_content_hash="normalized-sha256",
        normalization_version=1,
        derivation_id="derivation-1",
        collections=(
            LabelView(name="字母", normalized_name="zeta"),
            LabelView(name="阿尔法", normalized_name="alpha"),
        ),
        manual_tags=(LabelView(name="手工", normalized_name="manual"),),
        derivation=DerivationView(
            summary="先理解，\n再练习。",
            key_points=("理解原理", "动手练习"),
            topics=(
                LabelView(name="学习法", normalized_name="study"),
                LabelView(name="方法", normalized_name="method"),
            ),
            tags=(
                LabelView(name="练习", normalized_name="practice"),
                LabelView(name="基础", normalized_name="basic"),
            ),
            source_citations=(
                CitationView(claim="练习很重要", excerpt="先理解，再练习。"),
            ),
        ),
    )
    view.update(changes)
    return ArticleView(**view)


def test_article_page_has_traceable_frontmatter_and_sections():
    text = render_article(article_view())

    assert 'id: "doc-1"' in text
    assert 'generated_by: "pkb"' in text
    assert 'source_content_hash: "source-sha256"' in text
    assert 'normalized_content_hash: "normalized-sha256"' in text
    assert 'derivation_id: "derivation-1"' in text
    assert "## Summary\n\n先理解，\n再练习。" in text
    assert "- 理解原理" in text
    assert "练习很重要" in text
    assert "## Source" in text
    assert "https://www.zhihu.com/question/1/answer/2" in text
    assert "See [[../user/doc-1]]" in text


def test_article_sorts_labels_by_normalized_name_and_uses_stable_links():
    text = render_article(article_view())

    assert text.index("[[../collections/alpha|阿尔法]]") < text.index(
        "[[../collections/zeta|字母]]"
    )
    assert text.index("[[../topics/method|方法]]") < text.index(
        "[[../topics/study|学习法]]"
    )
    assert text.index("[[../tags/basic|基础]]") < text.index(
        "[[../tags/manual|手工]]"
    ) < text.index("[[../tags/practice|练习]]")


def test_article_without_derivation_marks_sections_as_not_generated():
    text = render_article(article_view(derivation=None, derivation_id=None))

    assert text.count("尚未生成") >= 4
    assert "理解原理" not in text
    assert "derivation_id: null" in text


def test_renderer_escapes_yaml_markdown_and_wikilink_injection():
    view = article_view(
        title='危险: "标题"\n---\n# 注入<script>',
        source='zhihu"\ngenerated_by: "attacker',
        source_url="https://example.test/a\n---\nevil: true",
        derivation=DerivationView(
            summary="正文\n# 假标题\n---\n[[evil]]",
            key_points=("- 假列表", "[链接](javascript:alert(1))"),
            topics=(LabelView(name="坏|名]]", normalized_name="safe-topic"),),
            tags=(),
            source_citations=(CitationView(claim="> 假引用", excerpt="---"),),
        ),
    )
    text = render_article(view)

    frontmatter = text.split("---", 2)[1]
    assert "\ngenerated_by: \"attacker" not in frontmatter
    assert '\\ngenerated_by: \\"attacker' in frontmatter
    assert "\n\\# 假标题" in text
    assert "\n# 注入" not in text
    assert "<script>" not in text
    assert "\n\\---" in text
    assert "\\[\\[evil\\]\\]" in text
    assert "[[../topics/safe-topic|坏\\|名\\]\\]]]" in text
    assert "[链接](javascript:alert" not in text


@pytest.mark.parametrize("bad_id", ["../secret", "a/b", "a\\b", "x|alias", "x]]"])
def test_article_rejects_unsafe_stable_document_ids(bad_id):
    with pytest.raises(ValueError, match="stable id"):
        render_article(article_view(id=bad_id))


def test_article_rejects_database_row_id_as_document_id():
    with pytest.raises(ValueError, match="stable id"):
        render_article(article_view(id=42))


def test_document_id_is_a_deterministic_safe_projection_of_identity_key():
    first = stable_document_id("zhihu:answer:123")
    assert first == stable_document_id("zhihu:answer:123")
    assert first != stable_document_id("zhihu:answer:124")
    assert first.startswith("document-")
    assert "/" not in first and ":" not in first


def entries():
    return [
        IndexEntryView(id="doc-z", title="字母", normalized_title="zeta"),
        IndexEntryView(id="doc-a", title="阿尔法", normalized_title="alpha"),
    ]


@pytest.mark.parametrize(
    ("renderer", "kind", "name"),
    [
        (render_source_index, "source", "知乎"),
        (render_topic_index, "topic", "学习"),
        (render_tag_index, "tag", "方法"),
    ],
)
def test_index_renderers_are_stable_and_link_articles(renderer, kind, name):
    first = renderer(name, entries())
    second = renderer(name, list(reversed(entries())))

    assert first == second
    assert f'index_kind: "{kind}"' in first
    assert first.index("[[../articles/doc-a|阿尔法]]") < first.index(
        "[[../articles/doc-z|字母]]"
    )
    assert "generated_at" not in first
    assert first.endswith("\n") and not first.endswith("\n\n")


def test_index_same_normalized_title_uses_stable_id_as_tiebreaker():
    tied = [
        IndexEntryView(id="doc-z", title="同名", normalized_title="same"),
        IndexEntryView(id="doc-a", title="同名", normalized_title="same"),
    ]
    text = render_tag_index("相同", tied)
    assert text.index("articles/doc-a") < text.index("articles/doc-z")
