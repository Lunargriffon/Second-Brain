from pathlib import Path

from pkb.knowledge.models import NormalizedDocument, SourceMembership


def test_normalized_document_separates_identity_from_membership():
    membership = SourceMembership(
        source="zhihu",
        source_item_id="answer:1",
        collection_id="10",
        source_url="https://www.zhihu.com/question/2/answer/1",
        raw_path=Path("data/raw/zhihu-10.jsonl"),
        raw_line=3,
    )
    document = NormalizedDocument(
        identity_key="zhihu:answer:1",
        canonical_url=membership.source_url,
        title="标题",
        author="作者",
        plain_content="正文",
        media_urls=("https://pic.zhimg.com/a.jpg",),
        source_created_at="2025-01-01T00:00:00Z",
        membership=membership,
    )
    assert document.membership.collection_id == "10"
    assert document.identity_key == "zhihu:answer:1"
