# YouTube Research Toolkit

Search YouTube, extract full transcripts, and push content to Google NotebookLM for AI-powered research. Built as a Claude Code skill but works standalone too.

## What It Does

- **Search YouTube** - Find videos by query, saves results as organized markdown files
- **Extract Transcripts** - Pull full transcripts from any YouTube video with captions
- **Push to NotebookLM** - Send videos or transcript files to Google NotebookLM notebooks

All scripts fall back to yt-dlp when no YouTube API key is configured. Zero config needed to get started.

## Setup

### Requirements

- Python 3.10+
- yt-dlp (required)
- youtube-transcript-api (recommended, for faster transcript extraction)
- google-api-python-client (optional, for YouTube Data API v3)

```bash
pip install -r requirements.txt
```

### Configuration

Copy the example config and customize:

```bash
cp config.example.json config.json
```

```json
{
  "youtube_api_key": "",
  "output_dir": "",
  "default_language": "en",
  "max_results": 10
}
```

- `youtube_api_key` - Optional. See [references/api-setup.md](references/api-setup.md) for setup instructions. Scripts work without it via yt-dlp.
- `output_dir` - Where to save markdown files. Defaults to `~/youtube-research-output/` if empty.
- `default_language` - Language code for transcripts (default: `en`).
- `max_results` - Default number of search results.

## Usage

### Search YouTube

```bash
python scripts/youtube_search.py "startup marketing strategy" --max-results 10
```

Output: Saves a markdown file with video titles, channels, view counts, durations, and descriptions.

```bash
# JSON output for programmatic use
python scripts/youtube_search.py "startup marketing" --json
```

### Extract Transcript

```bash
python scripts/youtube_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID"

# With timestamps
python scripts/youtube_transcript.py "VIDEO_ID" --timestamps

# Different language
python scripts/youtube_transcript.py "VIDEO_ID" --lang es
```

Output: Saves a markdown file with full transcript, video metadata, and word count.

### Push to NotebookLM

Requires the [notebooklm-py](https://github.com/teng-lin/notebooklm-py) CLI:

```bash
pip install notebooklm-py
notebooklm login  # One-time browser auth
```

```bash
# Add a YouTube video directly
python scripts/youtube_to_notebook.py "https://youtube.com/watch?v=VIDEO_ID" --notebook "My Research"

# Add a local transcript file
python scripts/youtube_to_notebook.py "./transcript-file.md" --notebook "My Research"
```

## Using as a Claude Code Skill

To install as a Claude Code skill, copy this folder to `~/.claude/skills/youtube-research/`. The `SKILL.md` file tells Claude Code how to use the scripts via natural language.

Triggers:
- `/youtube-research search "query"`
- `/youtube-research transcript <url>`
- `/youtube-research notebook <url> --notebook "name"`
- Or just say "search YouTube for..." and Claude Code picks it up.

## Output Format

All output files are markdown with YAML frontmatter:

**Search results:**
```markdown
---
query: "startup marketing"
date: 2026-03-11
result_count: 10
type: youtube-search
---
```

**Transcripts:**
```markdown
---
title: "Video Title"
channel: "Channel Name"
video_id: abc123
url: https://youtube.com/watch?v=abc123
word_count: 5000
type: youtube-transcript
---
```

## How It Works

1. **Search** tries YouTube Data API v3 first (if API key configured), falls back to yt-dlp
2. **Transcripts** try youtube-transcript-api first, fall back to yt-dlp subtitle extraction (json3/vtt)
3. **NotebookLM** integration uses the notebooklm-py CLI to push sources

## License

MIT
