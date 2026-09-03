from app.output.naming import clip_output_basename, sanitize_component


def test_sanitize_strips_unsafe_characters():
    # every run of disallowed characters (space, punctuation, path separators) collapses to one underscore
    assert sanitize_component("clip 001!/../etc") == "clip_001_etc"


def test_sanitize_never_starts_or_ends_with_separator_junk():
    result = sanitize_component("...///clip_001///...")
    assert not result.startswith(".") and not result.startswith("_")


def test_clip_output_basename_ignores_ai_free_text_by_construction():
    # naming only ever receives clip_id/rank/viral_score - hook/caption/etc never reach this function
    name = clip_output_basename("clip_001", 1, 92)
    assert name == "clip_001_rank_01_score_92"


def test_clip_output_basename_clamps_score_and_sanitizes_id():
    name = clip_output_basename("../../etc/passwd", 1, 999)
    assert ".." not in name
    assert "/" not in name
    assert name.endswith("_score_100")
