# auto-clip-poster

Turns long YouTube videos into captioned Instagram Reels and posts one or more every day, on its own.

```
queue.txt (your links)
   │
   ▼
AutoClip ── finds the best moments, cuts 9:16 Reels with burned-in captions, writes the caption copy
   │
   ▼
clip bank (data/state.json) ── Reels waiting for a slot
   │
   ▼
Postiz ── schedules each Reel through Instagram's official API at your POST_TIMES
```

It joins two open-source projects without copying their code:

| Piece | Project | How it's pulled in |
|---|---|---|
| Clipping | [AutoClip](https://github.com/zhouxiaoka/autoclip) (MIT) | Installed as its official package `autoclip-mcp`, pinned to 1.5.7.post1. We run its `autoclip produce` command. |
| Posting | [Postiz](https://github.com/gitroomhq/postiz-app) (AGPL-3.0) | Runs as its own service (Postiz Cloud, or the official Docker setup in `vendor/postiz-docker-compose`). We only call its public API. |

Keeping Postiz as a separate service matters: copying its AGPL source into this repo would put this whole repo under the AGPL.

## Words you'll see

- **Queue**: `data/queue.txt`, the list of long videos to clip. You add links; the loop works through them in order.
- **Clip bank**: Reels already made but not yet posted. The loop only clips a new video when the bank runs low.
- **Slot**: one posting time on one day, from `POST_TIMES`. Each slot gets exactly one Reel.
- **Cycle**: one run of the loop: top up the bank, then fill the next day's open slots. It runs daily at `RUN_AT`.
- **Draft mode**: posts land in the Postiz calendar for you to approve. Nothing goes live until you switch `POST_MODE=schedule`.
- **Dry run**: does everything except upload and post, and prints what it would have posted.

## Your checklist (things only you can do)

1. **Instagram**: make the new account a **Professional account** (Creator or Business). Instagram only allows posting through its official API for professional accounts.
2. **Postiz**: pick one.
   - **Postiz Cloud (fastest)**: sign up at postiz.com, click *Add Channel*, then *Instagram*, and log in. No Meta developer app needed.
   - **Self-hosted (free, more work)**: you need a Meta developer app (`FACEBOOK_APP_ID`/`FACEBOOK_APP_SECRET` or the Instagram standalone pair) and a **public** web address for Postiz's uploads, because Instagram downloads each video from a public link. A laptop at `localhost` will not work for Instagram. Follow [Postiz's Instagram guide](https://docs.postiz.com/providers/instagram) and use Cloudflare R2 storage.
3. **Postiz API key**: Postiz, then *Settings*, *Developers*, *Public API*. Paste it into `.env` as `POSTIZ_API_KEY`.
4. **AI model key for AutoClip**: AutoClip needs one AI model to judge which moments are worth clipping. An OpenAI key with `gpt-4o-mini` is cheap and works. Gemini and DeepSeek work too.
5. **Docker Desktop** installed and running.

## Set up (about 15 minutes)

```bash
git clone --recurse-submodules <this repo's URL> auto-clip-poster
cd auto-clip-poster
cp .env.example .env          # then fill in POSTIZ_API_KEY and your AI key
docker compose build
docker compose run --rm clipper check
```

`check` posts nothing. It confirms AutoClip, ffmpeg and your Postiz Instagram channel are all reachable. Fix anything marked `[FAIL]` before going on. (AutoClip's own doctor lines print in Chinese: ✓ means OK, ✗ means a problem.)

## Smallest slice first: one link, one Reel

```bash
docker compose run --rm clipper once "https://www.youtube.com/watch?v=VIDEO_ID" --credit "@original_creator"
```

This clips one video (expect 10 to 30 minutes), sends its best Reel to the next open slot, and banks the rest. With `POST_MODE=draft` the Reel shows up in your Postiz calendar as a draft. Open it, watch it, and publish it by hand. That's your proof that every piece works.

## Then turn on the daily loop

```bash
docker compose run --rm clipper add "https://www.youtube.com/watch?v=..." --credit "@creator"   # repeat for each video
docker compose up -d                                                                         # start the loop
docker compose logs -f clipper                                                               # watch it
docker compose run --rm clipper status                                                       # queue, bank, recent posts
```

Or edit `data/queue.txt` directly: one link per line, optionally `link | credit`. Lines starting with `#` are ignored.

Once you trust what the drafts look like, set `POST_MODE=schedule` in `.env` and restart with `docker compose up -d`. From then on Reels go live by themselves at `POST_TIMES`.

**How the loop decides:** every day at `RUN_AT`, if fewer Reels are banked than there are slots in a day, it clips the next new link in the queue. Then it fills the next day's open slots, one Reel each. A link that fails to clip gets one retry the next day, then is skipped (see `status`). A Reel that fails to post goes back to the front of the bank.

## The GTA scout (finds clips, never posts)

The scout runs on its own, every 15 minutes, and keeps a ranked list of the best new GTA clips. It is step 1 of the GTA 6 plan: it only finds, ranks and (if you turn it on) downloads. It never uploads or posts anything, and it doesn't need a graphics card.

Where it looks:
- **Twitch top clips** for the game's category, from Twitch's official API. Viewers already cut these, so the views tell us what's funny or wild. Needs a free Twitch developer app.
- **Your YouTube watchlist** in `data/creators.txt`: each channel's new uploads, from YouTube's free feed (no API key, no quota). For a YouTube video it never saves the whole thing; it reads the "Most replayed" graph and saves only the best 45 seconds around each peak.

What it skips, and why (`acp scout report` shows the count for each):
- Titles with leak or spoiler words (leak, datamined, spoiler, ending and more). Rockstar takes these down.
- Anything made before GTA 6 unlocks (11 PM Central, Nov 18). Pre-release footage gets taken down.
- YouTube titles that aren't about the game, clips older than a day, clips under 200 views, non-English Twitch clips.
- Anyone in `data/blocklist.txt`. Add every creator who files a claim or asks you to stop.
- The same moment clipped twice on Twitch: only the most-viewed copy is kept.

How it ranks: `20 x log10(1 + views) + 20 x log10(1 + views per hour)`. Views say people liked it; views per hour say it's hot right now. Scores only compare within one source, so downloads take turns between Twitch and YouTube.

### Rehearse on GTA V now

```bash
# in .env: SCOUT_PROFILE=gta5, plus TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET
docker compose build
docker compose run --rm scout scout check            # are Twitch and yt-dlp working?
docker compose run --rm scout scout run              # one pass: find and rank, no downloads
docker compose run --rm scout scout run --download   # same, and download the top 5
docker compose run --rm scout scout report           # the ranked list and what got skipped
```

Downloads land in `data/scout/clips/`, each with a `.json` file next to it saying who made it and where it came from (for credit later). They're deleted after 14 days.

### For launch

Set `SCOUT_PROFILE=gta6` and run `docker compose up -d scout`. It waits quietly until the game unlocks, then starts filling the list. Turn on `SCOUT_DOWNLOAD=true` once you've watched a few passes and like what it picks.

**Twitch keys (5 minutes, free):** Twitch requires two-factor sign-in on your Twitch account first (Settings, Security and Privacy). Then sign in at dev.twitch.tv/console, open *Applications*, choose *Register Your Application*, give it a unique name, add the OAuth Redirect URL `http://localhost:3000` (the scout never uses it, but the form needs one), pick a category such as *Application Integration*, and click *Create*. Open *Manage*, copy the Client ID, click *New Secret* and copy that too. Paste both into `.env`. Treat the secret like a password.

**The starter watchlist** in `data/creators.txt` is 8 big GTA channels so the scout has something to watch on day one. Being on that list is not permission to repost them. Edit it to the creators you want, and favor ones who allow clipping.

## Commands

| Command | What it does |
|---|---|
| `acp check` | Confirms everything is wired up. Posts nothing. |
| `acp once <link> [--credit X] [--count N]` | Clips one link and sends its best N Reels now. |
| `acp add <link> [--credit X]` | Adds a link to the queue. |
| `acp cycle` | Runs one daily cycle right now. |
| `acp loop` | Runs a cycle now, then every day at `RUN_AT`. This is what `docker compose up` runs. |
| `acp status` | Shows the queue, the bank, failures and recent posts. |

Inside Docker, prefix each with `docker compose run --rm clipper`, as in `docker compose run --rm clipper status`.

## Settings (`.env`)

All settings are explained in `.env.example`. The ones you'll touch most: `POST_MODE`, `POST_TIMES`, `TIMEZONE`, `CLIPS_PER_VIDEO`, `CAPTION_FOOTER`, `AUTOCLIP_INSTRUCTION` (plain-English steer, such as "favor practical advice"), `DRY_RUN`.

## Read this before you post other people's videos

- **Reach:** since April 30, 2026, Instagram keeps accounts that mainly repost other people's content out of recommendations (Explore, Reels feed). Watermarks and speed changes don't count as edits. Adding your own perspective does: commentary, a voiceover, a reaction. AutoClip's captions and reframing help, but on their own they may not be enough.
- **Copyright:** clipping someone else's video without permission can draw takedowns and strikes. Clip your own videos, creators who allow it, or content you have rights to, and credit the source with `--credit`.
- **Limits:** Instagram's API allows 100 posts per 24 hours per account. Postiz limits its create-post API to 90 calls an hour on self-hosted setups by default (100 on Postiz Cloud). Daily posting is far under both.

## Gotchas

- **YouTube may block server downloads** with "sign in to confirm you're not a bot". Running at home usually avoids it. Otherwise, download the video yourself and add the file path (`/data/videos/talk.mp4`) to the queue instead of the link.
- **Videos without captions** need local transcription (Whisper), which is in the default image. Build with `--build-arg WHISPER=0` for a smaller image if your sources always have captions.
- **Self-hosted Postiz on the same machine:** set `POSTIZ_API_URL=http://host.docker.internal:4007/api/public/v1`.
- **AutoClip telemetry is switched off**: the first run writes `privacy.json` with analytics and crash reports off.

## For Claude Code

See `CLAUDE.md`. Tests: `pip install -e ".[dev]" && pytest -q` (no keys or network needed; AutoClip and Postiz are mocked).
