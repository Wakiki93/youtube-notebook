#!/usr/bin/env python3
"""
YouTube transcript extraction — youtube-transcript-api primary, yt-dlp fallback.

Usage:
    python youtube_transcript.py "<url_or_id>" [--lang en] [--timestamps]
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_output_dir


def extract_video_id(url_or_id: str) -> str:
    """Extract video ID from URL or return as-is if already an ID."""
    patterns = [
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})",
        r"^([a-zA-Z0-9_-]{11})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
    return url_or_id


def slugify(text: str, max_len: int = 60) -> str:
    """Convert text to filesystem-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:max_len]


def get_video_metadata(video_id: str) -> dict:
    """Get video metadata via yt-dlp Python API (no API key needed)."""
    try:
        import yt_dlp

        ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )
            return {
                "title": data.get("title", "Unknown"),
                "channel": data.get("channel", data.get("uploader", "Unknown")),
                "duration": data.get("duration", 0),
                "views": data.get("view_count", 0),
                "upload_date": data.get("upload_date", ""),
                "description": data.get("description", "")[:500],
                "url": f"https://www.youtube.com/watch?v={video_id}",
            }
    except Exception as e:
        print(f"[metadata] yt-dlp metadata failed: {e}", file=sys.stderr)

    return {
        "title": video_id,
        "channel": "Unknown",
        "duration": 0,
        "views": 0,
        "upload_date": "",
        "description": "",
        "url": f"https://www.youtube.com/watch?v={video_id}",
    }


