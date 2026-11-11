"""Where candidates come from: Twitch's top clips for a game, and a YouTube watchlist.

Both only read public data. Nothing here downloads video or posts anything.
"""
from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import requests

log = logging.getLogger(__name__)

TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_API = "https://api.twitch.tv/helix"
YOUTUBE_RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={}"

NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


class SourceError(RuntimeError):
    """A source could not be read (bad keys, network down, feed broken)."""


@dataclass
class Candidate:
    """One thing that might become a clip: a Twitch clip or a YouTube upload."""
    key: str                 # "twitch:<clip id>" or "youtube:<video id>"
    source: str              # "twitch" or "youtube"
    url: str
    title: str
    creator: str             # streamer or channel name, used for credit and the blocklist
    creator_id: str = ""
    published_at: Optional[datetime] = None
    views: int = 0
    duration: float = 0.0    # seconds; 0 when the source doesn't say
    language: str = ""
    video_id: str = ""       # Twitch: the VOD the clip came from (for spotting duplicates)
    vod_offset: Optional[int] = None
    extra: dict = field(default_factory=dict)

    def age_hours(self, now: datetime) -> float:
        if not self.published_at:
            return 24.0
        return max((now - self.published_at).total_seconds() / 3600, 0.25)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["published_at"] = self.published_at.isoformat() if self.published_at else None
        return data


