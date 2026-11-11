"""Command line: `acp <command>`. Run `acp --help` for the list."""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys

from . import __version__
from .config import ConfigError, Settings
from .pipeline import Pipeline, next_slots
from .postiz import PostizClient, PostizError
from .state import State, append_queue, read_queue


def _settings() -> Settings:
    try:
        return Settings.from_env()
    except ConfigError as error:
        print(f"Setting problem: {error}", file=sys.stderr)
        raise SystemExit(2)


def cmd_once(args) -> int:
    s = _settings()
    report = Pipeline(s).run_once(args.url, credit=args.credit or "", count=args.count)
    print(report.summary())
    return 0 if report.posted else 1


def cmd_add(args) -> int:
    s = _settings()
    added = append_queue(s.queue_file, args.url, args.credit or "")
    print("Added to the queue." if added else "Already in the queue.")
    return 0


def cmd_cycle(args) -> int:
    print(Pipeline(_settings()).run_cycle().summary())
    return 0


def cmd_loop(args) -> int:
    Pipeline(_settings()).loop()
    return 0


def cmd_status(args) -> int:
    s = _settings()
    state = State(s.state_file)
    queue = read_queue(s.queue_file)
    done = state.data["sources"]
    waiting = [q.url for q in queue if q.url not in done]
    upcoming = next_slots(Pipeline(s).now(), s.post_times, s.timezone, len(s.post_times), state.taken_slots())
    out = {
        "mode": "DRY RUN" if s.dry_run else s.post_mode,
        "links_waiting": len(waiting),
        "links_done": sum(1 for v in done.values() if v.get("status") == "clipped"),
        "links_failed": {u: v.get("error") for u, v in done.items() if v.get("status") == "failed"},
        "clips_banked": len(state.bank),
        "posts_sent": len(state.data["posts"]),
        "last_posts": state.data["posts"][-5:],
        "next_open_slots": [slot.isoformat() for slot in upcoming],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_check(args) -> int:
    """Check every piece is wired up, without clipping or posting anything."""
    s = _settings()
    ok = True
    print(f"auto-clip-poster {__version__} | mode: {'DRY RUN' if s.dry_run else s.post_mode} | timezone: {s.timezone}")
    print(f"Post times: {', '.join(t.strftime('%H:%M') for t in s.post_times)} | daily run at {s.run_at:%H:%M}")

    exe = shutil.which(s.autoclip_bin)
    if not exe:
        print(f"[FAIL] AutoClip command '{s.autoclip_bin}' not found. Install it: pip install autoclip-mcp")
        ok = False
    else:
        version = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.strip()
        print(f"[ OK ] AutoClip found: {version}")
        if not args.skip_doctor:
            doctor = subprocess.run([exe, "--data-dir", str(s.autoclip_data_dir), "doctor"],
                                    capture_output=True, text=True)
            print("       autoclip doctor says:\n       " + (doctor.stdout + doctor.stderr).strip().replace("\n", "\n       "))
    if not shutil.which("ffmpeg"):
        print("[FAIL] ffmpeg is not installed")
        ok = False
    else:
        print("[ OK ] ffmpeg found")

    if s.dry_run:
        print("[SKIP] Postiz check skipped (DRY_RUN is on)")
    else:
        try:
            s.require_postiz()
            channel = PostizClient(s.postiz_api_url, s.postiz_api_key).instagram_channel(s.postiz_integration_id)
            print(f"[ OK ] Postiz connected; Instagram channel: {channel.get('profile') or channel.get('name')} ({channel.get('id')})")
        except (ConfigError, PostizError) as error:
            print(f"[FAIL] Postiz: {error}")
            ok = False

    queue = read_queue(s.queue_file)
    print(f"[INFO] {len(queue)} link(s) in {s.queue_file}")
    return 0 if ok else 1


def _scout_settings():
    from .scout.config import ScoutSettings
    try:
        return ScoutSettings.from_env()
    except ConfigError as error:
        print(f"Setting problem: {error}", file=sys.stderr)
        raise SystemExit(2)


def cmd_scout(args) -> int:
    """Find and rank clips on Twitch and YouTube. Never posts."""
    from .scout.runner import Scout, check_text, report_text
    s = _scout_settings()
    if args.action == "check":
        ok, text = check_text(s)
        print(text)
        return 0 if ok else 1
    if args.action == "report":
        print(report_text(s, args.top))
        return 0
    scout = Scout(s)
    if args.action == "loop":
        scout.loop()
        return 0
    report = scout.run_once(download=True if args.download else None)
    print(report.summary())
    print()
    print(report_text(s, args.top))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="acp", description="Clip long videos into Reels and post them to Instagram via Postiz.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="show detailed logs")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("once", help="smallest slice: clip one link and send its best Reel to the next slot")
    p.add_argument("url")
    p.add_argument("--credit", help="who made the original, shown in the caption")
    p.add_argument("--count", type=int, default=1, help="how many of its Reels to send now (rest are banked)")
    p.set_defaults(func=cmd_once)

    p = sub.add_parser("add", help="add a link to queue.txt")
    p.add_argument("url")
    p.add_argument("--credit", help="who made the original, shown in the caption")
    p.set_defaults(func=cmd_add)

    sub.add_parser("cycle", help="run one daily cycle now (refill clips, fill the next day's slots)").set_defaults(func=cmd_cycle)
    sub.add_parser("loop", help="run a cycle now and then once a day at RUN_AT, forever").set_defaults(func=cmd_loop)
    sub.add_parser("status", help="show the queue, clip bank and recent posts").set_defaults(func=cmd_status)
    p = sub.add_parser("check", help="check AutoClip, ffmpeg and Postiz are set up (posts nothing)")
    p.add_argument("--skip-doctor", action="store_true", help="skip AutoClip's model and Whisper check")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("scout", help="find and rank the best new GTA clips on Twitch and YouTube (never posts)")
    p.add_argument("action", nargs="?", default="run", choices=["run", "loop", "report", "check"],
                   help="run: one pass now (default); loop: every SCOUT_INTERVAL_MINUTES; "
                        "report: show the ranked list; check: test keys and tools")
    p.add_argument("--download", action="store_true", help="also download the top clips this pass")
    p.add_argument("--top", type=int, default=15, help="how many to list in the report")
    p.set_defaults(func=cmd_scout)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        return args.func(args)
    except PostizError as error:
        print(f"Postiz problem: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
