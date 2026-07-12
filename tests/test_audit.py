from pkb.audit import AuditResult, audit_zhihu_collection


class AuditHttp:
    def get_json(self, url: str, headers: dict[str, str]) -> dict:
        if "offset=0" in url:
            return {
                "data": [
                    {"content": {"type": "answer", "id": "1"}},
                    {"content": {"type": "article", "id": "2"}},
                ],
                "paging": {"totals": 3, "is_end": False},
            }
        if "offset=2" in url:
            return {
                "data": [
                    {"content": {"type": "pin", "id": "3"}},
                ],
                "paging": {"totals": 3, "is_end": True},
            }
        raise AssertionError(f"Unexpected URL: {url}")


def test_audit_zhihu_collection_counts_items_and_types():
    result = audit_zhihu_collection(
        collection_url="https://www.zhihu.com/collection/1",
        cookie="cookie",
        http=AuditHttp(),
        request_delay=0,
    )

    assert result == AuditResult(
        collection_id="1",
        reported_total=3,
        scanned_total=3,
        type_counts={"answer": 1, "article": 1, "pin": 1},
        page_count=2,
        warnings=[],
    )


def test_audit_zhihu_collection_warns_when_reported_total_does_not_match_scan():
    class ShortHttp:
        def get_json(self, url: str, headers: dict[str, str]) -> dict:
            return {
                "data": [{"content": {"type": "answer", "id": "1"}}],
                "paging": {"totals": 5, "is_end": True},
            }

    result = audit_zhihu_collection(
        collection_url="https://www.zhihu.com/collection/1",
        cookie="cookie",
        http=ShortHttp(),
        request_delay=0,
    )

    assert result.reported_total == 5
    assert result.scanned_total == 1
    assert result.warnings == ["reported_total_mismatch"]


def test_audit_result_serializes_to_dict():
    result = AuditResult(
        collection_id="575638886",
        reported_total=134,
        scanned_total=134,
        type_counts={"answer": 125, "article": 9},
        page_count=7,
        warnings=[],
    )

    assert result.to_dict() == {
        "collection_id": "575638886",
        "reported_total": 134,
        "scanned_total": 134,
        "type_counts": {"answer": 125, "article": 9},
        "page_count": 7,
        "warnings": [],
    }
