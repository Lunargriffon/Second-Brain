from pkb.douyin.eligibility import (
    Eligibility,
    RuleBasedKnowledgeValueClassifier,
    VideoMetadata,
)


def metadata(
    caption: str = "",
    *,
    hashtags: tuple[str, ...] = (),
    author: str = "",
) -> VideoMetadata:
    return VideoMetadata(caption=caption, hashtags=hashtags, author=author)


def classifier() -> RuleBasedKnowledgeValueClassifier:
    return RuleBasedKnowledgeValueClassifier()


def test_tutorial_metadata_is_kept():
    decision = classifier().classify(metadata("Python 自动化教程"))

    assert decision.eligibility is Eligibility.KEEP
    assert "knowledge_tutorial" in decision.reasons


def test_analysis_and_experience_metadata_are_kept():
    for caption in ("拆解一个商业案例", "我的项目复盘与经验总结"):
        assert classifier().classify(metadata(caption)).eligibility is Eligibility.KEEP


def test_appreciation_and_travel_metadata_are_excluded():
    expected = {
        "氛围感美女写真": "appreciation_beauty",
        "治愈风景壁纸": "appreciation_scenery",
        "三亚旅游攻略": "lifestyle_travel",
        "王者荣耀五杀高光": "entertainment_gaming",
    }
    for caption, reason in expected.items():
        decision = classifier().classify(metadata(caption))
        assert decision.eligibility is Eligibility.EXCLUDE
        assert reason in decision.reasons


def test_ambiguous_and_empty_metadata_fail_closed():
    assert classifier().classify(metadata("今天也要开心")).eligibility is Eligibility.EXCLUDE
    assert classifier().classify(metadata()).reasons == ("insufficient_knowledge_signal",)


def test_specific_knowledge_overrides_a_generic_negative_word():
    decision = classifier().classify(metadata("旅行摄影参数设置教程与后期调色方法"))

    assert decision.eligibility is Eligibility.KEEP
    assert "knowledge_tutorial" in decision.reasons
    assert "knowledge_method" in decision.reasons


def test_strong_appreciation_exclusion_wins_over_one_generic_knowledge_word():
    decision = classifier().classify(metadata("美女写真拍摄分享"))

    assert decision.eligibility is Eligibility.EXCLUDE
    assert "appreciation_beauty" in decision.reasons


def test_unicode_normalization_and_hashtags_are_classified():
    decision = classifier().classify(metadata(hashtags=("ＰＹＴＨＯＮ教程",)))

    assert decision.eligibility is Eligibility.KEEP


def test_author_alone_never_proves_knowledge_value():
    decision = classifier().classify(metadata(author="商业知识研究所"))

    assert decision.eligibility is Eligibility.EXCLUDE


def test_input_hash_is_stable_and_changes_with_video_metadata():
    first = classifier().classify(metadata("Python 教程", hashtags=("编程",)))
    same = classifier().classify(metadata("Ｐｙｔｈｏｎ  教程", hashtags=("编程",)))
    changed = classifier().classify(metadata("Python 教程", hashtags=("数据库",)))

    assert first.input_hash == same.input_hash
    assert first.input_hash != changed.input_hash
    assert first.classifier_version == classifier().version


def test_classifier_input_has_no_collection_or_folder_field():
    assert set(VideoMetadata.__dataclass_fields__) == {"caption", "hashtags", "author"}
