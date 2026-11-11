"""Filtering and scoring: which candidates are worth clipping, best first.

The score is plain on purpose, so you can read why something ranked high:
  score = 20 x log10(1 + views) + 20 x log10(1 + views per hour)
Views say people liked it; views per hour say it's hot right now. A clip with
1,000 views in its first hour beats one with 5,000 views over two days.
Scores compare well within one source only: a whole YouTube video has far more
views than a 30-second Twitch clip, so downloads take turns between sources.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from .sources import Candidate


def _phrase_re(words: Iterable[str]) -> Optional[re.Pattern]:
    words = [w.strip().lower() for w in words if w.strip()]
    if not words:
        return None
    # Whole words only, so "gta v" doesn't match "gta vi" and "ending" doesn't match "trending".
    parts = sorted((re.escape(w).replace(r"\ ", r"\s+") for w in words), key=len, reverse=True)
    return re.compile(r"(?<![\w])(?:" + "|".join(parts) + r")(?![\w])", re.IGNORECASE)


def score(item: Candidate, now: datetime) -> float:
    views = max(item.views, 0)
    per_hour = views / item.age_hours(now)
    return round(20 * math.log10(1 + views) + 20 * math.log10(1 + per_hour), 1)


@dataclass
class Verdict:
    keep: bool
    reason: str = ""


class Rules:
    def __init__(self, settings, blocklist: set[str], now: Optional[datetime] = None):
        self.s = settings
        self.now = now or datetime.now(timezone.utc)
        self.blocklist = blocklist
        self.topic = _phrase_re(settings.keywords)
        self.skip = _phrase_re(settings.skip_words)

    def check(self, item: Candidate) -> Verdict:
        creator_keys = {item.creator.lower(), item.creator.lower().lstrip("@"), item.creator_id.lower()}
        if creator_keys & self.blocklist:
            return Verdict(False, "creator is on your blocklist")
        if self.skip and (hit := self.skip.search(item.title)):
            return Verdict(False, f"title says '{hit.group(0)}' (leak or spoiler risk)")
        if self.s.not_before and item.published_at and item.published_at < self.s.not_before:
            return Verdict(False, "made before the game unlocked (pre-release footage gets taken down)")
        if item.published_at and item.published_at < self.now - timedelta(hours=self.s.lookback_hours):
            return Verdict(False, f"older than {self.s.lookback_hours} hours")
        if item.source == "twitch":
            # Twitch clips are already filtered by game category, so no title keyword is needed.
            if self.s.languages and item.language and item.language not in self.s.languages:
                return Verdict(False, f"language is {item.language}")
        elif self.topic and not self.topic.search(item.title):
            return Verdict(False, "title isn't about the game")
        if item.views < self.s.min_views:
            return Verdict(False, f"only {item.views} views so far")
        return Verdict(True)


def drop_overlaps(items: list[Candidate], window_seconds: int = 30) -> tuple[list[Candidate], list[Candidate]]:
    """Viewers often clip the same moment several times. Keep the most-viewed one.

    Two Twitch clips are the same moment when they come from the same stream (video_id)
    and start within `window_seconds` of each other.
    """
    kept: list[Candidate] = []
    dropped: list[Candidate] = []
    for item in sorted(items, key=lambda c: c.views, reverse=True):
        twin = None
        if item.source == "twitch" and item.video_id and item.vod_offset is not None:
            twin = next((k for k in kept if k.source == "twitch" and k.video_id == item.video_id
                         and k.vod_offset is not None and abs(k.vod_offset - item.vod_offset) <= window_seconds), None)
        if twin:
            item.extra["duplicate_of"] = twin.key
            dropped.append(item)
        else:
            kept.append(item)
    return kept, dropped


@dataclass
class Moment:
    start: float
    end: float
    peak: float  # 0 to 1, how hard viewers rewatched this part


def moments_from_heatmap(heatmap: list[dict], duration: float, clip_seconds: int = 45,
                         count: int = 2, skip_intro: float = 15.0) -> list[Moment]:
    """Turn YouTube's "Most replayed" graph into the best `count` clip windows.

    yt-dlp gives the graph as a list of {start_time, end_time, value} with value 0 to 1.
    We take the highest points (ignoring the intro and the last few seconds, where
    replays are mostly people skipping), centre a window on each, and never overlap.
    """
    points = [p for p in heatmap or [] if p.get("value") is not None]
    if not points:
        return []
    duration = duration or max(float(p.get("end_time") or 0) for p in points)
    half = clip_seconds / 2
    chosen: list[Moment] = []
    for p in sorted(points, key=lambda p: p["value"], reverse=True):
        mid = (float(p["start_time"]) + float(p["end_time"])) / 2
        if mid < skip_intro or mid > duration - 5:
            continue
        start = max(0.0, mid - half)
        end = min(duration, start + clip_seconds)
        start = max(0.0, end - clip_seconds)
        if any(start < m.end and end > m.start for m in chosen):
            continue
        chosen.append(Moment(round(start, 1), round(end, 1), round(float(p["value"]), 3)))
        if len(chosen) >= count:
            break
    return chosen
