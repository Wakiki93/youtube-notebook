from datetime import time

import pytest

from auto_clip_poster.captions import IG_CAPTION_LIMIT, build_caption
from auto_clip_poster.clipper import Clip
from auto_clip_poster.config import ConfigError, Settings, parse_times


def test_caption_has_title_credit_footer_and_clean_hashtags():
    clip = Clip(video_path="v.mp4", source_url="u", title="Big idea", description="Why it matters",
                tags=["Podcast", "#podcast", "big ideas", "!!"], credit="@thehost")
    caption = build_caption(clip, footer="Follow for daily clips", max_hashtags=10)
    assert caption.startswith("Big idea\n\nWhy it matters")
    assert "Credit: @thehost" in caption
    assert "Follow for daily clips" in caption
    assert caption.endswith("#Podcast #bigideas")  # deduped, cleaned, empty tag dropped


def test_caption_respects_limits():
    clip = Clip(video_path="v.mp4", source_url="u", title="T", description="x" * 5000,
                tags=[f"tag{i}" for i in range(50)], credit="@c")
    caption = build_caption(clip, max_hashtags=40)
    assert len(caption) <= IG_CAPTION_LIMIT
    assert caption.count("#") == 30
    assert "Credit: @c" in caption


def test_parse_times():
    assert parse_times("18:30, 09:00,09:00") == [time(9, 0), time(18, 30)]
    with pytest.raises(ConfigError):
        parse_times("nine")


def test_defaults_are_safe(tmp_path):
    s = Settings.from_env({"ACP_DATA_DIR": str(tmp_path)})
    assert s.post_mode == "draft"  # nothing goes live until you switch it
    assert s.queue_file == tmp_path / "queue.txt"
    with pytest.raises(ConfigError, match="POSTIZ_API_KEY"):
        s.require_postiz()


def test_bad_mode_rejected(tmp_path):
    with pytest.raises(ConfigError):
        Settings.from_env({"ACP_DATA_DIR": str(tmp_path), "POST_MODE": "yolo"})
