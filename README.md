# 🎧 F4 Music Bot

A Telegram music bot that streams YouTube audio into group voice chats, searches with tap-to-pick results, and sends MP3s/videos in private chat. The bot account handles commands; a separate Telegram **assistant user session** publishes audio into calls (Bot API accounts cannot).

## Quick start (Python 3.12+)

```bash
python3 -m venv .venv && source .venv/bin/activate     # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cp .env.example .env                                   # Windows: Copy-Item .env.example .env
python bot.py
```

Fill in `API_ID`, `API_HASH`, `BOT_TOKEN` in `.env`. On the first `/play` Telegram asks for the assistant account's phone, code and 2FA password. FFmpeg must be installed for MP3/video downloads. Installing Deno or Node.js helps yt-dlp with YouTube.

## What's new

- **Live player card** with thumbnail, progress bar, loop/volume status and requester; a fresh card appears automatically when the next song starts, and the old one collapses to "✅ Played".
- **One-tap controls**: pause/resume toggle, skip, stop, loop (off/track/queue), shuffle, volume ±, queue, refresh.
- **Interactive queue editor** with per-song ✖ remove, shuffle and clear.
- **Search with results** (`/search`, or just message the bot in private) and **history** with one-tap replay.
- **Playlist links** and `/auto` that builds a queue from YouTube's mix of related songs.
- **Auto-join**: the assistant joins the group by invite link when possible.
- Streams are resolved when a song *starts*, so long queues never hit expired URLs; unplayable queued songs are skipped with a notice.
- Download progress, a download queue, cooldowns, max song length, owner list and `/broadcast`.
- Robust HTML formatting (song titles with `_ * \`` no longer break messages).

## Commands

| Command | Use |
| --- | --- |
| `/start`, `/help` | Interactive menu |
| `/play <song or link>` | Play or queue (reply to a message to use its text) |
| `/search <song>` | Pick from 5 results |
| `/auto <song>` | Queue the song plus related tracks |
| `/now`, `/queue` | Player card, queue editor |
| `/pause`, `/resume`, `/skip`, `/stop`, `/leave` | Playback control |
| `/loop [off\|one\|all]`, `/shuffle`, `/volume <1-200>` | Playback modes |
| `/remove <n>`, `/clear`, `/history` | Queue tools |
| `/send <song>`, `/video [720p] <song>` | Download (needs `PRIVATE_DOWNLOADS=true`) |
| `/setup`, `/ping`, `/stats` | Info |
| `/broadcast <text>` | Owner/admin broadcast to registered users and groups |
| `/panel` | Owner/admin management panel (private chat) |
| `/balance`, `/redeem <code>` | Credits for private-chat searches/downloads |
| `/claim [plan/amount]` | Submit a payment claim for owner/admin review |
| `/premium <user_id> <1d\|2d\|7d\|perma>` | Owner/admin premium access |
| `/addcredits <user_id> <amount>` | Owner/admin payment verification and credit grant |
| `/code <credits> [uses]` | Owner/admin redeem-code generation |
| `/setqr` | Owner sets the payment QR by replying to an image |
| `/logs [user_id]` | Owner usage logs |
| `/broadcastgc <chat_id> <text>` | Owner/admin group-only broadcast; reply to any media to copy it |

Old aliases (`/gplay`, `/gqueue`, `/gskip`, …) still work. Controls work for people in the voice chat, group admins, the song's requester, and owners.

## Telegram setup

1. Create an API ID/hash at `my.telegram.org` and a bot token with `@BotFather`.
2. Add the bot to the group and promote it with **Manage video chats** and **Invite users**.
3. Start the voice chat, then `/play`. If the assistant can't auto-join, add it to the group once.

## Configuration

See `.env.example`; every option is commented. Notable: `OWNER_IDS`, `SUPEROWNER_IDS`, `MAX_QUEUE`, `MAX_DURATION_MINUTES`, `COOKIES_FILE` or `COOKIES_FROM_BROWSER=chrome` (fixes YouTube "confirm you're not a bot"), and `CLEANUP_SESSIONS_ON_EXIT=false` to stay logged in. Private users receive 10 daily credits; group searches remain unlimited after the user starts the bot in DM and passes configured force-join checks.

If YouTube asks for sign-in, log into YouTube in Chrome/Edge/Firefox, close that browser, set `COOKIES_FROM_BROWSER=chrome` in `.env`, and restart the bot. Alternatively export a Netscape `cookies.txt` file and set `COOKIES_FILE`.

## Hosting

The repository includes a `Dockerfile`, `docker-compose.yml`, and `Procfile`.

For Docker Compose, keep your existing `.env` in the project directory and run:

```bash
docker compose up -d --build
```

The Compose volume keeps `musicbot.sqlite3`, Telegram session files, downloads, and the Song Studio cache persistent. Do not remove your existing `.env` values. For a worker-based host, use the `Procfile` command `python bot.py` and configure the same environment variables from `.env` in the host dashboard.

Before starting on a new host, copy the logged-in assistant session if one already exists, or run the first start from an interactive console so Pyrogram can complete the assistant login. Set `CLEANUP_SESSIONS_ON_EXIT=false` for persistent hosting.

## Layout

```text
bot.py              # launcher
musicbot/
  config.py         # environment configuration
  handlers.py       # commands, cards, buttons
  main.py           # clients, lifecycle, instance lock
  media.py          # yt-dlp search / stream / downloads
  state.py          # SQLite queue, history, users
  voice.py          # PyTgCalls playback engine
```

Use media you are authorized to play or download. Generated files (`.venv`, sessions, `musicbot.sqlite3`, downloads) are git-ignored.
