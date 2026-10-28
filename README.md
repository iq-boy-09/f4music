<p align="center">
  <img src="assets/f4_music_banner.svg" alt="F4 Music animated banner" width="900">
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.12+"></a>
  <a href="https://core.telegram.org/bots/api"><img src="https://img.shields.io/badge/Telegram-Bot%20API-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" alt="Telegram Bot API"></a>
  <a href="https://www.docker.com/"><img src="https://img.shields.io/badge/Docker-ready-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker ready"></a>
</p>

# F4 Music Bot

F4 Music is a Telegram music bot for searching tracks, playing them in group voice chats, sending MP3s/videos in private chat, and managing access through credits, Premium, referrals, force-join, and an owner/admin panel.

<p align="center"><i>Search it. Queue it. Feel it.</i><br>✦ High-Res · Max-Bass · Zero-Lag ✦</p>

## About the creator

F4 Music is created and maintained by **[@f4did](https://t.me/f4did)**. If you reuse, deploy, modify, showcase, or build on this project, keep visible credit to **@f4did**. The bot’s media captions already include the credit automatically.

Please do not remove, hide, or replace the author credit when redistributing this project.

## What it includes

| Area | Included |
| --- | --- |
| Music | YouTube search, MP3, video, smart autoplay, Instagram Reels, Snapchat Spotlight, YouTube Shorts, and other yt-dlp-supported public links |
| Voice chat | Assistant-user streaming, priority group routing, queue, pause, resume, skip, loop, shuffle, volume |
| UI | Animated README banner, branded start cards, player thumbnail template, compact result buttons |
| Access | Daily credits at 00:01 IST, Premium, redeem codes, referrals, force-join verification |
| Management | Owner/admin panel, admin list, broadcasts, group broadcasts, usage logs, payment QR |
| Safety | Medium-quality downloads with size fallback, failed-download credit protection, cookie-less default, flood-safe progress edits |

## Screenshots and UI

The bot uses a branded F4 Music start card, inline search results, and a live player card with the current thumbnail. The animated banner above is stored locally at [`assets/f4_music_banner.svg`](assets/f4_music_banner.svg), so it remains available when the repository is mirrored.

## Requirements

- Python 3.12 or newer
- FFmpeg
- Telegram API ID and API hash from [my.telegram.org](https://my.telegram.org)
- Bot token from [@BotFather](https://t.me/BotFather)
- A normal Telegram assistant account for voice-chat playback
- A YouTube cookies file or browser-cookie setting if YouTube asks for verification

## 1. Windows installation

Open PowerShell in the project folder:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
winget install Gyan.FFmpeg.Shared
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

Copy `.env.example` to `.env`, fill in your values, and start:

```powershell
Copy-Item .env.example .env
python bot.py
```

Do not commit `.env`, Telegram session files, cookies, or the SQLite database. On the first voice-chat run, the configured owner is notified in the bot’s private chat when Telegram requests the assistant verification code; reply there with the code. If 2FA is enabled, the owner can reply with the password too.

## 2. Linux installation

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip ffmpeg build-essential
git clone YOUR_REPOSITORY_URL f4-music
cd f4-music
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
nano .env
python bot.py
```

For a long-running Linux server, use the included Docker setup or run the bot with `systemd`/`supervisord`. Keep the assistant session and `musicbot.sqlite3` on persistent storage.

## 3. Docker and Fadehost deployment

The repository includes [`Dockerfile`](Dockerfile), [`docker-compose.yml`](docker-compose.yml), and [`Procfile`](Procfile).

### Docker Compose

Keep your existing `.env` beside `docker-compose.yml`; its values are not replaced by the project:

```bash
docker compose up -d --build
docker compose logs -f f4-music
```

The Compose volume keeps the database, Telegram sessions, downloads, and Song Studio cache persistent.

### Fadehost or another Docker host

1. Create a new Docker application/service.
2. Upload or connect this repository.
3. Build from `Dockerfile`.
4. Add the same environment variables from your local `.env` in the host dashboard.
5. Attach persistent storage and mount it at `/app`, or configure `STATE_DB`, `DOWNLOAD_DIR`, `BOT_SESSION`, and `ASSISTANT_SESSION` to the mounted path.
6. Start the container with `python bot.py`.
7. Check logs and complete the assistant login once. The owner can receive and submit the verification code in the bot’s private chat, so an interactive host console is not required.

If the host is worker-based instead of Docker-based, use the Procfile process:

```text
python bot.py
```

## 4. Render deployment

Use a **Background Worker**, not a web service: this bot listens to Telegram. The included [`render.yaml`](render.yaml) is a Render Blueprint with a Docker build and persistent `/data` disk. For an already-created Web Service, the bot now exposes a small health endpoint on Render’s `PORT`, so the service can pass the port scan too.

### Blueprint method

1. Push the repository to GitHub/GitLab.
2. In Render, choose **New → Blueprint** and select the repository.
3. Confirm `render.yaml`.
4. Enter the secret values marked `sync: false`.
5. Deploy the worker.
6. Confirm the persistent disk is mounted at `/data`.
7. Watch the deploy logs and verify `Bot is live` plus the health-endpoint message if `PORT` is set.

### Dashboard method

1. Choose **New → Background Worker** and connect the repository.
2. Select Docker and use the repository `Dockerfile`.
3. Add your `.env` values under Environment. Render can bulk-import a valid `.env` file from the Environment page.
4. Add a persistent disk mounted at `/data`.
5. Set these persistent paths:

```env
STATE_DB=/data/musicbot.sqlite3
DOWNLOAD_DIR=/data/downloads
BOT_SESSION=/data/command_bot_live
ASSISTANT_SESSION=/data/voice_assistant
CLEANUP_SESSIONS_ON_EXIT=false
```

6. Deploy and inspect the worker logs. If using a Web Service instead, leave Render’s automatic `PORT` variable enabled; the bot binds to it automatically.

Render filesystems are ephemeral without a persistent disk, so sessions and SQLite data must live under `/data`. A disk is single-instance storage; do not scale this bot to multiple instances.

## YouTube verification and cookies

If YouTube shows `Sign in to confirm you’re not a bot`, use one of these options:

```env
# Computer with a logged-in browser:
COOKIES_FROM_BROWSER=chrome

# Render/Fadehost with a Netscape cookies file:
COOKIES_FILE=/data/cookies.txt
```

Close the browser before yt-dlp reads its cookie database. Never commit `cookies.txt`. See the official [yt-dlp cookie guide](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp).

Cookies are optional. The bot is cookie-less by default. An owner/admin can reply to a Netscape-format `cookies.txt` document with `/setcookies`; use `/delcookies` to remove the runtime cookie file. Cookies are stored under `DOWNLOAD_DIR/cookies.txt` and should never be shared.

## Telegram MTProto proxy

Managers can save a Telegram MTProto proxy from the bot:

```text
/setproxy HOST PORT SECRET
/delproxy
```

Restart the bot after changing the proxy. Proxy credentials are stored beside `STATE_DB` in `telegram_proxy.json`; keep that file private.

## Telegram setup checklist

1. Create an API ID/hash at [my.telegram.org](https://my.telegram.org).
2. Create a bot with [@BotFather](https://t.me/BotFather).
3. Add the bot to your group and grant the required admin permissions.
4. Add/configure the assistant account for voice playback.
5. Start a voice chat before using `/play`.
6. Add the Song Studio channel ID `-1003853614558` and make the bot an admin if you want MP3 caching.
7. Configure `OWNER_IDS` before using `/panel`.

The configured assistant account is kept in a separate session named `voice_assistant_8896291170`. If you move this bot from another assistant account, keep the new session name or remove the old session file before the first login.

If a host still sends a code to the previous number, update `ASSISTANT_PHONE`, `ASSISTANT_USER_ID`, and `ASSISTANT_SESSION` in that host’s Environment settings. Render dashboard variables override local `.env` files, and an old persistent session must be removed or renamed before starting the new login.

## Commands

### Visible command menu

`/start` · `/send` · `/video` · `/play` · `/playlist` · `/search` · `/ping` · `/help`

The full command list is intentionally inside `/help` so the Telegram command menu stays clean.

### User commands

| Command | Use |
| --- | --- |
| `/start` | Open the branded menu and verify force-join |
| `/search <song>` | Search five results and choose Play, Music, or Video |
| `/send <song or link>` | Send an MP3; one successful private result costs 1 credit by default |
| `/video [quality] <song or link>` | Send a video; costs 2 credits by default, defaults to 480p, and retries 360p if too large |
| `/play <song or link>` | Play or queue music in a group voice chat |
| `/balance` | View credits, Premium, admin, or owner status in private chat or groups |
| `/redeem <code>` | Redeem credits issued by management |
| `/refer` | View the referral link and referral rewards |
| `/help` | Show all available commands and usage |

### Group player commands

`/auto`, `/now`, `/queue`, `/pause`, `/resume`, `/skip`, `/stop`, `/leave`, `/loop`, `/shuffle`, `/volume`, `/remove`, `/clear`, `/history`

### Group favorite playlist

Group admins can manage the group’s Song Studio playlist:

```text
/playlist add SONG_OR_LINK
/playlist list
/playlist send NUMBER
/playlist remove NUMBER
/playlist clear
```

All members can view the playlist and use `/playlist send NUMBER`. Only group admins can add, remove, or clear songs. Playlist audio and group/member metadata are stored in the configured Song Studio channel; copied songs hide the original channel sender. Smart autoplay uses this playlist before choosing related tracks.

### Owner/admin commands

`/panel`, `/adminlist`, `/addadmin`, `/deladmin`, `/premium`, `/addcredits`, `/code`, `/forcejoin`, `/unforcejoin`, `/broadcast`, `/broadcastgc`, `/logs`, `/setqr`, `/setcookies`, `/delcookies`, `/setproxy`, `/delproxy`, `/setcost`, `/creditcosts`, `/setdaily`

Credit defaults are 1 credit for audio and 2 for video. Managers can change audio, video, search, and daily-refill values with `/setcost audio AMOUNT`, `/setcost video AMOUNT`, `/setcost search AMOUNT`, and `/setdaily AMOUNT`. The Credits section in `/panel` shows the current values.

Owner-only assistant commands are `/assistant`, `/setassistant PHONE USER_ID`, and `/clearassistant`. Assistant login verification is routed to the owner and includes the requesting group ID. The priority voice group is `-1004220556565`; other groups can use the assistant when that group is inactive.

Use `/help` inside Telegram for current descriptions and role-specific tools.

## Credits and Premium

- Private users receive 10 credits daily at 00:01 IST.
- A failed search/download does not deduct credits.
- Private-only commands show a short DM hint when used in a group. `/balance` is intentionally available directly in groups.
- Premium users bypass force-join checks.
- Groups have unlimited search and playback after verification.
- Premium users receive unlimited access and do not see purchase prompts.
- MP3s cached in Song Studio are forwarded quickly without re-downloading.
- Audio defaults to 128 kbps and retries at 96 kbps when needed.
- Progress cards refresh every 20 seconds for tracks longer than one minute and every 11 seconds for shorter tracks.

## Configuration

Every setting is documented in [`.env.example`](.env.example). Keep your existing `.env` values when updating the project. Important hosting values include:

```env
OWNER_IDS=your_telegram_user_id
PRIVATE_DOWNLOADS=true
CLEANUP_SESSIONS_ON_EXIT=false
```

Never publish `API_HASH`, `BOT_TOKEN`, assistant phone details, cookies, or session files. If a bot token has ever been posted publicly, revoke it with BotFather and create a replacement.

## Project layout

```text
bot.py                         # launcher
musicbot/handlers.py           # commands, cards, buttons, UI
musicbot/main.py               # clients and lifecycle
musicbot/media.py              # yt-dlp search, stream, downloads
musicbot/state.py              # SQLite users, credits, queue, cache
musicbot/voice.py              # PyTgCalls playback engine
assets/f4_music_banner.svg     # animated README banner
assets/player_template.png     # player card template
Dockerfile                     # production container
docker-compose.yml             # persistent local/container hosting
render.yaml                    # Render worker blueprint
Procfile                       # worker-host fallback
```

## Legal and attribution

Use only media you are authorized to play or download. Respect Telegram and YouTube rules. Keep the visible **@f4did** credit in every reuse or hosted copy of F4 Music.
