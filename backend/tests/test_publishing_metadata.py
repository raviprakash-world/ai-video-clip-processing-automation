"""PublishMetadata adapter (section 12/13/15): title fallback chain and the
caption+hashtags combination used by Instagram/Facebook."""
from app.publishing.metadata import PublishMetadata, build_publish_metadata
from app.schemas.validated import ValidatedClip


def _clip(**overrides) -> ValidatedClip:
    base = dict(
        clip_id="c1", rank=1, start_time="00:00:00", end_time="00:00:05",
        start_seconds=0, end_seconds=5, duration_seconds=5, ai_reported_duration_seconds=5,
        duration_mismatch=False, viral_score=80, category="cat", speaker="s",
        hook="the hook", title_options={}, caption="the caption", hashtags=["#a", "#b"],
        reason="r", payoff="p", context_warning=None, copyright_warning=None,
    )
    base.update(overrides)
    return ValidatedClip(**base)


def test_title_prefers_curiosity():
    clip = _clip(title_options={"curiosity": "Curiosity title", "direct": "Direct title"})
    meta = build_publish_metadata(clip)
    assert meta.title == "Curiosity title"


def test_title_falls_back_to_direct():
    clip = _clip(title_options={"direct": "Direct title"})
    meta = build_publish_metadata(clip)
    assert meta.title == "Direct title"


def test_title_falls_back_to_hook():
    clip = _clip(title_options={}, hook="the hook")
    meta = build_publish_metadata(clip)
    assert meta.title == "the hook"


def test_hashtags_come_only_from_json_never_invented():
    clip = _clip(hashtags=["#one", "#two"])
    meta = build_publish_metadata(clip)
    assert meta.hashtags == ["#one", "#two"]


def test_combined_caption_joins_body_and_hashtags():
    meta = PublishMetadata(title="t", caption="Body text", hashtags=["#a", "#b"])
    assert meta.combined_caption() == "Body text\n\n#a #b"


def test_combined_caption_with_no_hashtags():
    meta = PublishMetadata(title="t", caption="Body text", hashtags=[])
    assert meta.combined_caption() == "Body text"


def test_combined_caption_with_no_caption():
    meta = PublishMetadata(title="t", caption=None, hashtags=["#a"])
    assert meta.combined_caption() == "#a"
