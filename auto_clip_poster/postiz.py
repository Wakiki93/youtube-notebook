"""Step 2: upload a Reel to Postiz and schedule it on the Instagram channel.

Postiz talks to Instagram through Meta's official Graph API, so this never logs
in to Instagram directly. Postiz runs as its own service (Postiz Cloud or the
official Docker setup); we only call its public API, which keeps Postiz's AGPL
code out of this repo.

API reference: https://docs.postiz.com/public-api/introduction
"""
from __future__ import annotations

import logging
import mimetypes
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)

INSTAGRAM_TYPES = ("instagram", "instagram-standalone")


class PostizError(RuntimeError):
    """Postiz refused a request or answered with something unexpected."""


class PostizClient:
    def __init__(self, base_url: str, api_key: str, session: Optional[requests.Session] = None,
                 timeout: float = 60, sleep=_time.sleep):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": api_key})
        self.timeout = timeout
        self._sleep = sleep

    # -- plumbing -----------------------------------------------------------
    def _request(self, method: str, path: str, *, retries: int = 3, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", self.timeout)
        for attempt in range(1, retries + 1):
            try:
                response = self.session.request(method, url, **kwargs)
            except requests.RequestException as error:
                if attempt == retries:
                    raise PostizError(f"Can't reach Postiz at {self.base_url}: {error}") from error
                self._sleep(2 ** attempt)
                continue
            if response.status_code >= 500 and attempt < retries:
                self._sleep(2 ** attempt)
                continue
            if response.status_code == 401:
                raise PostizError("Postiz rejected the API key (401). Check POSTIZ_API_KEY.")
            if response.status_code == 429:
                raise PostizError("Postiz rate limit hit (429). Try again in an hour.")
            if response.status_code >= 400:
                raise PostizError(f"Postiz {method} {path} failed ({response.status_code}): {response.text[:300]}")
            if not response.content:
                return None
            try:
                return response.json()
            except ValueError as error:
                raise PostizError(f"Postiz {method} {path} returned non-JSON: {response.text[:200]}") from error
        raise PostizError(f"Postiz {method} {path} failed after {retries} tries")

    # -- channels -----------------------------------------------------------
    def integrations(self) -> list[dict]:
        data = self._request("GET", "/integrations")
        return data if isinstance(data, list) else []

    def instagram_channel(self, preferred_id: str = "") -> dict:
        """Pick the Instagram channel: the one named in settings, or the only one connected."""
        channels = [c for c in self.integrations() if c.get("identifier") in INSTAGRAM_TYPES and not c.get("disabled")]
        if preferred_id:
            for channel in channels:
                if channel.get("id") == preferred_id:
                    return channel
            raise PostizError(f"No active Instagram channel with id {preferred_id} in Postiz")
        if not channels:
            raise PostizError("No Instagram channel is connected in Postiz yet. Add one under Add Channel.")
        if len(channels) > 1:
            names = ", ".join(f"{c.get('profile') or c.get('name')} ({c.get('id')})" for c in channels)
            raise PostizError(f"Several Instagram channels found; set POSTIZ_INTEGRATION_ID to one of: {names}")
        return channels[0]

    # -- media --------------------------------------------------------------
    def upload(self, path: str | Path, adjust: bool = True) -> dict:
        path = Path(path)
        mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
        with path.open("rb") as handle:
            data = self._request(
                "POST", "/upload", retries=1,
                files={"file": (path.name, handle, mime)},
                data={"adjust": "true" if adjust else "false"},
                timeout=max(self.timeout, 600),
            )
        if not isinstance(data, dict) or not data.get("path"):
            raise PostizError(f"Postiz upload gave no file path: {data}")
        return data

    def wait_until_ready(self, media: dict, timeout: float = 300, interval: float = 5) -> dict:
        """With adjust on, Postiz re-encodes in the background; wait so the post uses the fixed file."""
        if media.get("status", "ready") == "ready" or not media.get("id"):
            return media
        waited = 0.0
        while waited < timeout:
            self._sleep(interval)
            waited += interval
            try:
                status = self._request("GET", f"/media/{media['id']}/status", retries=1)
            except PostizError as error:
                if "(404)" in str(error):  # older self-hosted Postiz has no status endpoint
                    return media
                raise
            state = (status or {}).get("status")
            if state == "ready":
                if status.get("warning"):
                    log.warning("Postiz kept the original file: %s", status["warning"])
                return {**media, **status}
            if state == "failed":
                raise PostizError(f"Postiz could not process the video: {status.get('error') or status}")
        raise PostizError(f"Postiz was still processing the video after {int(timeout)}s")

    # -- posts --------------------------------------------------------------
    def create_post(self, channel: dict, caption: str, media: dict, when: datetime, mode: str) -> Any:
        """mode: 'draft' (waits for you in the Postiz calendar), 'schedule', or 'now'."""
        if when.tzinfo is None:
            raise ValueError("when must be timezone-aware")
        body = {
            "type": mode,
            "date": when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "shortLink": False,
            "tags": [],
            "posts": [{
                "integration": {"id": channel["id"]},
                "value": [{"content": caption, "image": [{"id": media.get("id", ""), "path": media["path"]}]}],
                # A single video posted as post_type "post" goes out as a Reel.
                "settings": {"__type": channel.get("identifier", "instagram"), "post_type": "post"},
            }],
        }
        return self._request("POST", "/posts", retries=1, json=body)
