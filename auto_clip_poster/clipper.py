"""Step 1: turn one long video into Instagram Reels with the AutoClip CLI.

We call the official `autoclip produce` command (installed from the
`autoclip-mcp` package) instead of copying AutoClip's code, so upgrades are a
version bump and the two projects stay separate.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .config import Settings

log = logging.getLogger(__name__)

PLATFORM = "instagram_reels"

Runner = Callable[..., subprocess.CompletedProcess]


class ClipError(RuntimeError):
    """AutoClip failed or returned nothing we can post."""


@dataclass
class Clip:
    video_path: str
    source_url: str
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    cover_path: Optional[str] = None
    credit: str = ""
    project_id: str = ""
    clip_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Clip":
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)


def disable_autoclip_telemetry(data_dir: Path) -> None:
    """AutoClip sends anonymous usage stats unless privacy.json says no."""
    data_dir.mkdir(parents=True, exist_ok=True)
    privacy = data_dir / "privacy.json"
    if not privacy.exists():
        privacy.write_text(json.dumps({"analytics": False, "crash_reports": False}), encoding="utf-8")


def build_command(source: str, settings: Settings) -> list[str]:
    cmd = [
        settings.autoclip_bin,
        "--data-dir", str(settings.autoclip_data_dir),
        "produce", source,
        "--platform", PLATFORM,
        "--json",
        "--timeout", str(settings.autoclip_timeout),
    ]
    if settings.autoclip_template:
        cmd += ["--template", settings.autoclip_template]
    if settings.autoclip_instruction:
        cmd += ["--instruction", settings.autoclip_instruction]
    return cmd


def parse_result(stdout: str, source: str, credit: str = "") -> list[Clip]:
    """Read `autoclip produce --json` output and keep the clips that rendered."""
    text = stdout.strip()
    if not text:
        raise ClipError("AutoClip printed nothing. Run `autoclip doctor` to check its setup.")
    # stdout should be pure JSON, but tolerate stray lines before it.
    start = text.find("{")
    try:
        data = json.loads(text[start:] if start > 0 else text)
    except json.JSONDecodeError as error:
        raise ClipError(f"AutoClip output was not JSON: {text[:300]}") from error

    if not data.get("ok", False) and not any(o.get("video_path") for o in data.get("outputs") or []):
        reason = data.get("error") or data.get("error_code") or data.get("status") or "unknown error"
        raise ClipError(f"AutoClip could not clip {source}: {reason}")

    clips: list[Clip] = []
    for row in data.get("outputs") or []:
        video = row.get("video_path")
        if not video:
            continue
        post = row.get("post") or {}
        result = row.get("result") or {}
        tags = [str(t).lstrip("#").strip() for t in (post.get("tags") or []) if str(t).strip()]
        clips.append(Clip(
            video_path=video,
            source_url=source,
            title=(post.get("title") or result.get("title") or "").strip(),
            description=(post.get("description") or "").strip(),
            tags=tags,
            cover_path=row.get("cover_path"),
            credit=credit,
            project_id=str(data.get("project_id") or ""),
            clip_id=str(row.get("id") or ""),
        ))
    if not clips:
        raise ClipError(f"AutoClip finished but made no usable Reels from {source}")
    return clips


def produce(source: str, settings: Settings, credit: str = "", runner: Runner = subprocess.run) -> list[Clip]:
    """Clip one video. Returns at most CLIPS_PER_VIDEO clips, best first."""
    disable_autoclip_telemetry(settings.autoclip_data_dir)
    cmd = build_command(source, settings)
    log.info("Clipping %s (this can take 10 to 30 minutes)", source)
    try:
        done = runner(cmd, capture_output=True, text=True, timeout=settings.autoclip_timeout + 600)
    except FileNotFoundError as error:
        raise ClipError(f"Can't find the AutoClip command {settings.autoclip_bin!r}. Is autoclip-mcp installed?") from error
    except subprocess.TimeoutExpired as error:
        raise ClipError(f"AutoClip took longer than {settings.autoclip_timeout}s on {source}") from error
    if done.stderr:
        log.debug("autoclip stderr: %s", done.stderr[-2000:])
    if done.returncode not in (0, 1):
        raise ClipError(f"AutoClip rejected the input (exit {done.returncode}): {(done.stdout or done.stderr)[:300]}")
    clips = parse_result(done.stdout, source, credit)
    for clip in clips:
        if not Path(clip.video_path).is_file():
            log.warning("AutoClip listed %s but the file is missing", clip.video_path)
    clips = [c for c in clips if Path(c.video_path).is_file()]
    if not clips:
        raise ClipError(f"AutoClip's Reels for {source} are missing on disk")
    return clips[: settings.clips_per_video]
