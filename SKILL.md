# YouTube Research Skill

## Description
Search YouTube for videos on any topic, extract full transcripts, save as organized markdown files, and push content to Google NotebookLM for AI-powered research. Supports research, learning, and content creation workflows.

## Triggers
- `/youtube-research` slash command
- Natural language: "search YouTube", "youtube search", "find youtube videos", "youtube transcript", "get transcript", "youtube to notebook", "push to notebooklm", "research on youtube"

## Usage

### Search YouTube
```
/youtube-research search <query> [--max N]
```
Search for videos matching a query. Saves results as markdown.

Examples:
- `/youtube-research search "fastapi tutorial"`
- `/youtube-research search "crypto tax 2026" --max 5`
- "search YouTube for IRS form 8949 crypto"

### Extract Transcript
```
/youtube-research transcript <url_or_video_id> [--timestamps] [--lang xx]
```
Extract full transcript from a YouTube video. Saves as markdown with metadata.

Examples:
- `/youtube-research transcript https://www.youtube.com/watch?v=dQw4w9WgXcQ`
- `/youtube-research transcript dQw4w9WgXcQ --timestamps`
- "get the transcript for this youtube video: <url>"

### Push to NotebookLM
```
/youtube-research notebook <url_or_filepath> [--notebook "name"]
```
Add a YouTube video or local transcript file to a Google NotebookLM notebook.

Examples:
- `/youtube-research notebook https://youtube.com/watch?v=abc123 --notebook "Tax Research"`
- `/youtube-research notebook "./transcript-some-video.md"`
- "push this to notebooklm"

## Instructions

When the user triggers this skill, determine which mode they want (search, transcript, or notebook) from context.

### For search:
```bash
python ~/.claude/skills/youtube-research/scripts/youtube_search.py "<query>" --max-results <N>
```

### For transcript:
```bash
python ~/.claude/skills/youtube-research/scripts/youtube_transcript.py "<url_or_id>" [--timestamps] [--lang <code>]
```

### For notebook:
```bash
python ~/.claude/skills/youtube-research/scripts/youtube_to_notebook.py "<url_or_filepath>" [--notebook "<name>"]
```

After running the script, read the saved markdown file and present a summary to the user. If the script errors, check the error message and suggest fixes (e.g., video has no transcript available, network issue, etc.).

## Allowed Tools
- `Bash(python *)`
- `Read`
- `Write`
- `Glob`

## Config
Config file at `~/.claude/skills/youtube-research/config.json`. YouTube API key is optional — all scripts fall back to yt-dlp when no key is configured.

## Output Location
All files saved to the directory specified in `config.json` (defaults to `~/youtube-research-output/`).
