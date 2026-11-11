"""Downloading with yt-dlp. Only runs when SCOUT_DOWNLOAD=true.

- A Twitch clip is already short, so the whole clip is saved.
- A YouTube video is never saved whole. We read its "Most replayed" graph and
  save only the best SCOUT_CLIP_SECONDS around each peak.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .rank import Moment, moments_from_heatmap

log = logging.getLogger(__name__)

# 1080p at most is plenty for a vertical Reel, and keeps disk use sane.
FORMAT = "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b"


class FetchError(RuntimeError):
    pass


def _yt_dlp():
    try:
        import yt_dlp
    except ImportError as error:
        raise FetchError("yt-dlp is not installed: pip install yt-dlp") from error
    return yt_dlp


def _safe(key: str) -> str:
    return key.replace(":", "_").replace("/", "_")


def fetch(row, clips_dir: Path, clip_seconds: int, moments_per_video: int,
          ytdlp=None) -> tuple[list[str], list[dict]]:
    """Download one candidate. Returns (files, moments). Raises FetchError when nothing usable came down."""
    ytdlp = ytdlp or _yt_dlp()
    clips_dir.mkdir(parents=True, exist_ok=True)
    base = _safe(row["key"])
    common = {"quiet": True, "no_warnings": True, "format": FORMAT, "merge_output_format": "mp4",
              "noplaylist": True, "retries": 3, "socket_timeout": 30}
    try:
        if row["source"] == "twitch":
            target = clips_dir / f"{base}.mp4"
            with ytdlp.YoutubeDL({**common, "outtmpl": str(target)}) as ydl:
                ydl.download([row["url"]])
            return _existing([target]), []

        # YouTube: read the replay graph first, then cut only the best parts while downloading.
        with ytdlp.YoutubeDL({**common, "skip_download": True}) as ydl:
            info = ydl.extract_info(row["url"], download=False)
        moments = moments_from_heatmap(info.get("heatmap") or [], float(info.get("duration") or 0),
                                       clip_seconds, moments_per_video)
        if not moments:
            raise FetchError("no Most-replayed graph yet (YouTube shows it once a video has enough views)")
        files = []
        for n, m in enumerate(moments, 1):
            target = clips_dir / f"{base}_m{n}.mp4"
            opts = {**common, "outtmpl": str(target), "force_keyframes_at_cuts": True,
                    "download_ranges": ytdlp.utils.download_range_func(None, [(m.start, m.end)])}
            with ytdlp.YoutubeDL(opts) as ydl:
                ydl.download([row["url"]])
            files.extend(_existing([target]))
        if not files:
            raise FetchError("yt-dlp finished but no file was written")
        return files, [m.__dict__ for m in moments]
    except FetchError:
        raise
    except Exception as error:  # yt-dlp raises DownloadError and friends
        raise FetchError(str(error).strip().splitlines()[-1][:300] if str(error).strip() else repr(error)) from error


def _existing(paths: list[Path]) -> list[str]:
    return [str(p) for p in paths if p.exists() and p.stat().st_size > 0]


def write_sidecar(files: list[str], row, moments: list[dict]) -> None:
    """Save who made it and where it came from next to each file, for credit in the caption later."""
    for f in files:
        Path(f).with_suffix(".json").write_text(json.dumps({
            "source_url": row["url"], "title": row["title"], "creator": row["creator"],
            "source": row["source"], "score": row["score"], "moments": moments,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
