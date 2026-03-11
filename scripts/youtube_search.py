#!/usr/bin/env python3
"""
YouTube search — API v3 primary, yt-dlp fallback.

Usage:
    python youtube_search.py "<query>" [--max-results 10] [--json]
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent scripts dir for config import
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_api_key, get_output_dir, load_config


def slugify(text: str, max_len: int = 50) -> str:
    """Convert text to filesystem-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:max_len]


def format_duration(seconds: int) -> str:
    """Format seconds to HH:MM:SS or MM:SS."""
    if seconds < 0:
        return "unknown"
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_views(count) -> str:
    """Format view count with commas."""
    try:
        return f"{int(count):,}"
    except (ValueError, TypeError):
        return str(count)


def search_youtube_api(query: str, max_results: int) -> list[dict]:
    """Search using YouTube Data API v3."""
    api_key = get_api_key()
    if not api_key:
        return []

    try:
        from googleapiclient.discovery import build

        youtube = build("youtube", "v3", developerKey=api_key)

        # Search for videos
        search_response = (
            youtube.search()
            .list(q=query, part="snippet", type="video", maxResults=max_results)
            .execute()
        )

        if not search_response.get("items"):
            return []

        video_ids = [item["id"]["videoId"] for item in search_response["items"]]

        # Get detailed video info
        videos_response = (
            youtube.videos()
            .list(id=",".join(video_ids), part="snippet,contentDetails,statistics")
            .execute()
        )

        results = []
        for item in videos_response.get("items", []):
            snippet = item["snippet"]
            stats = item.get("statistics", {})
            duration_iso = item.get("contentDetails", {}).get("duration", "")

            # Parse ISO 8601 duration
            duration_sec = 0
            dur_match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_iso)
            if dur_match:
                h = int(dur_match.group(1) or 0)
                m = int(dur_match.group(2) or 0)
                s = int(dur_match.group(3) or 0)
                duration_sec = h * 3600 + m * 60 + s

            results.append(
                {
                    "title": snippet.get("title", ""),
                    "channel": snippet.get("channelTitle", ""),
                    "url": f"https://www.youtube.com/watch?v={item['id']}",
                    "video_id": item["id"],
                    "duration": format_duration(duration_sec),
                    "duration_seconds": duration_sec,
                    "views": format_views(stats.get("viewCount", 0)),
                    "publish_date": snippet.get("publishedAt", "")[:10],
                    "description": snippet.get("description", "")[:300],
                }
            )

        return results

    except Exception as e:
        print(f"[API] YouTube API search failed: {e}", file=sys.stderr)
        return []


def search_ytdlp(query: str, max_results: int) -> list[dict]:
    """Fallback search using yt-dlp Python API."""
    print(f"[yt-dlp] Searching for: {query}", file=sys.stderr)
    try:
        import yt_dlp

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "skip_download": True,
        }

        results = []
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_results = ydl.extract_info(
                f"ytsearch{max_results}:{query}", download=False
            )

            if not search_results or "entries" not in search_results:
                return []

            for data in search_results["entries"]:
                if data is None:
                    continue
                results.append(
                    {
                        "title": data.get("title", "Unknown"),
                        "channel": data.get("channel", data.get("uploader", "Unknown")),
                        "url": data.get("webpage_url", data.get("url", "")),
                        "video_id": data.get("id", ""),
                        "duration": format_duration(data.get("duration", -1) or -1),
                        "duration_seconds": data.get("duration", 0) or 0,
                        "views": format_views(data.get("view_count", 0)),
                        "publish_date": (data.get("upload_date", "") or "")[:10],
                        "description": (data.get("description", "") or "")[:300],
                    }
                )

        return results

    except Exception as e:
        print(f"[yt-dlp] Search error: {e}", file=sys.stderr)
        return []


def save_results_markdown(query: str, results: list[dict]) -> Path:
    """Save search results as markdown with YAML frontmatter."""
    output_dir = get_output_dir()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = slugify(query)
    filename = f"search-{slug}-{date_str}.md"
    filepath = output_dir / filename

    lines = [
        "---",
        f'query: "{query}"',
        f"date: {date_str}",
        f"result_count: {len(results)}",
        "type: youtube-search",
        "---",
        "",
        f"# YouTube Search: {query}",
        "",
        f"*{len(results)} results — {date_str}*",
        "",
    ]

    for i, r in enumerate(results, 1):
        lines.extend(
            [
                f"## {i}. {r['title']}",
                "",
                f"- **Channel:** {r['channel']}",
                f"- **Duration:** {r['duration']}",
                f"- **Views:** {r['views']}",
                f"- **Published:** {r['publish_date']}",
                f"- **URL:** {r['url']}",
                "",
            ]
        )
        if r["description"]:
            lines.extend([f"> {r['description']}", ""])
        lines.append("---")
        lines.append("")

    filepath.write_text("\n".join(lines), encoding="utf-8")
    return filepath


def main():
    parser = argparse.ArgumentParser(description="Search YouTube videos")
    parser.add_argument("query", help="Search query")
    parser.add_argument(
        "--max-results", type=int, default=None, help="Max results (default from config)"
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    config = load_config()
    max_results = args.max_results or config.get("max_results", 10)

    # Try API first, fall back to yt-dlp
    results = search_youtube_api(args.query, max_results)
    source = "YouTube API"

    if not results:
        source = "yt-dlp"
        results = search_ytdlp(args.query, max_results)

    if not results:
        print("No results found.", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        filepath = save_results_markdown(args.query, results)
        print(f"Source: {source}")
        print(f"Found: {len(results)} results")
        print(f"Saved: {filepath}")

        # Print summary to stdout
        print()
        for i, r in enumerate(results, 1):
            print(f"{i}. {r['title']}")
            print(f"   {r['channel']} | {r['duration']} | {r['views']} views")
            print(f"   {r['url']}")
            print()


if __name__ == "__main__":
    main()
