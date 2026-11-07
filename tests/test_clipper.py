import json
import subprocess

import pytest

from auto_clip_poster import clipper
from auto_clip_poster.clipper import ClipError, build_command, parse_result

from .conftest import autoclip_json


def test_command_targets_instagram_reels(settings):
    cmd = build_command("https://youtu.be/x", settings)
    assert cmd[:2] == ["autoclip", "--data-dir"]
    assert cmd[cmd.index("--platform") + 1] == "instagram_reels"
    assert "--json" in cmd and "produce" in cmd


def test_parse_keeps_rendered_clips(tmp_path):
    clips = parse_result(autoclip_json(tmp_path, n=2), "https://youtu.be/x", credit="@host")
    assert [c.title for c in clips] == ["Title 0", "Title 1"]
    assert clips[0].tags == ["Podcast", "Mindset", "big ideas"]
    assert clips[0].credit == "@host" and clips[0].source_url == "https://youtu.be/x"


def test_parse_tolerates_log_lines_before_json(tmp_path):
    out = "some warning\n" + autoclip_json(tmp_path, n=1)
    assert len(parse_result(out, "u")) == 1


def test_parse_partial_keeps_finished_clips(tmp_path):
    data = json.loads(autoclip_json(tmp_path, n=2))
    data["ok"] = False
    data["status"] = "partial"
    del data["outputs"][1]["video_path"]
    assert len(parse_result(json.dumps(data), "u")) == 1


def test_parse_failure_raises():
    with pytest.raises(ClipError, match="could not clip"):
        parse_result(json.dumps({"ok": False, "error": "no subtitles", "outputs": []}), "u")
    with pytest.raises(ClipError):
        parse_result("", "u")


def test_produce_caps_clips_and_turns_off_telemetry(settings, tmp_path):
    def runner(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=autoclip_json(tmp_path, n=5), stderr="progress...")

    clips = clipper.produce("https://youtu.be/x", settings, runner=runner)
    assert len(clips) == 3  # CLIPS_PER_VIDEO
    privacy = json.loads((settings.autoclip_data_dir / "privacy.json").read_text())
    assert privacy == {"analytics": False, "crash_reports": False}


def test_produce_missing_binary(settings):
    def runner(cmd, **kw):
        raise FileNotFoundError

    with pytest.raises(ClipError, match="Can't find"):
        clipper.produce("u", settings, runner=runner)


def test_produce_bad_input_exit_code(settings):
    def runner(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 2, stdout='{"ok": false, "error_code": "invalid_input"}', stderr="")

    with pytest.raises(ClipError, match="rejected"):
        clipper.produce("not-a-link", settings, runner=runner)
