"""Config loader for YouTube Research skill."""

import json
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "config.json"

DEFAULT_CONFIG = {
    "youtube_api_key": "",
    "output_dir": str(Path.home() / "youtube-research-output"),
    "default_language": "en",
    "max_results": 10,
}


def load_config() -> dict:
    """Load config from config.json, falling back to defaults."""
    config = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        config.update({k: v for k, v in user_config.items() if v})
    return config


def get_output_dir() -> Path:
    """Get and ensure output directory exists."""
    config = load_config()
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def get_api_key() -> str:
    """Get YouTube API key, empty string if not configured."""
    return load_config().get("youtube_api_key", "")
