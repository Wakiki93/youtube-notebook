"""The loop: keep a bank of clips filled from the queue, and fill each posting slot with one Reel.

One cycle does three things:
1. Refill: while the clip bank can't cover the next day's slots, clip the next
   link in queue.txt with AutoClip.
2. Plan: work out the next posting slots (POST_TIMES in TIMEZONE) that don't
   already have a post.
3. Post: for each slot, upload one clip to Postiz and create the post
   (draft, schedule or now, per POST_MODE).
"""
from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, time
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from . import clipper as clipper_mod
from .captions import build_caption
from .clipper import Clip, ClipError
from .config import Settings
from .postiz import PostizClient, PostizError
from .state import MAX_ATTEMPTS, State, read_queue

log = logging.getLogger(__name__)

ProduceFn = Callable[[str, Settings, str], list[Clip]]


@dataclass
class CycleReport:
    clipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    posted: list[str] = field(default_factory=list)
    bank_left: int = 0
    queue_left: int = 0
    note: str = ""

    def summary(self) -> str:
        lines = [
            f"Clipped {len(self.clipped)} video(s), {len(self.failed)} failed.",
            f"Sent {len(self.posted)} post(s) to Postiz.",
            f"Clips waiting: {self.bank_left}. Links left in queue: {self.queue_left}.",
        ]
        if self.posted:
            lines += [f"  - {p}" for p in self.posted]
        if self.failed:
            lines += [f"  ! {f}" for f in self.failed]
        if self.note:
            lines.append(self.note)
        return "\n".join(lines)


def next_slots(now: datetime, post_times: list[time], tz: str, count: int, taken: set[str]) -> list[datetime]:
    """The next `count` posting times after `now` that have no post yet."""
    zone = ZoneInfo(tz)
    local_now = now.astimezone(zone)
    slots: list[datetime] = []
    day = local_now.date()
    for _ in range(60):  # look ahead up to 60 days
        for t in post_times:
            slot = datetime.combine(day, t, tzinfo=zone)
            if slot <= local_now + timedelta(minutes=10):  # too soon for Postiz to publish reliably
                continue
            if slot.isoformat() in taken:
                continue
            slots.append(slot)
            if len(slots) >= count:
                return slots
        day += timedelta(days=1)
    return slots


def seconds_until(now: datetime, at: time, tz: str) -> float:
    zone = ZoneInfo(tz)
    local_now = now.astimezone(zone)
    target = datetime.combine(local_now.date(), at, tzinfo=zone)
    if target <= local_now:
        target = datetime.combine(local_now.date() + timedelta(days=1), at, tzinfo=zone)
    return (target - local_now).total_seconds()


