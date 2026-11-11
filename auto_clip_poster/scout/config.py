"""Scout settings, read from environment variables (the same .env file as the poster).

Every setting has a default that works for GTA 6 launch week. To rehearse on
GTA V before launch, set SCOUT_PROFILE=gta5 and nothing else.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Mapping, Optional

from ..config import ConfigError, _bool, _int

# GTA 6 unlocks at 11 PM Central on Nov 18, 2026 (midnight Eastern, Nov 19).
GTA6_UNLOCK = "2026-11-18T23:00:00-06:00"

PROFILES = {
    "gta6": {
        "games": ["Grand Theft Auto VI"],
        "keywords": ["gta 6", "gta vi", "gta6", "gtavi", "grand theft auto vi", "grand theft auto 6"],
        "not_before": GTA6_UNLOCK,
    },
    # Rehearsal on today's GTA V content, before GTA 6 is out.
    "gta5": {
        "games": ["Grand Theft Auto V"],
        "keywords": ["gta 5", "gta v", "gta5", "gtav", "gta online", "grand theft auto v", "grand theft auto 5"],
        "not_before": "",
    },
}

# Titles with these words are skipped: leaks and spoilers get taken down by Rockstar
# and annoy the people you want to follow you.
DEFAULT_SKIP_WORDS = [
    "leak", "leaks", "leaked", "datamine", "datamined", "dataminer", "spoiler", "spoilers",
    "ending", "final mission", "early copy", "review copy", "pre-release", "prerelease",
]


def _list(raw: Optional[str], default: list[str]) -> list[str]:
    if raw is None or not raw.strip():
        return list(default)
    return [part.strip() for part in raw.split(",") if part.strip()]


def parse_when(raw: str, name: str) -> Optional[datetime]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ConfigError(f"{name} must look like 2026-11-18T23:00:00-06:00, got {raw!r}") from error
    if value.tzinfo is None:
        raise ConfigError(f"{name} needs a time zone offset, like -06:00")
    return value


@dataclass
class ScoutSettings:
    profile: str = "gta6"
    # Twitch (free developer app at dev.twitch.tv/console)
    twitch_client_id: str = ""
    twitch_client_secret: str = ""
    games: list[str] = field(default_factory=lambda: list(PROFILES["gta6"]["games"]))
    # What counts as on-topic in a YouTube title
    keywords: list[str] = field(default_factory=lambda: list(PROFILES["gta6"]["keywords"]))
    skip_words: list[str] = field(default_factory=lambda: list(DEFAULT_SKIP_WORDS))
    languages: list[str] = field(default_factory=lambda: ["en"])
    not_before: Optional[datetime] = None
    lookback_hours: int = 24
    min_views: int = 200
    interval_minutes: int = 15
    # Downloading
    download: bool = False
    downloads_per_run: int = 5
    clip_seconds: int = 45
    moments_per_video: int = 2
    keep_days: int = 14
    data_dir: Path = Path("/data")

    @property
    def scout_dir(self) -> Path:
        return self.data_dir / "scout"

    @property
    def db_file(self) -> Path:
        return self.scout_dir / "scout.db"

    @property
    def clips_dir(self) -> Path:
        return self.scout_dir / "clips"

    @property
    def creators_file(self) -> Path:
        return self.data_dir / "creators.txt"

    @property
    def blocklist_file(self) -> Path:
        return self.data_dir / "blocklist.txt"

    @property
    def has_twitch(self) -> bool:
        return bool(self.twitch_client_id and self.twitch_client_secret)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "ScoutSettings":
        env = os.environ if env is None else env
        profile = (env.get("SCOUT_PROFILE", "").strip().lower() or "gta6")
        if profile not in PROFILES:
            raise ConfigError(f"SCOUT_PROFILE must be one of {', '.join(PROFILES)}, got {profile!r}")
        preset = PROFILES[profile]
        not_before_raw = env.get("SCOUT_NOT_BEFORE")
        if not_before_raw is None:
            not_before_raw = preset["not_before"]
        languages = [x.lower() for x in _list(env.get("SCOUT_LANGUAGES"), ["en"])]
        if languages == ["any"]:
            languages = []
        return cls(
            profile=profile,
            twitch_client_id=env.get("TWITCH_CLIENT_ID", "").strip(),
            twitch_client_secret=env.get("TWITCH_CLIENT_SECRET", "").strip(),
            games=_list(env.get("SCOUT_GAMES"), preset["games"]),
            keywords=[k.lower() for k in _list(env.get("SCOUT_KEYWORDS"), preset["keywords"])],
            skip_words=[k.lower() for k in _list(env.get("SCOUT_SKIP_WORDS"), DEFAULT_SKIP_WORDS)],
            languages=languages,
            not_before=parse_when(not_before_raw, "SCOUT_NOT_BEFORE"),
            lookback_hours=_int(env, "SCOUT_LOOKBACK_HOURS", 24, minimum=1),
            min_views=_int(env, "SCOUT_MIN_VIEWS", 200, minimum=0),
            interval_minutes=_int(env, "SCOUT_INTERVAL_MINUTES", 15, minimum=5),
            download=_bool(env.get("SCOUT_DOWNLOAD"), False),
            downloads_per_run=_int(env, "SCOUT_DOWNLOADS_PER_RUN", 5, minimum=0),
            clip_seconds=_int(env, "SCOUT_CLIP_SECONDS", 45, minimum=10),
            moments_per_video=_int(env, "SCOUT_MOMENTS_PER_VIDEO", 2, minimum=1),
            keep_days=_int(env, "SCOUT_KEEP_DAYS", 14, minimum=1),
            data_dir=Path(env.get("ACP_DATA_DIR", "").strip() or "/data"),
        )
