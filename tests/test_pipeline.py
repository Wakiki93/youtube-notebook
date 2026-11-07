from datetime import datetime, time
from zoneinfo import ZoneInfo

from auto_clip_poster.clipper import Clip, ClipError
from auto_clip_poster.pipeline import Pipeline, next_slots, seconds_until
from auto_clip_poster.postiz import PostizClient, PostizError
from auto_clip_poster.state import State, append_queue, read_queue

from .conftest import FakeResponse, FakeSession

CHI = ZoneInfo("America/Chicago")
IG = {"id": "ig1", "identifier": "instagram", "profile": "myclips", "disabled": False}


def fake_client(fail_posts=False):
    session = FakeSession({
        ("GET", "/integrations"): FakeResponse(200, [IG]),
        ("POST", "/upload"): FakeResponse(200, {"id": "m", "path": "https://cdn/c.mp4", "status": "ready"}),
        ("POST", "/posts"): FakeResponse(500 if fail_posts else 200, [{"postId": "p", "integration": "ig1"}]),
    })
    return PostizClient("https://postiz.test/public/v1", "k", session=session, sleep=lambda s: None), session


def fake_produce(tmp_path, n=3, fail_on=()):
    calls = []

    def produce(url, settings, credit):
        calls.append(url)
        if url in fail_on:
            raise ClipError("no captions")
        clips = []
        for i in range(n):
            video = tmp_path / f"{abs(hash(url))}-{i}.mp4"
            video.write_bytes(b"v")
            clips.append(Clip(video_path=str(video), source_url=url, title=f"{url} #{i}", credit=credit))
        return clips

    produce.calls = calls
    return produce


def at(hour, minute=0, day=7):
    return lambda: datetime(2026, 11, day, hour, minute, tzinfo=CHI)


def posts(session):
    return [c[2]["json"] for c in session.calls if c[0] == "POST" and c[1].endswith("/posts")]


# -- slots -----------------------------------------------------------------
def test_next_slots_skips_past_and_taken():
    now = datetime(2026, 11, 7, 12, 0, tzinfo=CHI)
    taken = {datetime(2026, 11, 7, 18, 0, tzinfo=CHI).isoformat()}
    slots = next_slots(now, [time(9), time(18)], "America/Chicago", 2, taken)
    assert [s.isoformat() for s in slots] == ["2026-11-08T09:00:00-06:00", "2026-11-08T18:00:00-06:00"]


def test_seconds_until_rolls_to_tomorrow():
    now = datetime(2026, 11, 7, 7, 0, tzinfo=CHI)
    assert seconds_until(now, time(6, 0), "America/Chicago") == 23 * 3600


# -- smallest slice ----------------------------------------------------------
def test_once_posts_best_clip_and_banks_the_rest(settings, tmp_path):
    client, session = fake_client()
    produce = fake_produce(tmp_path, n=3)
    report = Pipeline(settings, client=client, produce=produce, now=at(12)).run_once("https://youtu.be/a", credit="@host")
    assert len(report.posted) == 1
    body = posts(session)[0]
    assert body["date"] == "2026-11-08T00:00:00.000Z"  # 18:00 Chicago today
    assert "Credit: @host" in body["posts"][0]["value"][0]["content"]
    state = State(settings.state_file)
    assert len(state.bank) == 2 and state.bank[0]["title"] == "https://youtu.be/a #1"
    assert state.data["sources"]["https://youtu.be/a"]["status"] == "clipped"


def test_once_checks_postiz_before_clipping(settings, tmp_path):
    session = FakeSession({("GET", "/integrations"): FakeResponse(200, [])})
    client = PostizClient("https://p/public/v1", "k", session=session, sleep=lambda s: None)
    produce = fake_produce(tmp_path)
    try:
        Pipeline(settings, client=client, produce=produce, now=at(12)).run_once("u")
    except PostizError:
        pass
    assert produce.calls == []  # never spent time clipping


# -- daily loop ---------------------------------------------------------------
def test_cycle_refills_from_queue_and_fills_two_slots(settings, tmp_path):
    append_queue(settings.queue_file, "https://youtu.be/a", "@a")
    append_queue(settings.queue_file, "https://youtu.be/b")
    client, session = fake_client()
    produce = fake_produce(tmp_path, n=1, fail_on={"https://youtu.be/a"})
    report = Pipeline(settings, client=client, produce=produce, now=at(6)).run_cycle()
    # a failed, so the loop moved on to b; a waits until tomorrow for its one retry
    assert produce.calls == ["https://youtu.be/a", "https://youtu.be/b"]
    assert len(report.posted) == 1 and "Out of clips" in report.note
    assert len(report.failed) == 1 and report.queue_left == 1
    Pipeline(settings, client=client, produce=produce, now=at(6, day=8)).run_cycle()
    assert produce.calls[-1] == "https://youtu.be/a"  # retried once, next day
    assert State(settings.state_file).next_source(read_queue(settings.queue_file)) is None  # gave up after 2 tries


def test_cycle_is_idempotent_for_slots(settings, tmp_path):
    for u in ("https://youtu.be/a", "https://youtu.be/b"):
        append_queue(settings.queue_file, u)
    client, session = fake_client()
    pipe = Pipeline(settings, client=client, produce=fake_produce(tmp_path, n=3), now=at(6))
    pipe.run_cycle()
    pipe.run_cycle()  # same morning again: today's slots are taken, so it fills tomorrow's
    dates = [b["date"] for b in posts(session)]
    assert dates == ["2026-11-07T15:00:00.000Z", "2026-11-08T00:00:00.000Z",
                     "2026-11-08T15:00:00.000Z", "2026-11-09T00:00:00.000Z"]
    assert len(set(dates)) == 4


def test_failed_post_returns_clip_to_bank(settings, tmp_path):
    append_queue(settings.queue_file, "https://youtu.be/a")
    client, _ = fake_client(fail_posts=True)
    report = Pipeline(settings, client=client, produce=fake_produce(tmp_path, n=3), now=at(6)).run_cycle()
    assert report.posted == [] and report.failed
    assert len(State(settings.state_file).bank) == 3


def test_dry_run_never_calls_postiz(settings, tmp_path):
    settings.dry_run = True
    append_queue(settings.queue_file, "https://youtu.be/a")
    report = Pipeline(settings, produce=fake_produce(tmp_path, n=2), now=at(6)).run_cycle()
    assert len(report.posted) == 2
    assert all(p["dry_run"] for p in State(settings.state_file).data["posts"])


def test_loop_survives_a_crash(settings, tmp_path):
    pipe = Pipeline(settings, client=fake_client()[0], produce=fake_produce(tmp_path), now=at(6))
    pipe.run_cycle = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    slept = []
    pipe.loop(sleep=slept.append, max_cycles=2)
    assert len(slept) == 1


def test_queue_file_parsing(tmp_path):
    q = tmp_path / "queue.txt"
    q.write_text("# my list\nhttps://youtu.be/a | Credit: @a\n\nnot a link\nhttps://youtu.be/a\n/videos/local.mp4")
    items = read_queue(q)
    assert [(i.url, i.credit) for i in items] == [("https://youtu.be/a", "Credit: @a"), ("/videos/local.mp4", "")]
    assert append_queue(q, "https://youtu.be/b") is True
    assert append_queue(q, "https://youtu.be/b") is False
    assert q.read_text().endswith("/videos/local.mp4\nhttps://youtu.be/b\n")