def _parse_time(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _rfc3339(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- Twitch

class TwitchSource:
    """Top clips for a game from Twitch's official API (Get Clips, sorted by views)."""

    def __init__(self, client_id: str, client_secret: str, session: Optional[requests.Session] = None):
        if not client_id or not client_secret:
            raise SourceError("TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET are empty. "
                              "Make a free app at dev.twitch.tv/console and paste both into .env.")
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = session or requests.Session()
        self._token = ""
        self._token_expires = 0.0
        self._game_ids: dict[str, str] = {}

    def _auth(self) -> dict:
        if not self._token or time.time() > self._token_expires - 60:
            reply = self.session.request("POST", TWITCH_TOKEN_URL, data={
                "client_id": self.client_id, "client_secret": self.client_secret,
                "grant_type": "client_credentials"}, timeout=30)
            if reply.status_code != 200:
                raise SourceError(f"Twitch refused the app keys ({reply.status_code}). Check TWITCH_CLIENT_ID/SECRET.")
            body = reply.json()
            self._token = body["access_token"]
            self._token_expires = time.time() + int(body.get("expires_in", 3600))
        return {"Client-Id": self.client_id, "Authorization": f"Bearer {self._token}"}

    def _get(self, path: str, params: dict) -> dict:
        reply = self.session.request("GET", f"{TWITCH_API}{path}", params=params, headers=self._auth(), timeout=30)
        if reply.status_code == 401:  # token expired early; get a new one once
            self._token = ""
            reply = self.session.request("GET", f"{TWITCH_API}{path}", params=params, headers=self._auth(), timeout=30)
        if reply.status_code != 200:
            raise SourceError(f"Twitch {path} answered {reply.status_code}: {reply.text[:200]}")
        return reply.json()

    def game_id(self, name: str) -> str:
        if name.isdigit():
            return name
        if name not in self._game_ids:
            data = self._get("/games", {"name": name}).get("data") or []
            if not data:
                raise SourceError(f"Twitch has no category named {name!r} yet")
            self._game_ids[name] = data[0]["id"]
        return self._game_ids[name]

    def clips(self, game: str, since: datetime, until: datetime, limit: int = 300) -> list[Candidate]:
        params = {"game_id": self.game_id(game), "started_at": _rfc3339(since),
                  "ended_at": _rfc3339(until), "first": 100}
        out: list[Candidate] = []
        while len(out) < limit:
            body = self._get("/clips", params)
            for item in body.get("data") or []:
                out.append(candidate_from_twitch(item))
            cursor = (body.get("pagination") or {}).get("cursor")
            if not cursor or not body.get("data"):
                break
            params["after"] = cursor
        return out[:limit]


def candidate_from_twitch(item: dict) -> Candidate:
    return Candidate(
        key=f"twitch:{item['id']}",
        source="twitch",
        url=item.get("url") or f"https://clips.twitch.tv/{item['id']}",
        title=(item.get("title") or "").strip(),
        creator=item.get("broadcaster_name") or "",
        creator_id=str(item.get("broadcaster_id") or ""),
        published_at=_parse_time(item.get("created_at")),
        views=int(item.get("view_count") or 0),
        duration=float(item.get("duration") or 0),
        language=(item.get("language") or "").lower(),
        video_id=str(item.get("video_id") or ""),
        vod_offset=item.get("vod_offset"),
        extra={"clipped_by": item.get("creator_name") or "", "thumbnail": item.get("thumbnail_url") or ""},
    )


# --------------------------------------------------------------------------- YouTube

@dataclass
class Creator:
    channel: str      # a channel id (UC...) or a handle (@name)
    name: str = ""


STARTER_CREATORS = """\
# Your YouTube watchlist. One channel per line: a channel id (UC...) or @handle, then | and a name.
# The scout checks each channel's new uploads every cycle (free, uses no API quota).
# This is a starter list of big GTA channels. Edit it freely. Being on this list is NOT
# permission to repost them: add creators who allow clipping, and put anyone who
# files a claim in blocklist.txt.
UC72PuhDwKtZ5MikpGNhPAtA | TGG
UCV6mNrW8CrmWtcxWfQXy11g | DarkViperAU
UCuWcjpKbIDAbZfHoru1toFg | GTA Series Videos
UCcZ3sRk9z6jjgfYQbMGTpig | Broughy1322
UC36MGPfPwOWafAXauiV4LdA | TmarTn2
UCFTjC8OaMv_kP5frj9UU1Rw | ENXGMA
UC9C95npo2PgAg1f_Zy3HdBQ | Tylarious
UC4zMEl8Qh_nE5nDnp0cxRFQ | Gtamen
"""

STARTER_BLOCKLIST = """\
# Creators to never clip. One per line: a channel name, @handle, Twitch name or channel id.
# Add anyone who files a copyright claim or asks you to stop.
"""


def read_creators(path: Path) -> list[Creator]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(STARTER_CREATORS, encoding="utf-8")
    out: list[Creator] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        channel, _, name = line.partition("|")
        channel = channel.strip()
        if channel:
            out.append(Creator(channel=channel, name=name.strip()))
    return out


def read_blocklist(path: Path) -> set[str]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(STARTER_BLOCKLIST, encoding="utf-8")
    return {line.strip().lower().lstrip("@") for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")}


def parse_rss(xml_text: str) -> list[Candidate]:
    """Parse a YouTube channel feed (its 15 newest uploads)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as error:
        raise SourceError(f"YouTube feed was not valid XML: {error}") from error
    out: list[Candidate] = []
    for entry in root.findall("a:entry", NS):
        video_id = entry.findtext("yt:videoId", default="", namespaces=NS)
        if not video_id:
            continue
        link = entry.find("a:link", NS)
        stats = entry.find("media:group/media:community/media:statistics", NS)
        url = link.get("href") if link is not None else f"https://www.youtube.com/watch?v={video_id}"
        out.append(Candidate(
            key=f"youtube:{video_id}",
            source="youtube",
            url=url,
            title=(entry.findtext("a:title", default="", namespaces=NS) or "").strip(),
            creator=entry.findtext("a:author/a:name", default="", namespaces=NS),
            creator_id=entry.findtext("yt:channelId", default="", namespaces=NS),
            published_at=_parse_time(entry.findtext("a:published", default="", namespaces=NS)),
            views=int(stats.get("views", 0)) if stats is not None else 0,
            extra={"short": "/shorts/" in url},
        ))
    return out


class YouTubeWatchlist:
    """New uploads from a list of channels: the free RSS feed first, yt-dlp if the feed is down."""

    def __init__(self, session: Optional[requests.Session] = None, ytdlp=None):
        self.session = session or requests.Session()
        self._ytdlp = ytdlp  # injectable for tests
        self._resolved: dict[str, str] = {}

    def _ydl(self):
        if self._ytdlp is None:
            try:
                import yt_dlp
            except ImportError as error:
                raise SourceError("yt-dlp is not installed: pip install yt-dlp") from error
            self._ytdlp = yt_dlp
        return self._ytdlp

    def channel_id(self, channel: str) -> str:
        if re.fullmatch(r"UC[\w-]{22}", channel):
            return channel
        if channel not in self._resolved:
            handle = channel if channel.startswith("@") else "@" + channel
            with self._ydl().YoutubeDL({"quiet": True, "extract_flat": True, "playlistend": 1,
                                        "skip_download": True}) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/{handle}", download=False)
            self._resolved[channel] = info.get("channel_id") or info.get("id") or ""
        return self._resolved[channel]

    def uploads(self, creator: Creator) -> list[Candidate]:
        cid = self.channel_id(creator.channel)
        try:
            reply = self.session.request("GET", YOUTUBE_RSS.format(cid), timeout=30)
            if reply.status_code == 200:
                items = parse_rss(reply.text)
                for item in items:
                    item.creator = item.creator or creator.name
                return items
            log.warning("YouTube feed for %s answered %s; using yt-dlp instead", creator.name or cid, reply.status_code)
        except (requests.RequestException, SourceError) as error:
            log.warning("YouTube feed for %s failed (%s); using yt-dlp instead", creator.name or cid, error)
        return self._uploads_ytdlp(cid, creator.name)

    def _uploads_ytdlp(self, channel_id: str, name: str) -> list[Candidate]:
        opts = {"quiet": True, "extract_flat": True, "playlistend": 15, "skip_download": True}
        with self._ydl().YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/channel/{channel_id}/videos", download=False)
        out = []
        for entry in info.get("entries") or []:
            vid = entry.get("id")
            if not vid:
                continue
            published = None
            if entry.get("timestamp"):
                published = datetime.fromtimestamp(entry["timestamp"], tz=timezone.utc)
            out.append(Candidate(
                key=f"youtube:{vid}", source="youtube", url=f"https://www.youtube.com/watch?v={vid}",
                title=entry.get("title") or "", creator=entry.get("channel") or name, creator_id=channel_id,
                published_at=published, views=int(entry.get("view_count") or 0),
                duration=float(entry.get("duration") or 0)))
        return out


def collect(settings, twitch: Optional[TwitchSource], youtube: Optional[YouTubeWatchlist],
            now: Optional[datetime] = None) -> tuple[list[Candidate], list[str]]:
    """Read every source once. Returns (candidates, problems). One broken source never stops the rest."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=settings.lookback_hours)
    found: list[Candidate] = []
    problems: list[str] = []
    if twitch is not None:
        for game in settings.games:
            try:
                found.extend(twitch.clips(game, since, now))
            except (SourceError, requests.RequestException) as error:
                problems.append(f"Twitch {game}: {error}")
    if youtube is not None:
        for creator in read_creators(settings.creators_file):
            try:
                found.extend(youtube.uploads(creator))
            except Exception as error:  # yt-dlp raises many types; one channel must not stop the rest
                problems.append(f"YouTube {creator.name or creator.channel}: {error}")
    return found, problems


def iter_unique(items: Iterable[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    out = []
    for item in items:
        if item.key not in seen:
            seen.add(item.key)
            out.append(item)
    return out