def get_transcript_api(video_id: str, lang: str) -> list[dict] | None:
    """Get transcript using youtube-transcript-api."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        ytt_api = YouTubeTranscriptApi()
        transcript = ytt_api.fetch(video_id, languages=[lang, "en"])

        entries = []
        for snippet in transcript.snippets:
            entries.append(
                {
                    "start": snippet.start,
                    "duration": snippet.duration,
                    "text": snippet.text,
                }
            )
        return entries

    except Exception as e:
        print(f"[transcript-api] Failed: {e}", file=sys.stderr)
        return None


def get_transcript_ytdlp(video_id: str, lang: str) -> list[dict] | None:
    """Fallback: get transcript using yt-dlp Python API."""
    print(f"[yt-dlp] Attempting subtitle extraction for {video_id}", file=sys.stderr)
    try:
        import tempfile
        import yt_dlp

        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": [lang],
                "subtitlesformat": "json3",
                "outtmpl": f"{tmpdir}/%(id)s.%(ext)s",
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])

            # Find the subtitle file
            tmppath = Path(tmpdir)
            sub_files = list(tmppath.glob("*.json3")) + list(tmppath.glob("*.json"))

            if not sub_files:
                # Retry with vtt
                ydl_opts["subtitlesformat"] = "vtt"
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
                sub_files = list(tmppath.glob("*.vtt"))

            if not sub_files:
                print("[yt-dlp] No subtitle files found", file=sys.stderr)
                return None

            content = sub_files[0].read_text(encoding="utf-8")

            # Parse json3 format
            if sub_files[0].suffix in (".json3", ".json"):
                data = json.loads(content)
                entries = []
                for event in data.get("events", []):
                    if "segs" in event:
                        text = "".join(seg.get("utf8", "") for seg in event["segs"]).strip()
                        if text:
                            entries.append(
                                {
                                    "start": event.get("tStartMs", 0) / 1000,
                                    "duration": event.get("dDurationMs", 0) / 1000,
                                    "text": text,
                                }
                            )
                return entries if entries else None

            # Parse VTT format
            entries = []
            for block in content.split("\n\n"):
                lines = block.strip().split("\n")
                for i, line in enumerate(lines):
                    if "-->" in line:
                        text = " ".join(lines[i + 1:]).strip()
                        text = re.sub(r"<[^>]+>", "", text)  # strip HTML tags
                        if text:
                            time_parts = line.split("-->")[0].strip().split(":")
                            if len(time_parts) == 3:
                                h, m, s = time_parts
                                start = int(h) * 3600 + int(m) * 60 + float(s)
                            elif len(time_parts) == 2:
                                m, s = time_parts
                                start = int(m) * 60 + float(s)
                            else:
                                start = 0
                            entries.append({"start": start, "duration": 0, "text": text})
            return entries if entries else None

    except Exception as e:
        print(f"[yt-dlp] Subtitle extraction failed: {e}", file=sys.stderr)
        return None


def format_timestamp(seconds: float) -> str:
    """Format seconds to HH:MM:SS or MM:SS."""
    s = int(seconds)
    h, remainder = divmod(s, 3600)
    m, sec = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def deduplicate_entries(entries: list[dict]) -> list[dict]:
    """Remove duplicate/overlapping transcript entries."""
    if not entries:
        return entries
    seen = set()
    deduped = []
    for entry in entries:
        text = entry["text"].strip()
        if text and text not in seen:
            seen.add(text)
            deduped.append(entry)
    return deduped


def save_transcript_markdown(
    video_id: str,
    metadata: dict,
    entries: list[dict],
    with_timestamps: bool,
) -> Path:
    """Save transcript as markdown with YAML frontmatter."""
    output_dir = get_output_dir()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title_slug = slugify(metadata.get("title", video_id))
    filename = f"transcript-{title_slug}-{date_str}.md"
    filepath = output_dir / filename

    # Build word count
    full_text = " ".join(e["text"] for e in entries)
    word_count = len(full_text.split())

    lines = [
        "---",
        f'title: "{metadata.get("title", "")}"',
        f'channel: "{metadata.get("channel", "")}"',
        f"video_id: {video_id}",
        f"url: {metadata.get('url', '')}",
        f"upload_date: {metadata.get('upload_date', '')}",
        f"transcript_date: {date_str}",
        f"word_count: {word_count}",
        "type: youtube-transcript",
        "---",
        "",
        f"# {metadata.get('title', video_id)}",
        "",
        f"**Channel:** {metadata.get('channel', 'Unknown')}",
        f"**URL:** {metadata.get('url', '')}",
        f"**Words:** {word_count:,}",
        "",
        "---",
        "",
        "## Transcript",
        "",
    ]

    if with_timestamps:
        for entry in entries:
            ts = format_timestamp(entry["start"])
            lines.append(f"**[{ts}]** {entry['text']}")
            lines.append("")
    else:
        # Group into paragraphs (~every 5 entries)
        paragraph = []
        for i, entry in enumerate(entries):
            paragraph.append(entry["text"])
            if len(paragraph) >= 5:
                lines.append(" ".join(paragraph))
                lines.append("")
                paragraph = []
        if paragraph:
            lines.append(" ".join(paragraph))
            lines.append("")

    filepath.write_text("\n".join(lines), encoding="utf-8")
    return filepath


def main():
    parser = argparse.ArgumentParser(description="Extract YouTube video transcript")
    parser.add_argument("url_or_id", help="YouTube video URL or ID")
    parser.add_argument("--lang", default="en", help="Transcript language (default: en)")
    parser.add_argument(
        "--timestamps", action="store_true", help="Include timestamps in output"
    )
    args = parser.parse_args()

    video_id = extract_video_id(args.url_or_id)
    print(f"Video ID: {video_id}", file=sys.stderr)

    # Get metadata
    print("Fetching metadata...", file=sys.stderr)
    metadata = get_video_metadata(video_id)
    print(f"Title: {metadata['title']}", file=sys.stderr)

    # Get transcript — try API first, then yt-dlp
    print("Extracting transcript...", file=sys.stderr)
    entries = get_transcript_api(video_id, args.lang)
    source = "youtube-transcript-api"

    if entries is None:
        source = "yt-dlp"
        entries = get_transcript_ytdlp(video_id, args.lang)

    if not entries:
        print("ERROR: Could not extract transcript. Video may not have captions.", file=sys.stderr)
        sys.exit(1)

    # Deduplicate
    entries = deduplicate_entries(entries)

    # Save
    filepath = save_transcript_markdown(video_id, metadata, entries, args.timestamps)
    word_count = sum(len(e["text"].split()) for e in entries)

    print(f"\nSource: {source}")
    print(f"Title: {metadata['title']}")
    print(f"Channel: {metadata['channel']}")
    print(f"Entries: {len(entries)}")
    print(f"Words: {word_count:,}")
    print(f"Saved: {filepath}")


if __name__ == "__main__":
    main()
