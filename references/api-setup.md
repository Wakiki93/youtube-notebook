# YouTube Data API v3 — Setup Guide

The YouTube API key is **optional**. All scripts fall back to yt-dlp when no key is configured.
Adding an API key gives you faster, more reliable search results with full metadata.

## Steps

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select existing)
3. Enable "YouTube Data API v3" from the API Library
4. Go to Credentials → Create Credentials → API Key
5. Copy the key and paste it into `~/.claude/skills/youtube-research/config.json`:

```json
{
  "youtube_api_key": "YOUR_KEY_HERE"
}
```

## Quota
- Free tier: 10,000 units/day
- A search costs 100 units → ~100 searches/day
- A video details call costs 1 unit
- When quota is exhausted, scripts automatically fall back to yt-dlp

## Security
- Never commit API keys to git
- The config.json is in your user-level Claude config, not in any repo
