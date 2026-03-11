#!/usr/bin/env python3
"""
Push YouTube video or local transcript to Google NotebookLM.

Usage:
    python youtube_to_notebook.py "<url_or_filepath>" [--notebook "name"]
"""

import argparse
import subprocess
import sys
from pathlib import Path


def is_youtube_url(value: str) -> bool:
    """Check if value looks like a YouTube URL."""
    return any(
        domain in value
        for domain in ["youtube.com", "youtu.be", "youtube-nocookie.com"]
    )


def notebooklm_add_source(source: str, notebook_name: str | None = None) -> bool:
    """Add a source to NotebookLM using the CLI."""
    cmd = ["notebooklm", "source", "add", source]

    if notebook_name:
        cmd.extend(["--notebook", notebook_name])

    print(f"Running: {' '.join(cmd)}", file=sys.stderr)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        print(proc.stdout)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)
        return proc.returncode == 0
    except FileNotFoundError:
        # Try with full path on Windows
        try:
            import shutil

            notebooklm_path = shutil.which("notebooklm")
            if not notebooklm_path:
                # Common Windows pip install location
                notebooklm_path = str(
                    Path.home()
                    / "AppData/Roaming/Python/Python312/Scripts/notebooklm.exe"
                )
            cmd[0] = notebooklm_path
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            print(proc.stdout)
            if proc.stderr:
                print(proc.stderr, file=sys.stderr)
            return proc.returncode == 0
        except Exception as e:
            print(f"ERROR: notebooklm CLI not found. Run 'notebooklm login' first.", file=sys.stderr)
            print(f"Details: {e}", file=sys.stderr)
            return False
    except subprocess.TimeoutExpired:
        print("ERROR: NotebookLM operation timed out (120s)", file=sys.stderr)
        return False
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Push to Google NotebookLM")
    parser.add_argument(
        "source",
        help="YouTube URL or local file path to push to NotebookLM",
    )
    parser.add_argument(
        "--notebook",
        default=None,
        help="Notebook name to add source to (creates if needed)",
    )
    args = parser.parse_args()

    source = args.source

    # If it's a local file, verify it exists
    if not is_youtube_url(source):
        filepath = Path(source)
        if not filepath.exists():
            print(f"ERROR: File not found: {source}", file=sys.stderr)
            sys.exit(1)
        source = str(filepath.resolve())
        print(f"Adding local file: {source}", file=sys.stderr)
    else:
        print(f"Adding YouTube URL: {source}", file=sys.stderr)

    if args.notebook:
        print(f"Target notebook: {args.notebook}", file=sys.stderr)

    success = notebooklm_add_source(source, args.notebook)

    if success:
        print("\nSource added to NotebookLM successfully.")
    else:
        print("\nFailed to add source to NotebookLM.", file=sys.stderr)
        print("Make sure you've run 'notebooklm login' first.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
