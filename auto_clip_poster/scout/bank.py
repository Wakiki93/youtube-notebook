"""The scout's memory: every candidate it has seen, its score, and what happened to it.

A small SQLite file at data/scout/scout.db. Seeing the same clip again only
updates its views and score, so nothing is ever downloaded twice.

Statuses:
  new         passed the filters, waiting to be downloaded
  skipped     failed a filter (reason says which)
  duplicate   the same moment as a better clip
  downloaded  video is on disk, ready for the next stage
  failed      download failed (reason says why); retried once
  expired     files deleted after SCOUT_KEEP_DAYS
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .sources import Candidate

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
  key TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  url TEXT NOT NULL,
  title TEXT,
  creator TEXT,
  creator_id TEXT,
  published_at TEXT,
  views INTEGER DEFAULT 0,
  score REAL DEFAULT 0,
  status TEXT NOT NULL,
  reason TEXT DEFAULT '',
  attempts INTEGER DEFAULT 0,
  moments TEXT DEFAULT '[]',
  files TEXT DEFAULT '[]',
  data TEXT DEFAULT '{}',
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS by_status_score ON candidates(status, score DESC);
CREATE TABLE IF NOT EXISTS runs (
  at TEXT PRIMARY KEY,
  found INTEGER, kept INTEGER, downloaded INTEGER, problems TEXT
);
"""

# A candidate that reached one of these stays there; a later sighting only refreshes views and score.
STICKY = {"downloaded", "expired"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Bank:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def upsert(self, item: Candidate, score: float, status: str, reason: str = "") -> str:
        """Record a sighting. Returns the status the candidate ends up with."""
        row = self.get(item.key)
        now = _now()
        if row is None:
            self.db.execute(
                "INSERT INTO candidates (key, source, url, title, creator, creator_id, published_at, views, score,"
                " status, reason, data, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (item.key, item.source, item.url, item.title, item.creator, item.creator_id,
                 item.published_at.isoformat() if item.published_at else None, item.views, score,
                 status, reason, json.dumps(item.to_dict(), ensure_ascii=False), now, now))
            self.db.commit()
            return status
        keep = row["status"] in STICKY or (row["status"] == "failed" and status == "new")
        new_status = row["status"] if keep else status
        new_reason = row["reason"] if keep else reason
        self.db.execute("UPDATE candidates SET views=?, score=?, status=?, reason=?, title=?, last_seen=? WHERE key=?",
                        (max(item.views, row["views"]), score, new_status, new_reason, item.title, now, item.key))
        self.db.commit()
        return new_status

    def get(self, key: str) -> Optional[sqlite3.Row]:
        return self.db.execute("SELECT * FROM candidates WHERE key=?", (key,)).fetchone()

    def to_download(self, limit: int) -> list[sqlite3.Row]:
        """The best `limit` waiting candidates, taking turns between Twitch and YouTube.

        Scores only compare well within one source (a YouTube video has far more views
        than a 30-second Twitch clip), so each source gets its fair share of downloads.
        """
        rows = self.db.execute(
            "SELECT * FROM candidates WHERE status='new' OR (status='failed' AND attempts < 2)"
            " ORDER BY score DESC").fetchall()
        lanes: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            lanes.setdefault(row["source"], []).append(row)
        picked: list[sqlite3.Row] = []
        while len(picked) < limit and any(lanes.values()):
            for source in sorted(lanes):
                if lanes[source] and len(picked) < limit:
                    picked.append(lanes[source].pop(0))
        return picked

    def mark_downloaded(self, key: str, files: list[str], moments: list[dict]) -> None:
        self.db.execute("UPDATE candidates SET status='downloaded', files=?, moments=?, reason='',"
                        " attempts=attempts+1 WHERE key=?", (json.dumps(files), json.dumps(moments), key))
        self.db.commit()

    def mark_failed(self, key: str, error: str) -> None:
        self.db.execute("UPDATE candidates SET status='failed', reason=?, attempts=attempts+1 WHERE key=?",
                        (error[:300], key))
        self.db.commit()

    def top(self, limit: int = 20, source: Optional[str] = None,
            statuses: tuple[str, ...] = ("new", "downloaded", "failed")) -> list[sqlite3.Row]:
        marks = ",".join("?" * len(statuses))
        where = f"status IN ({marks})" + (" AND source=?" if source else "")
        args = (*statuses, source) if source else statuses
        return self.db.execute(f"SELECT * FROM candidates WHERE {where} ORDER BY score DESC LIMIT ?",
                               (*args, limit)).fetchall()

    def counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in
                self.db.execute("SELECT status, COUNT(*) AS n FROM candidates GROUP BY status")}

    def skip_reasons(self, limit: int = 8) -> list[tuple[str, int]]:
        rows = self.db.execute("SELECT reason, COUNT(*) AS n FROM candidates WHERE status='skipped'"
                               " GROUP BY reason ORDER BY n DESC LIMIT ?", (limit,))
        return [(r["reason"], r["n"]) for r in rows]

    def expire(self, keep_days: int) -> list[str]:
        """Forget the files of downloads older than keep_days. Returns the file paths to delete."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat(timespec="seconds")
        rows = self.db.execute("SELECT key, files FROM candidates WHERE status='downloaded' AND first_seen < ?",
                               (cutoff,)).fetchall()
        paths = [p for r in rows for p in json.loads(r["files"] or "[]")]
        self.db.executemany("UPDATE candidates SET status='expired' WHERE key=?", [(r["key"],) for r in rows])
        self.db.commit()
        return paths

    def log_run(self, found: int, kept: int, downloaded: int, problems: list[str]) -> None:
        self.db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?)",
                        (_now(), found, kept, downloaded, json.dumps(problems, ensure_ascii=False)))
        self.db.commit()

    def last_run(self) -> Optional[sqlite3.Row]:
        return self.db.execute("SELECT * FROM runs ORDER BY at DESC LIMIT 1").fetchone()
