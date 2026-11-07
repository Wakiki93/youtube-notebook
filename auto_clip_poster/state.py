"""The queue of videos to clip and the record of what was clipped and posted.

- queue.txt is yours to edit: one video link per line, optionally `link | credit`.
  Lines starting with # are ignored. We never rewrite this file except to append.
- state.json is the loop's memory: which links are done, the bank of clips
  waiting to post, and every post sent to Postiz.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .clipper import Clip

MAX_ATTEMPTS = 2


@dataclass
class QueueItem:
    url: str
    credit: str = ""


def read_queue(path: Path) -> list[QueueItem]:
    if not path.exists():
        return []
    items: list[QueueItem] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url, _, credit = line.partition("|")
        url = url.strip()
        if not url.lower().startswith(("http://", "https://", "/")) or url in seen:
            continue
        seen.add(url)
        items.append(QueueItem(url=url, credit=credit.strip()))
    return items


def append_queue(path: Path, url: str, credit: str = "") -> bool:
    """Add a link to queue.txt. Returns False if it's already there."""
    if any(item.url == url for item in read_queue(path)):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if path.exists() and path.read_text(encoding="utf-8") and not path.read_text(encoding="utf-8").endswith("\n"):
        prefix = "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(prefix + (f"{url} | {credit}" if credit else url) + "\n")
    return True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data = {"sources": {}, "bank": [], "posts": []}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
            self.data.update({k: loaded.get(k, v) for k, v in self.data.items()})

    # -- persistence --------------------------------------------------------
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    # -- sources ------------------------------------------------------------
    def next_source(self, queue: list[QueueItem], skip: Optional[set[str]] = None) -> Optional[QueueItem]:
        """New links first, in queue order; then one retry for links that failed before."""
        skip = skip or set()
        for item in queue:
            if item.url not in skip and item.url not in self.data["sources"]:
                return item
        for item in queue:
            record = self.data["sources"].get(item.url) or {}
            if (item.url not in skip and record.get("status") == "failed"
                    and record.get("attempts", 0) < MAX_ATTEMPTS):
                return item
        return None

    def mark_clipped(self, url: str, count: int) -> None:
        self.data["sources"][url] = {"status": "clipped", "clips": count, "at": _now(),
                                     "attempts": self.data["sources"].get(url, {}).get("attempts", 0) + 1}

    def mark_failed(self, url: str, error: str) -> None:
        attempts = self.data["sources"].get(url, {}).get("attempts", 0) + 1
        self.data["sources"][url] = {"status": "failed", "error": error[:500], "at": _now(), "attempts": attempts}

    # -- clip bank ----------------------------------------------------------
    @property
    def bank(self) -> list[dict]:
        return self.data["bank"]

    def add_clips(self, clips: list[Clip]) -> None:
        self.bank.extend(c.to_dict() for c in clips)

    def pop_clip(self) -> Optional[Clip]:
        return Clip.from_dict(self.bank.pop(0)) if self.bank else None

    def return_clip(self, clip: Clip) -> None:
        self.bank.insert(0, clip.to_dict())

    # -- posts --------------------------------------------------------------
    def taken_slots(self) -> set[str]:
        return {p["slot"] for p in self.data["posts"] if p.get("slot")}

    def record_post(self, clip: Clip, slot: datetime, mode: str, response, dry_run: bool) -> None:
        post_id = ""
        if isinstance(response, list) and response:
            post_id = str(response[0].get("postId") or response[0].get("id") or "")
        elif isinstance(response, dict):
            post_id = str(response.get("postId") or response.get("id") or "")
        self.data["posts"].append({
            "slot": slot.isoformat(), "mode": mode, "dry_run": dry_run, "postiz_id": post_id,
            "video": clip.video_path, "title": clip.title, "source": clip.source_url, "at": _now(),
        })
