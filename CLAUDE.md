# Notes for Claude Code

The owner directs the work and does not write code. Explain changes in plain English, keep the README current, and run `pytest -q` before every commit.

## Layout
- `auto_clip_poster/clipper.py`: runs `autoclip --data-dir D produce <src> --platform instagram_reels --json`, parses `outputs[].video_path/cover_path/post{title,description,tags}`. Exit 0 ok, 1 failed or partial, 2 bad input.
- `auto_clip_poster/postiz.py`: Postiz public API client. `GET /integrations`, `POST /upload` (multipart, `adjust`), `GET /media/{id}/status`, `POST /posts`. A single mp4 with `settings.post_type="post"` is published as a Reel.
- `auto_clip_poster/pipeline.py`: `run_once` (smallest slice), `run_cycle` (refill bank, fill next day's slots), `loop` (daily at RUN_AT).
- `auto_clip_poster/state.py`: `data/queue.txt` (owner-edited, append-only from code) and `data/state.json` (sources, bank, posts).
- `auto_clip_poster/scout/`: the GTA scout (`acp scout run|loop|report|check`). Never posts.
  - `sources.py`: Twitch Get Clips (app token, `/helix/games?name=`, `/helix/clips?game_id&started_at&ended_at&first=100&after`), YouTube channel RSS with a yt-dlp `--flat-playlist` fallback, `data/creators.txt` and `data/blocklist.txt`.
  - `rank.py`: filters (blocklist, leak/spoiler words, `SCOUT_NOT_BEFORE` unlock, lookback, language, topic keywords, min views), score, Twitch duplicate-moment removal, `moments_from_heatmap` (yt-dlp `heatmap` field).
  - `bank.py`: SQLite at `data/scout/scout.db`; `fetch.py`: yt-dlp downloads (YouTube via `download_ranges` around replay peaks); `runner.py`: one pass, loop, report.
  - `tests/fixtures/gta5_real_2026-11-10.json`: real GTA V Twitch clips and YouTube uploads read on Nov 10, 2026, replayed in `tests/test_scout.py`.
- `vendor/postiz-docker-compose`: official Postiz self-host compose, as a git submodule.

## Rules
- Never copy Postiz source into this repo (AGPL). Talk to it over HTTP only.
- AutoClip is pinned in `pyproject.toml`. Before bumping it, rerun `autoclip produce --help` and check the JSON shape in `tests/conftest.py::autoclip_json` still matches.
- Never commit `.env`, keys, tokens, `data/` or videos.
- Default `POST_MODE` stays `draft`. Never post for the owner as a test; use `DRY_RUN=true`.
- Docs: AutoClip `docs/CLI_AND_MCP.md` in its repo; Postiz https://docs.postiz.com/public-api/introduction
