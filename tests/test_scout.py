"""Tests for the GTA scout. No network: Twitch, YouTube and yt-dlp are all faked.

test_real_gta5_* replay real data read on 2026-11-10 (tests/fixtures/gta5_real_2026-11-10.json):
Twitch's top 20 GTA V clips of the day and the newest uploads of three GTA YouTube channels.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from auto_clip_poster.cli import main
from auto_clip_poster.config import ConfigError
from auto_clip_poster.scout.bank import Bank
from auto_clip_poster.scout.config import ScoutSettings
from auto_clip_poster.scout.fetch import FetchError, fetch
from auto_clip_poster.scout.rank import Rules, _phrase_re, drop_overlaps, moments_from_heatmap, score
from auto_clip_poster.scout.runner import Scout, report_text
from auto_clip_poster.scout.sources import (Candidate, SourceError, TwitchSource, YouTubeWatchlist,
                                            candidate_from_twitch, parse_rss, read_creators)
from tests.conftest import FakeResponse, FakeSession

FIXTURE = Path(__file__).parent / "fixtures" / "gta5_real_2026-11-10.json"
NOW = datetime(2026, 11, 10, 15, 36, tzinfo=timezone.utc)


def scout_settings(tmp_path, **env):
    base = {"ACP_DATA_DIR": str(tmp_path), "SCOUT_PROFILE": "gta5", "SCOUT_LANGUAGES": "any"}
    base.update(env)
    return ScoutSettings.from_env(base)


def real_candidates():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    items = [candidate_from_twitch(c) for c in data["twitch_clips"]]
    for v in data["youtube_uploads"]:
        items.append(Candidate(
            key=f"youtube:{v['video_id']}", source="youtube", url=f"https://www.youtube.com/watch?v={v['video_id']}",
            title=v["title"], creator=v["channel"], creator_id=v["channel_id"],
            published_at=datetime.fromisoformat(v["published"]), views=v["views"]))
    return items


# ----------------------------------------------------------------------------- settings

def test_profiles(tmp_path):
    gta6 = ScoutSettings.from_env({"ACP_DATA_DIR": str(tmp_path)})
    assert gta6.games == ["Grand Theft Auto VI"]
    assert gta6.not_before == datetime(2026, 11, 19, 5, 0, tzinfo=timezone.utc)  # 11 PM Central, Nov 18
    assert gta6.download is False and gta6.languages == ["en"]
    gta5 = scout_settings(tmp_path)
    assert gta5.games == ["Grand Theft Auto V"] and gta5.not_before is None and gta5.languages == []
    with pytest.raises(ConfigError):
        ScoutSettings.from_env({"SCOUT_PROFILE": "gta4"})
    with pytest.raises(ConfigError):
        ScoutSettings.from_env({"SCOUT_NOT_BEFORE": "2026-11-18 23:00"})


def test_whole_word_matching():
    gta5 = _phrase_re(["gta v", "gta 5"])
    assert gta5.search("Going Back to the Original GTA 5")
    assert gta5.search("gta  v speedrun")
    assert not gta5.search("Will GTA VI Make My Views Skyrocket?")
    skip = _phrase_re(["ending", "leaks"])
    assert skip.search("GTA 6: Achievement Leaks, Official Physical Box Leaks")
    assert not skip.search("Trending GTA clips")


# ----------------------------------------------------------------------------- sources

def test_twitch_token_game_and_clip_pages():
    clip = {"id": "Clip1", "url": "https://clips.twitch.tv/Clip1", "broadcaster_name": "xQc",
            "broadcaster_id": "71092938", "creator_name": "fan", "video_id": "v1", "language": "en",
            "title": "what", "view_count": 665, "created_at": "2026-11-09T21:36:00Z", "duration": 53.0,
            "vod_offset": 4000}
    session = FakeSession({
        ("POST", "/oauth2/token"): FakeResponse(200, {"access_token": "tok", "expires_in": 5000000}),
        ("GET", "/helix/games"): FakeResponse(200, {"data": [{"id": "32982", "name": "Grand Theft Auto V"}]}),
        ("GET", "/helix/clips"): [FakeResponse(200, {"data": [clip], "pagination": {"cursor": "next"}}),
                                  FakeResponse(200, {"data": [], "pagination": {}})],
    })
    twitch = TwitchSource("id", "secret", session=session)
    clips = twitch.clips("Grand Theft Auto V", NOW - timedelta(hours=24), NOW)
    assert [c.key for c in clips] == ["twitch:Clip1"]
    assert clips[0].views == 665 and clips[0].vod_offset == 4000 and clips[0].language == "en"
    clip_calls = [c for c in session.calls if c[1].endswith("/clips")]
    assert clip_calls[0][2]["params"]["game_id"] == "32982"
    assert clip_calls[0][2]["params"]["started_at"] == "2026-11-09T15:36:00Z"
    assert clip_calls[1][2]["params"]["after"] == "next"
    assert clip_calls[0][2]["headers"] == {"Client-Id": "id", "Authorization": "Bearer tok"}
    assert sum(1 for c in session.calls if c[1].endswith("/token")) == 1  # token reused


def test_twitch_needs_keys():
    with pytest.raises(SourceError):
        TwitchSource("", "")


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
 <title>DarkViperAU</title>
 <entry>
  <id>yt:video:L3oUi0wNG44</id>
  <yt:videoId>L3oUi0wNG44</yt:videoId>
  <yt:channelId>UCV6mNrW8CrmWtcxWfQXy11g</yt:channelId>
  <title>GTA 5 Facts and Glitches You Don't Know #104 (From Speedrunners)</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=L3oUi0wNG44"/>
  <author><name>DarkViperAU</name><uri>https://www.youtube.com/channel/UCV6mNrW8CrmWtcxWfQXy11g</uri></author>
  <published>2026-11-04T09:00:00+00:00</published>
  <updated>2026-11-10T09:00:00+00:00</updated>
  <media:group>
   <media:title>GTA 5 Facts and Glitches You Don't Know #104 (From Speedrunners)</media:title>
   <media:community><media:starRating count="9000" average="5.00" min="1" max="5"/><media:statistics views="259000"/></media:community>
  </media:group>
 </entry>
</feed>"""


