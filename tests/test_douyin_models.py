import pytest

from pkb.douyin.eligibility import Eligibility, EligibilityDecision
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


def test_favorite_item_round_trips_eligibility_decision():
    decision = EligibilityDecision(
        eligibility=Eligibility.KEEP,
        reasons=("knowledge_tutorial",),
        classifier_version="rules-v1",
        input_hash="abc",
    )

    classified = item().with_eligibility(decision)

    assert FavoriteItem.from_dict(classified.to_dict()) == classified
    assert classified.stage is Stage.DISCOVERED


def test_favorite_item_rejects_partial_eligibility_state():
    values = item().to_dict()
    values["eligibility"] = "keep"

    with pytest.raises(ValueError, match="classification fields"):
        FavoriteItem.from_dict(values)


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
        transcript_text="这是口述知识",
        transcript_segments=(TranscriptSegment(0.0, 4.0, "这是口述知识"),),
        transcription_engine="sensevoice",
        transcription_model="SenseVoiceSmall",
        language="zh",
        source_duration_seconds=4.0,
        content_fingerprint="sha256:abc",
        status="persisted",
    )

    serialized = record.to_dict()

    assert DouyinRawRecord.from_dict(serialized) == record
    assert not any("path" in key or "media" in key for key in serialized)
    assert set(serialized) == {
        "work_id",
        "url",
        "author_id",
        "author",
        "caption",
        "hashtags",
        "published_at",
        "observed_at",
        "transcript_text",
        "transcript_segments",
        "transcription_engine",
        "transcription_model",
        "language",
        "source_duration_seconds",
        "content_fingerprint",
        "status",
    }


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
            transcript_text="bad order",
            transcript_segments=(
                TranscriptSegment(5.0, 6.0, "later"),
                TranscriptSegment(1.0, 2.0, "earlier"),
            ),
            transcription_engine="sensevoice",
            transcription_model="SenseVoiceSmall",
            language="zh",
            source_duration_seconds=6.0,
            content_fingerprint="sha256:abc",
            status="persisted",
        )


@pytest.mark.parametrize(
    ("duration", "fingerprint", "status", "message"),
    [
        (-1.0, "sha256:abc", "persisted", "source_duration_seconds"),
        (1.0, "", "persisted", "content_fingerprint"),
        (1.0, "sha256:abc", "", "status"),
    ],
)
def test_raw_record_validates_persistence_metadata(duration, fingerprint, status, message):
    with pytest.raises(ValueError, match=message):
        DouyinRawRecord(
            work_id="7",
            url="https://www.douyin.com/video/7",
            author_id="u1",
            author="作者",
            caption="内容",
            hashtags=(),
            published_at=None,
            observed_at="2026-07-18T00:00:00Z",
            transcript_text="知识",
            transcript_segments=(TranscriptSegment(0.0, 1.0, "知识"),),
            transcription_engine="sensevoice",
            transcription_model="SenseVoiceSmall",
            language="zh",
            source_duration_seconds=duration,
            content_fingerprint=fingerprint,
            status=status,
        )
