from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from auto_clip_poster.postiz import PostizClient, PostizError

from .conftest import FakeResponse, FakeSession

IG = {"id": "ig1", "identifier": "instagram", "profile": "myclips", "disabled": False}
X = {"id": "x1", "identifier": "x", "profile": "me", "disabled": False}


def client(routes):
    session = FakeSession(routes)
    return PostizClient("https://postiz.test/public/v1", "k", session=session, sleep=lambda s: None), session


def test_api_key_header_set():
    c, session = client({})
    assert session.headers["Authorization"] == "k"


def test_picks_the_only_instagram_channel():
    c, _ = client({("GET", "/integrations"): FakeResponse(200, [X, IG])})
    assert c.instagram_channel()["id"] == "ig1"


def test_several_instagram_channels_need_an_id():
    other = {**IG, "id": "ig2", "identifier": "instagram-standalone"}
    c, _ = client({("GET", "/integrations"): FakeResponse(200, [IG, other])})
    with pytest.raises(PostizError, match="POSTIZ_INTEGRATION_ID"):
        c.instagram_channel()
    assert c.instagram_channel("ig2")["identifier"] == "instagram-standalone"


def test_no_instagram_channel():
    c, _ = client({("GET", "/integrations"): FakeResponse(200, [X])})
    with pytest.raises(PostizError, match="No Instagram channel"):
        c.instagram_channel()


def test_bad_key_message():
    c, _ = client({("GET", "/integrations"): FakeResponse(401, {"message": "no"})})
    with pytest.raises(PostizError, match="POSTIZ_API_KEY"):
        c.integrations()


def test_server_errors_are_retried():
    c, session = client({("GET", "/integrations"): [FakeResponse(502, {}), FakeResponse(200, [IG])]})
    assert c.integrations() == [IG]
    assert len(session.calls) == 2


def test_upload_and_wait_for_processing(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    c, session = client({
        ("POST", "/upload"): FakeResponse(200, {"id": "m1", "path": "https://cdn/clip.mp4", "status": "processing"}),
        ("GET", "/media/m1/status"): [FakeResponse(200, {"status": "processing"}),
                                      FakeResponse(200, {"id": "m1", "path": "https://cdn/clip.mp4", "status": "ready"})],
    })
    media = c.wait_until_ready(c.upload(video, adjust=True))
    assert media["status"] == "ready"
    method, url, kwargs = session.calls[0]
    assert kwargs["data"] == {"adjust": "true"} and kwargs["files"]["file"][2] == "video/mp4"


def test_wait_skips_when_status_endpoint_missing():
    c, _ = client({})  # 404 for everything
    media = {"id": "m1", "path": "https://cdn/c.mp4", "status": "processing"}
    assert c.wait_until_ready(media) == media


def test_create_post_body_is_a_reel_in_utc():
    c, session = client({("POST", "/posts"): FakeResponse(200, [{"postId": "p1", "integration": "ig1"}])})
    when = datetime(2026, 11, 8, 9, 0, tzinfo=ZoneInfo("America/Chicago"))
    c.create_post(IG, "caption", {"id": "m1", "path": "https://cdn/c.mp4"}, when, "schedule")
    body = session.calls[0][2]["json"]
    assert body["type"] == "schedule"
    assert body["date"] == "2026-11-08T15:00:00.000Z"  # 9:00 Chicago (CST) is 15:00 UTC
    post = body["posts"][0]
    assert post["integration"] == {"id": "ig1"}
    assert post["settings"] == {"__type": "instagram", "post_type": "post"}
    assert post["value"][0]["image"] == [{"id": "m1", "path": "https://cdn/c.mp4"}]


def test_create_post_needs_timezone():
    c, _ = client({})
    with pytest.raises(ValueError):
        c.create_post(IG, "c", {"path": "p"}, datetime(2026, 1, 1), "draft")
