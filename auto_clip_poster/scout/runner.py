"""One scout pass, and the loop that repeats it.

A pass: read Twitch and the YouTube watchlist, filter, drop repeats, score,
record everything in the bank, then (only if SCOUT_DOWNLOAD=true) download the
best few. It never uploads or posts anything.
"""
from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .bank import Bank
from .config import ScoutSettings
from .fetch import FetchError, fetch, write_sidecar
from .rank import Rules, drop_overlaps, score
from .sources import (Candidate, SourceError, TwitchSource, YouTubeWatchlist, collect, iter_unique,
                      read_blocklist)

log = logging.getLogger(__name__)


@dataclass
class PassReport:
    found: int = 0
    kept: int = 0
    skipped: int = 0
    duplicates: int = 0
    downloaded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Scout pass: {self.found} found, {self.kept} worth clipping, "
                 f"{self.skipped} skipped, {self.duplicates} repeats of the same moment."]
        if self.downloaded:
            lines.append(f"Downloaded {len(self.downloaded)} clip file(s).")
        for f in self.failed:
            lines.append(f"Download failed: {f}")
        for p in self.problems:
            lines.append(f"Problem: {p}")
        lines.append("Nothing was posted.")
        return "\n".join(lines)


class Scout:
    def __init__(self, settings: ScoutSettings, twitch: Optional[TwitchSource] = None,
                 youtube: Optional[YouTubeWatchlist] = None, ytdlp=None, clock=None):
        self.s = settings
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.ytdlp = ytdlp
        problems = []
        if twitch is None and settings.has_twitch:
            twitch = TwitchSource(settings.twitch_client_id, settings.twitch_client_secret)
        elif twitch is None:
            problems.append("Twitch is off: TWITCH_CLIENT_ID/SECRET are empty (YouTube watchlist still runs)")
        self.twitch = twitch
        self.youtube = youtube if youtube is not None else YouTubeWatchlist(ytdlp=ytdlp)
        self.setup_problems = problems

    def run_once(self, download: Optional[bool] = None, candidates: Optional[list[Candidate]] = None) -> PassReport:
        """One pass. `candidates` lets a test (or a replay) skip the network."""
        now = self.clock()
        report = PassReport(problems=list(self.setup_problems))
        if candidates is None:
            found, problems = collect(self.s, self.twitch, self.youtube, now)
            report.problems.extend(problems)
        else:
            found = candidates
        found = iter_unique(found)
        report.found = len(found)

        rules = Rules(self.s, read_blocklist(self.s.blocklist_file), now)
        bank = Bank(self.s.db_file)
        try:
            passed: list[Candidate] = []
            for item in found:
                verdict = rules.check(item)
                if verdict.keep:
                    passed.append(item)
                else:
                    bank.upsert(item, score(item, now), "skipped", verdict.reason)
                    report.skipped += 1
            kept, dupes = drop_overlaps(passed)
            for item in dupes:
                bank.upsert(item, score(item, now), "duplicate", f"same moment as {item.extra['duplicate_of']}")
            report.duplicates = len(dupes)
            for item in kept:
                bank.upsert(item, score(item, now), "new")
            report.kept = len(kept)

            if self.s.download if download is None else download:
                self._download(bank, report)
            for path in bank.expire(self.s.keep_days):
                for f in (Path(path), Path(path).with_suffix(".json")):
                    f.unlink(missing_ok=True)
            bank.log_run(report.found, report.kept, len(report.downloaded), report.problems)
        finally:
            bank.close()
        return report

    def _download(self, bank: Bank, report: PassReport) -> None:
        for row in bank.to_download(self.s.downloads_per_run):
            try:
                files, moments = fetch(row, self.s.clips_dir, self.s.clip_seconds, self.s.moments_per_video,
                                       ytdlp=self.ytdlp)
            except FetchError as error:
                bank.mark_failed(row["key"], str(error))
                report.failed.append(f"{row['title'][:60]} ({error})")
                continue
            write_sidecar(files, row, moments)
            bank.mark_downloaded(row["key"], files, moments)
            report.downloaded.extend(files)

    def loop(self, sleep=_time.sleep) -> None:  # pragma: no cover - runs forever
        log.info("Scout running every %s minutes (profile %s). It never posts.",
                 self.s.interval_minutes, self.s.profile)
        while True:
            try:
                log.info(self.run_once().summary().replace("\n", " | "))
            except Exception:
                log.exception("Scout pass crashed; trying again next cycle")
            sleep(self.s.interval_minutes * 60)


def report_text(settings: ScoutSettings, top: int = 15) -> str:
    """A plain-English look at the bank: what's ranked best and why things were skipped."""
    bank = Bank(settings.db_file)
    try:
        counts = bank.counts()
        last = bank.last_run()
        lines = [f"Profile: {settings.profile} | games: {', '.join(settings.games)}"]
        if last:
            lines.append(f"Last pass {last['at']}: {last['found']} found, {last['kept']} worth clipping, "
                         f"{last['downloaded']} downloaded.")
        lines.append("In the bank: " + (", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "nothing yet"))
        for source, label in (("twitch", "Twitch clips"), ("youtube", "YouTube videos (clips come from their Most-replayed peaks)")):
            rows = bank.top(top, source)
            if not rows:
                continue
            lines.append("")
            lines.append(f"Top {label}:")
            for i, r in enumerate(rows, 1):
                lines.append(f"{i:>2}. [{r['score']:>5.1f}] {r['title'][:70]}  ({r['creator']}, {r['views']:,} views)"
                             + ("" if r["status"] == "new" else f" [{r['status']}]"))
                lines.append(f"     {r['url']}")
        reasons = bank.skip_reasons()
        if reasons:
            lines.append("")
            lines.append("Skipped, and why:")
            lines.extend(f"  {n:>3} x {reason}" for reason, n in reasons)
        return "\n".join(lines)
    finally:
        bank.close()


def check_text(settings: ScoutSettings) -> tuple[bool, str]:
    """Confirm the scout's pieces work, without downloading or posting anything."""
    ok = True
    lines = [f"Scout profile: {settings.profile} | games: {', '.join(settings.games)}"]
    if settings.not_before:
        lines.append(f"Ignores anything made before {settings.not_before.isoformat()} (game unlock)")
    if settings.has_twitch:
        try:
            twitch = TwitchSource(settings.twitch_client_id, settings.twitch_client_secret)
            for game in settings.games:
                lines.append(f"[ OK ] Twitch: '{game}' is category {twitch.game_id(game)}")
        except (SourceError, Exception) as error:
            ok = False
            lines.append(f"[FAIL] Twitch: {error}")
    else:
        lines.append("[SKIP] Twitch: no keys yet (make a free app at dev.twitch.tv/console)")
    try:
        import yt_dlp
        lines.append(f"[ OK ] yt-dlp {yt_dlp.version.__version__}")
    except ImportError:
        ok = False
        lines.append("[FAIL] yt-dlp is not installed")
    from .sources import read_creators
    creators = read_creators(settings.creators_file)
    lines.append(f"[INFO] {len(creators)} YouTube channel(s) in {settings.creators_file}")
    lines.append(f"[INFO] Downloads are {'ON' if settings.download else 'OFF (SCOUT_DOWNLOAD=false)'}")
    return ok, "\n".join(lines)