def test_parse_rss():
    [item] = parse_rss(RSS)
    assert item.key == "youtube:L3oUi0wNG44" and item.views == 259000
    assert item.creator == "DarkViperAU" and item.creator_id == "UCV6mNrW8CrmWtcxWfQXy11g"
    assert item.published_at == datetime(2026, 11, 4, 9, tzinfo=timezone.utc)


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL."""
    calls = []
    info = {}

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        FakeYDL.calls.append(("info", url, self.opts))
        return FakeYDL.info

    def download(self, urls):
        FakeYDL.calls.append(("download", urls[0], self.opts))
        Path(self.opts["outtmpl"]).write_bytes(b"\x00\x00\x00\x18ftypmp42")


class FakeYtDlp:
    YoutubeDL = FakeYDL

    class utils:
        @staticmethod
        def download_range_func(chapters, ranges):
            return ("ranges", tuple(ranges))


@pytest.fixture(autouse=True)
def reset_fake():
    FakeYDL.calls = []
    FakeYDL.info = {}


def test_youtube_feed_down_falls_back_to_ytdlp():
    FakeYDL.info = {"entries": [{"id": "abc123def45", "title": "GTA 5 stunt", "view_count": 5000,
                                 "timestamp": NOW.timestamp(), "channel": "TGG"}]}
    session = FakeSession({("GET", "channel_id=UC72PuhDwKtZ5MikpGNhPAtA"): FakeResponse(500, {})})
    yt = YouTubeWatchlist(session=session, ytdlp=FakeYtDlp)
    from auto_clip_poster.scout.sources import Creator
    [item] = yt.uploads(Creator("UC72PuhDwKtZ5MikpGNhPAtA", "TGG"))
    assert item.key == "youtube:abc123def45" and item.views == 5000
    assert FakeYDL.calls[0][1] == "https://www.youtube.com/channel/UC72PuhDwKtZ5MikpGNhPAtA/videos"


def test_starter_creator_list_is_written_once(tmp_path):
    path = tmp_path / "creators.txt"
    creators = read_creators(path)
    assert len(creators) == 8 and creators[0].name == "TGG"
    path.write_text("@SomeoneElse | Someone\n", encoding="utf-8")
    assert [c.channel for c in read_creators(path)] == ["@SomeoneElse"]


# ----------------------------------------------------------------------------- ranking

def test_score_prefers_fast_risers():
    fresh = Candidate("twitch:a", "twitch", "u", "t", "c", published_at=NOW - timedelta(hours=1), views=1000)
    stale = Candidate("twitch:b", "twitch", "u", "t", "c", published_at=NOW - timedelta(hours=48), views=5000)
    assert score(fresh, NOW) > score(stale, NOW)


def test_same_moment_clipped_twice_keeps_the_bigger_one():
    a = Candidate("twitch:a", "twitch", "u", "t", "c", views=900, video_id="v1", vod_offset=1000)
    b = Candidate("twitch:b", "twitch", "u", "t", "c", views=300, video_id="v1", vod_offset=1012)
    c = Candidate("twitch:c", "twitch", "u", "t", "c", views=200, video_id="v1", vod_offset=5000)
    kept, dropped = drop_overlaps([b, a, c])
    assert [k.key for k in kept] == ["twitch:a", "twitch:c"]
    assert dropped[0].extra["duplicate_of"] == "twitch:a"


def test_heatmap_moments():
    heat = [{"start_time": i * 6.0, "end_time": (i + 1) * 6.0, "value": 0.1} for i in range(100)]
    heat[1]["value"] = 1.0   # intro replay: ignored
    heat[40]["value"] = 0.9  # 243 s
    heat[41]["value"] = 0.8  # overlaps the first pick: ignored
    heat[70]["value"] = 0.7  # 423 s
    moments = moments_from_heatmap(heat, 600, clip_seconds=40, count=2)
    assert [(m.start, m.end) for m in moments] == [(223.0, 263.0), (403.0, 443.0)]
    assert moments[0].peak == 0.9
    assert moments_from_heatmap([], 600) == []


# ----------------------------------------------------------------------------- real GTA V data

def test_real_gta5_pass_ranks_and_filters(tmp_path):
    s = scout_settings(tmp_path)
    report = Scout(s, twitch=None, youtube=None, clock=lambda: NOW).run_once(candidates=real_candidates())
    assert report.found == 39
    bank = Bank(s.db_file)
    # Leak and datamine videos never make the list.
    assert bank.get("youtube:kqGKEVdC5k4")["status"] == "skipped"     # "Game Files DATAMINED"
    assert "leak" in bank.get("youtube:ABb_jP4UzEM")["reason"]        # "Achievement Leaks"
    # Off-topic for GTA V: a Mafia II upload and GTA 6 news.
    assert bank.get("youtube:vTNO4fjiORM")["reason"] == "title isn't about the game"
    assert bank.get("youtube:6yQgfcsgq-Q")["reason"] == "title isn't about the game"
    # Older than a day.
    assert "older than" in bank.get("youtube:L3oUi0wNG44")["reason"]
    # GTA V content from today passes.
    assert bank.get("youtube:7n_8l4uvnjk")["status"] == "new"        # "Going Back to the Original GTA 5"
    top_twitch = bank.top(3, "twitch")
    assert top_twitch[0]["key"] == "twitch:EndearingFaintPeanutFUNgineer-5ziWGxu9uZFiEQpf"  # 3.3K views
    assert all(r["status"] == "new" for r in bank.top(20, "twitch"))
    bank.close()
    text = report_text(s)
    assert "Tony (AnthonyZ) jukes cops at white garage" in text and "Skipped, and why" in text


def test_real_data_is_all_blocked_before_gta6_unlocks(tmp_path):
    s = ScoutSettings.from_env({"ACP_DATA_DIR": str(tmp_path), "SCOUT_PROFILE": "gta6",
                                "SCOUT_KEYWORDS": "gta 6,gta vi,gta 5,gta online", "SCOUT_LANGUAGES": "any"})
    report = Scout(s, twitch=None, youtube=None, clock=lambda: NOW).run_once(candidates=real_candidates())
    assert report.kept == 0


def test_blocklist_and_seeing_a_clip_again(tmp_path):
    s = scout_settings(tmp_path)
    (tmp_path / "blocklist.txt").write_text("# no\nxQc\n", encoding="utf-8")
    scout = Scout(s, twitch=None, youtube=None, clock=lambda: NOW)
    scout.run_once(candidates=real_candidates())
    bank = Bank(s.db_file)
    assert bank.get("twitch:ResourcefulBlazingKumquatPraiseIt-dbnC1oRVhIZpi3Bp")["reason"] == "creator is on your blocklist"
    bank.mark_downloaded("twitch:EndearingFaintPeanutFUNgineer-5ziWGxu9uZFiEQpf", ["/x.mp4"], [])
    bank.close()
    again = real_candidates()
    again[0].views = 9000
    scout.run_once(candidates=again)
    bank = Bank(s.db_file)
    row = bank.get("twitch:EndearingFaintPeanutFUNgineer-5ziWGxu9uZFiEQpf")
    assert row["status"] == "downloaded" and row["views"] == 9000   # not downloaded twice, views refreshed
    bank.close()


# ----------------------------------------------------------------------------- downloading

def test_downloads_take_turns_and_cut_youtube_peaks(tmp_path):
    s = scout_settings(tmp_path, SCOUT_DOWNLOADS_PER_RUN="3", SCOUT_CLIP_SECONDS="30", SCOUT_MOMENTS_PER_VIDEO="1")
    heat = [{"start_time": i * 10.0, "end_time": (i + 1) * 10.0, "value": 0.2} for i in range(60)]
    heat[30]["value"] = 1.0
    FakeYDL.info = {"duration": 600, "heatmap": heat}
    scout = Scout(s, twitch=None, youtube=None, ytdlp=FakeYtDlp, clock=lambda: NOW)
    report = scout.run_once(download=True, candidates=real_candidates())
    assert len(report.downloaded) == 3 and not report.failed
    bank = Bank(s.db_file)
    done = bank.db.execute("SELECT key, source, moments FROM candidates WHERE status='downloaded'").fetchall()
    assert sorted(r["source"] for r in done) == ["twitch", "twitch", "youtube"]
    yt = next(r for r in done if r["source"] == "youtube")
    assert json.loads(yt["moments"])[0]["start"] == 290.0
    bank.close()
    ranged = [c for c in FakeYDL.calls if c[0] == "download" and "download_ranges" in c[2]]
    assert ranged[0][2]["download_ranges"] == ("ranges", ((290.0, 320.0),))
    sidecars = list((tmp_path / "scout" / "clips").glob("*.json"))
    assert len(sidecars) == 3 and json.loads(sidecars[0].read_text())["creator"]


def test_youtube_without_replay_graph_fails_cleanly(tmp_path):
    FakeYDL.info = {"duration": 600}
    row = {"key": "youtube:x", "source": "youtube", "url": "https://www.youtube.com/watch?v=x"}
    with pytest.raises(FetchError, match="Most-replayed"):
        fetch(row, tmp_path, 45, 2, ytdlp=FakeYtDlp)


def test_cli_scout_report_and_check(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ACP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCOUT_PROFILE", "gta5")
    assert main(["scout", "report"]) == 0
    assert "In the bank: nothing yet" in capsys.readouterr().out
    main(["scout", "check"])
    out = capsys.readouterr().out
    assert "Twitch: no keys yet" in out and "8 YouTube channel(s)" in out
