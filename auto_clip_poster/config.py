"""Settings, read from environment variables (a .env file in Docker)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Mapping, Optional

POST_MODES = ("draft", "schedule", "now")


class ConfigError(ValueError):
    """A setting is missing or has a value we can't use."""


def _bool(value: Optional[str], default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(env: Mapping[str, str], name: str, default: int, minimum: int = 0) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from error
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}, got {value}")
    return value


def parse_times(raw: str, name: str = "POST_TIMES") -> list[time]:
    """'09:00, 18:30' -> [time(9, 0), time(18, 30)], sorted and de-duplicated."""
    out: set[time] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hour, minute = part.split(":")
            out.add(time(int(hour), int(minute)))
        except ValueError as error:
            raise ConfigError(f"{name} entries must look like 09:00, got {part!r}") from error
    if not out:
        raise ConfigError(f"{name} needs at least one time, like 09:00")
    return sorted(out)


@dataclass
class Settings:
    # Postiz (posting)
    postiz_api_url: str = "https://api.postiz.com/public/v1"
    postiz_api_key: str = ""
    postiz_integration_id: str = ""
    post_mode: str = "draft"
    adjust_media: bool = True
    # Schedule
    timezone: str = "America/Chicago"
    post_times: list[time] = field(default_factory=lambda: [time(9, 0), time(18, 0)])
    run_at: time = time(6, 0)
    # AutoClip (clipping)
    autoclip_bin: str = "autoclip"
    autoclip_data_dir: Path = Path("/data/autoclip")
    autoclip_timeout: int = 7200
    autoclip_template: str = ""
    autoclip_instruction: str = ""
    clips_per_video: int = 5
    # Captions
    caption_footer: str = ""
    max_hashtags: int = 10
    # Files and safety
    data_dir: Path = Path("/data")
    dry_run: bool = False

    @property
    def queue_file(self) -> Path:
        return self.data_dir / "queue.txt"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if env is None else env
        data_dir = Path(env.get("ACP_DATA_DIR", "").strip() or "/data")
        mode = (env.get("POST_MODE", "").strip().lower() or "draft")
        if mode not in POST_MODES:
            raise ConfigError(f"POST_MODE must be one of {', '.join(POST_MODES)}, got {mode!r}")
        run_at = parse_times(env.get("RUN_AT", "").strip() or "06:00", "RUN_AT")
        if len(run_at) != 1:
            raise ConfigError("RUN_AT takes one time, like 06:00")
        return cls(
            postiz_api_url=(env.get("POSTIZ_API_URL", "").strip() or cls.postiz_api_url).rstrip("/"),
            postiz_api_key=env.get("POSTIZ_API_KEY", "").strip(),
            postiz_integration_id=env.get("POSTIZ_INTEGRATION_ID", "").strip(),
            post_mode=mode,
            adjust_media=_bool(env.get("POSTIZ_ADJUST_MEDIA"), True),
            timezone=env.get("TIMEZONE", "").strip() or "America/Chicago",
            post_times=parse_times(env.get("POST_TIMES", "").strip() or "09:00,18:00"),
            run_at=run_at[0],
            autoclip_bin=env.get("AUTOCLIP_BIN", "").strip() or "autoclip",
            autoclip_data_dir=Path(env.get("AUTOCLIP_DATA_DIR", "").strip() or str(data_dir / "autoclip")),
            autoclip_timeout=_int(env, "AUTOCLIP_TIMEOUT", 7200, minimum=60),
            autoclip_template=env.get("AUTOCLIP_TEMPLATE", "").strip(),
            autoclip_instruction=env.get("AUTOCLIP_INSTRUCTION", "").strip(),
            clips_per_video=_int(env, "CLIPS_PER_VIDEO", 5, minimum=1),
            caption_footer=env.get("CAPTION_FOOTER", "").replace("\\n", "\n").strip(),
            max_hashtags=_int(env, "MAX_HASHTAGS", 10, minimum=0),
            data_dir=data_dir,
            dry_run=_bool(env.get("DRY_RUN"), False),
        )

    def require_postiz(self) -> None:
        if self.dry_run:
            return
        if not self.postiz_api_key:
            raise ConfigError("POSTIZ_API_KEY is empty. Copy it from Postiz: Settings > Developers > Public API.")
