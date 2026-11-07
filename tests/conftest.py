import json
from pathlib import Path

import pytest

from auto_clip_poster.config import Settings


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class FakeSession:
    """Stands in for requests.Session: replies from a route table and records every call."""

    def __init__(self, routes):
        self.routes = routes  # {(METHOD, path_suffix): FakeResponse or list of them}
        self.headers = {}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for (m, suffix), reply in self.routes.items():
            if m == method and url.endswith(suffix):
                if isinstance(reply, list):
                    return reply.pop(0) if len(reply) > 1 else reply[0]
                return reply
        return FakeResponse(404, {"message": "not found"})


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.from_env({
        "ACP_DATA_DIR": str(tmp_path),
        "POSTIZ_API_URL": "https://postiz.test/public/v1",
        "POSTIZ_API_KEY": "test-key",
        "POST_MODE": "schedule",
        "POST_TIMES": "09:00,18:00",
        "TIMEZONE": "America/Chicago",
        "CLIPS_PER_VIDEO": "3",
    })


def autoclip_json(tmp_path: Path, n=3, ok=True, project="p1"):
    outputs = []
    for i in range(n):
        video = tmp_path / f"clip{i}.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        outputs.append({
            "id": f"v{i}", "strategy_id": "instagram_reels", "status": "completed",
            "post": {"title": f"Title {i}", "description": f"Desc {i}", "tags": ["Podcast", "#Mindset", "big ideas"]},
            "video_path": str(video), "cover_path": None,
        })
    return json.dumps({"ok": ok, "project_id": project, "status": "completed" if ok else "failed", "outputs": outputs})