class Pipeline:
    def __init__(self, settings: Settings, client: Optional[PostizClient] = None,
                 produce: Optional[ProduceFn] = None, now: Callable[[], datetime] = None):
        self.settings = settings
        self.client = client
        self.produce = produce or (lambda url, s, credit: clipper_mod.produce(url, s, credit))
        self.now = now or (lambda: datetime.now(ZoneInfo(settings.timezone)))
        self._channel: Optional[dict] = None

    # -- helpers ------------------------------------------------------------
    def _client(self) -> PostizClient:
        if self.client is None:
            self.settings.require_postiz()
            self.client = PostizClient(self.settings.postiz_api_url, self.settings.postiz_api_key)
        return self.client

    def channel(self) -> dict:
        if self._channel is None:
            if self.settings.dry_run:
                self._channel = {"id": "dry-run", "identifier": "instagram", "profile": "(dry run)"}
            else:
                self._channel = self._client().instagram_channel(self.settings.postiz_integration_id)
        return self._channel

    def clip_source(self, state: State, url: str, credit: str, report: CycleReport) -> int:
        try:
            clips = self.produce(url, self.settings, credit)
        except ClipError as error:
            log.error("%s", error)
            state.mark_failed(url, str(error))
            report.failed.append(f"{url}: {error}")
            state.save()
            return 0
        state.add_clips(clips)
        state.mark_clipped(url, len(clips))
        state.save()
        report.clipped.append(url)
        log.info("Got %d Reel(s) from %s", len(clips), url)
        return len(clips)

    def post_clip(self, state: State, clip: Clip, slot: datetime, report: CycleReport) -> None:
        s = self.settings
        caption = build_caption(clip, s.caption_footer, s.max_hashtags)
        label = f"{slot:%a %b %d %H:%M} [{s.post_mode}] {clip.title or clip.video_path}"
        if s.dry_run:
            log.info("DRY RUN, would post: %s\n%s", label, caption)
            response = None
        else:
            client = self._client()
            channel = self.channel()
            media = client.wait_until_ready(client.upload(clip.video_path, adjust=s.adjust_media))
            response = client.create_post(channel, caption, media, slot, s.post_mode)
        state.record_post(clip, slot, s.post_mode, response, s.dry_run)
        state.save()
        report.posted.append(label)

    # -- public entry points -------------------------------------------------
    def run_once(self, url: str, credit: str = "", count: int = 1) -> CycleReport:
        """Smallest slice: clip one link and send its best `count` Reel(s) to the next open slot(s)."""
        state = State(self.settings.state_file)
        report = CycleReport()
        self.channel()  # fail fast on Postiz setup before spending 20 minutes clipping
        got = self.clip_source(state, url, credit, report)
        if got:
            # The new clips went to the back of the bank; post these, keep the rest banked.
            new = [state.bank.pop() for _ in range(got)][::-1]
            to_post, keep = new[:count], new[count:]
            state.bank.extend(keep)
            slots = next_slots(self.now(), self.settings.post_times, self.settings.timezone,
                               len(to_post), state.taken_slots())
            for raw, slot in zip(to_post, slots):
                clip = Clip.from_dict(raw)
                try:
                    self.post_clip(state, clip, slot, report)
                except PostizError as error:
                    state.bank.insert(0, raw)
                    state.save()
                    report.failed.append(f"posting {clip.title}: {error}")
                    break
        state.save()
        report.bank_left = len(state.bank)
        return report

    def run_cycle(self) -> CycleReport:
        s = self.settings
        state = State(s.state_file)
        report = CycleReport()
        per_day = len(s.post_times)

        # 1. Refill the bank so the next day of slots is covered.
        tried: set[str] = set()  # a link that fails waits for the next day before its retry
        while len(state.bank) < per_day:
            item = state.next_source(read_queue(s.queue_file), skip=tried)
            if item is None:
                break
            tried.add(item.url)
            self.clip_source(state, item.url, item.credit, report)

        # 2 + 3. Fill the open slots for roughly the next day.
        slots = next_slots(self.now(), s.post_times, s.timezone, per_day, state.taken_slots())
        for slot in slots:
            clip = state.pop_clip()
            if clip is None:
                report.note = "Out of clips. Add more links to queue.txt (acp add <link>)."
                break
            try:
                self.post_clip(state, clip, slot, report)
            except (PostizError, OSError) as error:
                state.return_clip(clip)
                state.save()
                report.failed.append(f"posting {clip.title or clip.video_path}: {error}")
                break

        state.save()
        report.bank_left = len(state.bank)
        queue = read_queue(s.queue_file)
        report.queue_left = sum(1 for q in queue if q.url not in state.data["sources"]
                                or (state.data["sources"][q.url].get("status") == "failed"
                                    and state.data["sources"][q.url].get("attempts", 0) < MAX_ATTEMPTS))
        return report

    def loop(self, sleep: Callable[[float], None] = _time.sleep, max_cycles: Optional[int] = None) -> None:
        """Run a cycle now, then once a day at RUN_AT, forever."""
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            try:
                log.info("Cycle starting\n%s", self.run_cycle().summary())
            except Exception:  # keep the daily loop alive; the next cycle retries
                log.exception("Cycle failed; will retry at the next run time")
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            wait = seconds_until(self.now(), self.settings.run_at, self.settings.timezone)
            log.info("Next cycle in %.1f hours", wait / 3600)
            sleep(wait)
