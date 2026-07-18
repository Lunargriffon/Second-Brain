import pytest

from pkb.douyin.models import DouyinRawRecord, FavoriteItem, Stage, TranscriptSegment


def item() -> FavoriteItem:
    return FavoriteItem(
        work_id="7",
        url="https://www.douyin.com/video/7",
        author_id="u1",
        author="作者",
        caption="内容",
        hashtags=("知识",),
        published_at=None,
        observed_at="2026-07-18T00:00:00Z",
        stage=Stage.DISCOVERED,
    )


def test_stage_transition_is_explicit_and_serializable():
    acquired = item().transition(Stage.ACQUIRED)
    assert acquired.to_dict()["stage"] == "acquired"
    assert FavoriteItem.from_dict(acquired.to_dict()) == acquired


def test_illegal_transition_is_rejected():
    with pytest.raises(ValueError, match="discovered -> indexed"):
        item().transition(Stage.INDEXED)


def test_segment_rejects_reverse_time():
    with pytest.raises(ValueError, match="end"):
        TranscriptSegment(start=2.0, end=1.0, text="bad")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"work_id": ""}, "work_id"),
        ({"url": "http://www.douyin.com/video/7"}, "HTTPS"),
        ({"observed_at": "yesterday"}, "observed_at"),
        ({"published_at": "2026-99-99"}, "published_at"),
    ],
)
def test_favorite_item_validates_identity_url_and_timestamps(changes, message):
    values = item().to_dict()
    values.update(changes)
    with pytest.raises(ValueError, match=message):
        FavoriteItem.from_dict(values)


def test_raw_record_round_trips_without_local_media_paths():
    record = DouyinRawRecord(
        work_id="7",
        url="https://www.douyin.com/video/7",
        author_id="u1",
        author="作者",
        caption="内容",
        hashtags=("知识",),
        published_at=None,
        observed_at="2026-07-18T00:00:00Z",
        transcript="这是口述知识",
        segments=(TranscriptSegment(0.0, 4.0, "这是口述知识"),),
        engine="sensevoice",
        model="SenseVoiceSmall",
        language="zh",
    )

    serialized = record.to_dict()

    assert DouyinRawRecord.from_dict(serialized) == record
    assert not any("path" in key or "media" in key for key in serialized)


def test_raw_record_rejects_out_of_order_segments():
    with pytest.raises(ValueError, match="segment order"):
        DouyinRawRecord(
            work_id="7",
            url="https://www.douyin.com/video/7",
            author_id="u1",
            author="作者",
            caption="内容",
            hashtags=(),
            published_at=None,
            observed_at="2026-07-18T00:00:00Z",
            transcript="bad order",
            segments=(
                TranscriptSegment(5.0, 6.0, "later"),
                TranscriptSegment(1.0, 2.0, "earlier"),
            ),
            engine="sensevoice",
            model="SenseVoiceSmall",
            language="zh",
        )
