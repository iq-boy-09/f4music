import asyncio
import base64
import html
import json
import logging
import math
import re
import secrets
import shutil
import tempfile
import time
import zlib
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pyrogram import ContinuePropagation, filters
from pyrogram.enums import ChatMemberStatus, ChatType, MessagesFilter
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import ChatPrivileges, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb

from .media import MediaError, YouTubeMedia, is_url
from .state import QueueItem, QueueStore
from .voice import LOOP_MODES, NoActiveCall, QueueFull, VoicePlayer

logger = logging.getLogger(__name__)

HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update({"User-Agent": "F4-Music-Bot/1.0"})
HTTP_SESSION.mount(
    "https://",
    HTTPAdapter(
        pool_connections=4,
        pool_maxsize=8,
        max_retries=Retry(
            total=2,
            backoff_factor=0.3,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        ),
    ),
)

GROUP_TYPES = (ChatType.GROUP, ChatType.SUPERGROUP)
LOOP_LABEL = {"off": "Off", "one": "Track", "all": "Queue"}
NUMBERS = ["1", "2", "3", "4", "5", "6", "7", "8"]
VIDEO_QUALITIES = (144, 240, 360, 480, 720, 1080)
DEFAULT_VIDEO_HEIGHT = 480
LOW_VIDEO_HEIGHT = 360
MAX_VIDEO_BYTES = 455 * 1024 * 1024
CREDIT_LINE = "<i>✦ F4 MUSIC · crafted by @f4did ✦</i>"
DOWNLOAD_CAPTION = "<i>✦ made by @f4did · F4 MUSIC ✦</i>"
PAYMENT_QR = Path(__file__).resolve().parent.parent / "assets" / "payment_qr.jpg"
STARTUP_IMAGE = Path(__file__).resolve().parent.parent / "assets" / "startup_image.jpg"
SONG_STUDIO_CHAT_ID = -1003853614558
GROUP_COMMANDS = [
    "play", "gplay", "p", "auto", "now", "queue", "gqueue", "skip", "gskip", "pause", "gpause",
    "resume", "gresume", "stop", "gstop", "leave", "gleave", "loop", "shuffle", "volume",
    "remove", "clear", "history", "playlist",
]

HOME_TEXT = (
    "╭━━━〔 𝐅𝟒 𝐌𝐔𝐒𝐈𝐂 ♪ 〕━━━╮\n"
    "┃  🎧 <b>WELCOME TO THE SOUNDWAVE</b>\n"
    "┃  ⚡ High-Res · Max-Bass · Zero-Lag\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
    "💬 <b>Private</b> · Search, MP3 and video\n"
    "🎁 <b>10 daily credits</b> · one successful result = one credit\n"
    "👥 <b>Groups</b> · unlimited voice playback and searches\n\n"
    "✦ Choose your path below ✦\n"
    + CREDIT_LINE
)


def help_text(in_group: bool, manager: bool = False) -> str:
    text = (
        "<b>F4 MUSIC HELP</b>\n\n"
        "<b>Everywhere</b>\n"
        "/start — open the music menu\n"
        "/send <i>song or link</i> — get an MP3\n"
        "/video <i>song or link</i> — get a video\n"
        "/play <i>song or link</i> — play in a group voice chat\n"
        "/ping — check response time\n"
        "/help — show this guide\n\n"
        "<b>Search and account</b>\n"
        "/search <i>song</i> — show five results\n"
        "Send a song name or link directly in private chat.\n"
        "/balance — view credits or premium status\n"
        "/redeem <i>code</i> — claim credits\n"
        "/refer — view your referral link\n\n"
    )
    if in_group:
        text += (
            "<b>Group player</b>\n"
            "/auto <i>song</i> — add related tracks\n"
            "/now — current track\n/queue — waiting tracks\n"
            "/pause · /resume · /skip · /stop · /leave\n"
            "/loop <i>off|one|all</i> · /shuffle · /volume <i>1-200</i>\n"
            "/remove <i>number</i> · /clear · /history\n"
            "/playlist <i>add|list|send|remove|clear</i> — group smart-next favorites\n"
        )
    else:
        text += "<b>Private chat</b>\nMP3 and video downloads use one credit per successful result. Groups have unlimited searches and playback.\n"
    if manager:
        text += (
            "\n<b>Manager tools</b>\n"
            "/panel · /adminlist · /addadmin · /deladmin · /premium\n"
            "/addcredits · /code · /forcejoin · /unforcejoin\n"
            "/broadcast · /broadcastgc · /logs · /setqr\n"
            "/setcookies · /delcookies · /setproxy · /delproxy\n"
        )
    return text + "\n" + CREDIT_LINE


def welcome_text(name: str, plan: str, power: str) -> str:
    """Personalized DM landing card shown by /start."""
    return "<b>" + (
        "╭━━━〔 𝐅𝟒 𝐌𝐔𝐒𝐈𝐂 ♪ 〕━━━╮\n"
        f"┃  👤 USER  ·  <b>{esc(name)}</b>\n"
        f"┃  💠 PLAN  ·  {plan}\n"
        f"┃  ⚡️ POWER ·  {power}\n"
        "╰━━━━━━━━━━━━━━╯\n"
        "◇ High-Res • Max-Bass • Zero-Lag ◇\n"
        "✦ ── ✦ ── ✦ ── ✦ ── ✦\n"
        "🚀 <b> Enter The Soundwave Below</b>\n"
        "✦ ── ✦ ── ✦ ── ✦ ── ✦\n"
        + CREDIT_LINE
    ) + "</b>"


def group_welcome_text(title: str) -> str:
    return (
        "<b>╭━━━〔 𝐅𝟒 𝐌𝐔𝐒𝐈𝐂 ♪ 〕━━━╮\n"
        "┃ 🎧 GROUP PLAYER READY\n"
        f"┃ 👥 GC  ·  {esc(title or 'This group')}\n"
        "┃ ⚡ POWER · ♾️ UNLIMITED\n"
        "╰━━━━━━━━━━━━━━╯\n"
        "Start a voice chat, then use /play <i>song</i>.\n"
        "Search and playback are unlimited in verified groups.\n\n"
        + CREDIT_LINE
        + "</b>"
    )

SETUP_TEXT = (
    "⚙️ <b>Voice chat setup</b>\n\n"
    "1️⃣ Add the bot to your group and make it an <b>admin</b> with <code>Manage video chats</code> "
    "and <code>Invite users</code>.\n"
    "2️⃣ Start a voice or video chat in the group.\n"
    "3️⃣ Use /play <i>song name</i>. The assistant account joins automatically; "
    "if Telegram blocks that, add it to the group once by hand.\n\n"
    "<i>Why an assistant account? Telegram bots cannot publish audio into a voice chat, so a normal "
    "user session streams it. You log in with it once.</i>\n\n"
    + CREDIT_LINE
)

PANELS = {
    "search": "🔎 <b>Search</b>\n\nSend any song name in private chat, or use /search <i>song</i> anywhere. "
              "Tap a result to play it (groups) or download it (private).",
    "play": "▶️ <b>Play in a voice chat</b>\n\n"
            "/play <i>song or link</i> — play or queue (YouTube playlist links work too)\n"
            "/auto <i>song</i> — queue the song plus similar tracks\n"
            "/history — replay recent songs\n\n"
            "Reply to a message containing a song name with /play to use its text.",
    "send": "🎵 <b>Get an MP3</b>\n\n/send <i>song name</i> — or just message me the name in private chat.",
    "video": "🎬 <b>Get a video</b>\n\n/video <i>720p song name</i>\n"
             "Qualities: " + ", ".join(f"{q}p" for q in VIDEO_QUALITIES) + " (default 480p).",
    "controls": "🎛 <b>Player controls</b>\n\n"
                "The player card has pause/resume, skip, stop, loop, shuffle, volume and queue buttons.\n\n"
                "/pause · /resume · /skip · /stop · /leave\n"
                "/loop <i>off|one|all</i> · /shuffle · /volume <i>1-200</i>\n"
                "/remove <i>n</i> · /clear · /queue · /now\n\n"
                "Controls work for people in the voice chat, group admins and whoever requested the song.",
    "setup": SETUP_TEXT,
    "player_setup": "🎛 <b>Player & Setup</b>\n\n"
                     "Use the player controls below during playback.\n\n"
                     + SETUP_TEXT,
}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def esc(value) -> str:
    return html.escape(str(value or ""), quote=False)


def fmt_time(seconds) -> str:
    seconds = int(seconds or 0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def fmt_bytes(value) -> str:
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024


def fmt_eta(seconds) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    return fmt_time(int(seconds))


def fmt_uptime(seconds) -> str:
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    return " ".join(part for part in (f"{days}d" if days else "", f"{hours}h" if hours else "", f"{minutes}m") if part)


def bar(current, total, width=14) -> str:
    if not total:
        return "▰" * 3 + "▱" * (width - 3)
    filled = round(width * min(max(current, 0), total) / total)
    return "▰" * filled + "▱" * (width - filled)


def mention(user_id, name) -> str:
    name = esc(name or "someone")
    return f'<a href="tg://user?id={user_id}">{name}</a>' if user_id else name


def is_admin(member) -> bool:
    return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)


def who(message):
    user = message.from_user
    if user:
        return user.id, user.first_name or user.username or "Someone"
    return 0, (message.sender_chat.title if message.sender_chat else "Admin")


def get_query(message, skip=1) -> str:
    text = " ".join(message.command[skip:]).strip()
    if not text and message.reply_to_message:
        reply = message.reply_to_message
        text = (reply.text or reply.caption or "").strip()
    return text[:200]


def friendly_error(exc) -> str:
    text = str(exc) or exc.__class__.__name__
    low = text.lower()
    if "phone_number_invalid" in low:
        return "Assistant account phone number was rejected."
    if "peer id invalid" in low or "peer_id_invalid" in low:
        return "Assistant account cannot access this chat."
    if "sign in to confirm" in low or "sign-in verification" in low or "not a bot" in low:
        return "YouTube verification is required."
    if "ffmpeg" in low:
        return "FFmpeg is missing."
    if "chat_admin_required" in low or "requires chat admin privileges" in low:
        return "Assistant account needs chat permissions."
    if "unavailable" in low or "private video" in low or "removed" in low:
        return "Video is unavailable or private."
    if "no audio format" in low or "nothing playable" in low or "no playable" in low:
        return "No playable format was found."
    if "too large" in low or "file size" in low:
        return "File is too large for Telegram."
    return "Source could not be downloaded."


def download_error(exc) -> str:
    """Keep download failures short and consistent for users."""
    return f"❌ <b>This cannot be downloaded by bot.</b>\nReason: {friendly_error(exc)}"


async def safe_callback_answer(callback, *args, **kwargs):
    """Answer inline buttons without logging stale Telegram callback IDs as crashes."""
    try:
        return await callback.answer(*args, **kwargs)
    except Exception as exc:
        if "query_id_invalid" in str(exc).lower() or exc.__class__.__name__ == "QueryIdInvalid":
            logger.debug("Ignoring expired callback query", exc_info=True)
            return None
        raise


class PickStore:
    """Short-lived storage for search results behind inline buttons."""

    def __init__(self, ttl=900, limit=300):
        self.ttl, self.limit, self.data = ttl, limit, {}

    def put(self, items):
        now = time.monotonic()
        for key in [k for k, v in self.data.items() if now - v[0] > self.ttl]:
            del self.data[key]
        while len(self.data) >= self.limit:
            self.data.pop(next(iter(self.data)))
        token = secrets.token_hex(3)
        self.data[token] = (now, items)
        return token

    def get(self, token):
        entry = self.data.get(token)
        if not entry or time.monotonic() - entry[0] > self.ttl:
            self.data.pop(token, None)
            return None
        return entry[1]

    def drop(self, token):
        self.data.pop(token, None)


def home_kb(bot_username=None, in_group=False, show_owner=False):
    rows = [
        [Btn("🔎 Search", callback_data="h:search"), Btn("🎵 MP3", callback_data="h:send")],
        [Btn("▶️ Play", callback_data="h:play"), Btn("🎬 Video", callback_data="h:video")],
    ]
    if in_group:
        rows.append([Btn("🎛 Player & Setup", callback_data="h:player_setup")])
    else:
        rows.extend([
            [Btn("🎛 Player & Setup", callback_data="h:player_setup"), Btn("💎 Premium", callback_data="user:premium")],
            [Btn("🎁 Redeem", callback_data="user:redeem"),
             Btn("👑 Owner/Admin Panel", callback_data="panel:home") if show_owner else Btn("💬 Contact", url="https://t.me/f4talkbot")],
        ])
    return Kb(rows)


def dm_start_kb(bot_username=None):
    if not bot_username:
        return None
    return Kb([[Btn("💬 Start in Private Chat", url=f"https://t.me/{bot_username}?start=music")]])


def back_kb():
    return Kb([[Btn("⬅ Back", callback_data="h:home")]])


def panel_kb():
    return Kb([
        [Btn("📊 Overview", callback_data="panel:stats"), Btn("👤 Users", callback_data="panel:users")],
        [Btn("💎 Premium", callback_data="panel:premium"), Btn("🛡 Admins", callback_data="panel:admins")],
        [Btn("👥 Groups", callback_data="panel:groups"), Btn("📢 Force Join", callback_data="panel:channels")],
        [Btn("🍪 Cookies", callback_data="panel:cookies"), Btn("⚙️ Settings", callback_data="panel:settings")],
        [Btn("📣 Broadcast", callback_data="panel:broadcast"), Btn("🧾 Logs & Commands", callback_data="panel:help")],
        [Btn("🏠 Home", callback_data="panel:back")],
    ])


def force_join_kb(channels):
    rows = []
    for _, title, username, invite_link in channels:
        link = invite_link or (f"https://t.me/{username}" if username else None)
        if link:
            rows.append([Btn(f"📢 Join {title or username or 'Channel'}", url=link)])
    rows.append([Btn("✅ Verify Membership", callback_data="verify_join")])
    return Kb(rows)


_EDIT_STATE = {}
_EDIT_INTERVAL = 2.5


async def edit_card(message, text, kb=None):
    """Edit a text or photo message, ignoring harmless Telegram errors."""
    key = (getattr(getattr(message, "chat", None), "id", 0), getattr(message, "id", id(message)))
    state = _EDIT_STATE.setdefault(key, {"lock": asyncio.Lock(), "last": 0.0})
    if state["lock"].locked():
        return
    async with state["lock"]:
        wait = _EDIT_INTERVAL - (time.monotonic() - state["last"])
        if wait > 0:
            return
        try:
            if message.photo:
                await message.edit_caption(text, reply_markup=kb)
            else:
                await message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
            state["last"] = time.monotonic()
        except MessageNotModified:
            state["last"] = time.monotonic()
        except FloodWait as exc:
            state["last"] = time.monotonic()
            await asyncio.sleep(min(max(int(exc.value), 1), 30))
            try:
                if message.photo:
                    await message.edit_caption(text, reply_markup=kb)
                else:
                    await message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
                state["last"] = time.monotonic()
            except Exception:
                logger.debug("Could not retry rate-limited message edit", exc_info=True)
        except Exception:
            logger.debug("Could not edit message", exc_info=True)
    if len(_EDIT_STATE) > 2000:
        for old_key in list(_EDIT_STATE)[:500]:
            _EDIT_STATE.pop(old_key, None)


def _compose_template_sync(template_path: Path, thumb_url: str):
    """Put only the current thumbnail inside the template's white panel."""
    if not template_path.is_file() or not thumb_url:
        return None
    try:
        response = HTTP_SESSION.get(thumb_url, timeout=(5, 15))
        response.raise_for_status()
        thumb_data = response.content
        with Image.open(template_path) as source, Image.open(BytesIO(thumb_data)) as thumb:
            template = source.convert("RGB")
            # Coordinates are from the supplied 1672x941 template.
            scale_x, scale_y = template.width / 1672, template.height / 941
            left, top, right, bottom = (
                round(817 * scale_x), round(211 * scale_y),
                round(1580 * scale_x), round(646 * scale_y),
            )
            inset = max(4, round(8 * scale_x))
            box = (left + inset, top + inset, right - inset, bottom - inset)
            fitted = ImageOps.fit(thumb.convert("RGB"), (box[2] - box[0], box[3] - box[1]), method=Image.Resampling.LANCZOS)
            template.paste(fitted, (box[0], box[1]))
            output_dir = Path(tempfile.mkdtemp(prefix="f4_music_"))
            output = output_dir / "player-card.jpg"
            template.save(output, "JPEG", quality=94, optimize=True, progressive=True)
            return output
    except Exception:
        logger.debug("Could not render player template", exc_info=True)
        return None


# ---------------------------------------------------------------------------
def register(app, player: VoicePlayer, media: YouTubeMedia, store: QueueStore, cfg, login_bridge=None):
    started_at = time.time()
    cards = {}            # chat_id -> (message, title) of the live player card
    card_refresh_tasks = {}
    template_path = Path(__file__).resolve().parent.parent / "assets" / "player_template.png"
    picks = PickStore()
    cooldowns = {}
    setup_ok = {}         # chat_id -> monotonic time until which the setup check is cached
    download_gate = asyncio.Semaphore(cfg.download_slots)
    bot_info = {}

    async def bot_username():
        if "name" not in bot_info:
            bot_info["name"] = (await app.get_me()).username
        return bot_info["name"]

    if login_bridge:
        @app.on_message(filters.private & filters.text)
        async def assistant_login_reply(client, message):
            if not login_bridge.active:
                raise ContinuePropagation
            if not message.from_user or message.from_user.id not in login_bridge.owner_ids:
                return
            accepted = await login_bridge.submit(message.text.strip())
            if accepted:
                await message.reply("✅ Received securely. Continuing assistant login…")

    async def studio_audio_message(client, query_or_title):
        """Find an MP3 in Song Studio without downloading it again."""
        if not query_or_title or is_url(query_or_title):
            return None
        cached = store.studio_find(query_or_title)
        if cached:
            try:
                message = await client.get_messages(SONG_STUDIO_CHAT_ID, int(cached[1]))
                if message and message.audio:
                    return message
            except Exception:
                logger.debug("Cached Song Studio message is unavailable", exc_info=True)

        query = " ".join(str(query_or_title).split())[:100]
        try:
            async for message in client.search_messages(
                SONG_STUDIO_CHAT_ID, query=query, filter=MessagesFilter.AUDIO, limit=10
            ):
                if not message.audio:
                    continue
                audio = message.audio
                descriptor = " ".join(
                    part for part in (audio.title, audio.performer, message.caption) if part
                ).lower()
                words = [word.lower() for word in query.split() if len(word) > 2]
                if words and not all(word in descriptor for word in words):
                    continue
                title = " - ".join(part for part in (audio.title, audio.performer) if part) or query
                store.studio_save(title, message.id)
                return message
        except Exception:
            logger.debug("Song Studio search failed", exc_info=True)
        return None

    def playlist_marker(payload) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        marker = "F4PLAYLIST:" + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii")
        return (
            f"{marker}\n"
            f"📍 Group: {esc(payload.get('group') or 'Unknown')}\n"
            f"🆔 <code>{payload.get('chat_id')}</code>\n"
            f"👤 Added by: {esc(payload.get('by') or payload.get('added_by') or 'Unknown')}"
        )

    def playlist_payload(message):
        caption = message.caption or ""
        if not caption.startswith("F4PLAYLIST:"):
            return None
        try:
            encoded = caption.splitlines()[0].split(":", 1)[1]
            raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
            return json.loads(zlib.decompress(raw).decode("utf-8"))
        except (ValueError, TypeError, KeyError, zlib.error, json.JSONDecodeError):
            return None

    def playlist_index_marker(index) -> str:
        raw = json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return "F4PLAYLIST_INDEX:" + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii")

    def playlist_index_payload(message):
        text = (message.text or message.caption or "") if message else ""
        if not text.startswith("F4PLAYLIST_INDEX:"):
            return None
        try:
            encoded = text.splitlines()[0].split(":", 1)[1]
            raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
            return json.loads(zlib.decompress(raw).decode("utf-8"))
        except (ValueError, TypeError, KeyError, zlib.error, json.JSONDecodeError):
            return None

    async def load_playlist_index(client):
        chat = await client.get_chat(SONG_STUDIO_CHAT_ID)
        pinned = getattr(chat, "pinned_message", None)
        index = playlist_index_payload(pinned) if pinned else None
        return (index if isinstance(index, dict) else {"groups": {}}, pinned if isinstance(index, dict) else None)

    async def save_playlist_index(client, index, pinned=None):
        text = playlist_index_marker(index)
        if pinned:
            await client.edit_message_text(SONG_STUDIO_CHAT_ID, pinned.id, text)
            return pinned
        message = await client.send_message(SONG_STUDIO_CHAT_ID, text, disable_notification=True)
        try:
            await client.pin_chat_message(SONG_STUDIO_CHAT_ID, message.id, disable_notification=True)
        except Exception:
            logger.warning("Could not pin Song Studio playlist index", exc_info=True)
        return message

    async def channel_playlist(client, chat_id):
        """Read this group's playlist records directly from Song Studio."""
        rows = []
        try:
            index, _ = await load_playlist_index(client)
            records = index.get("groups", {}).get(str(chat_id), [])
            for payload in records:
                message = await client.get_messages(SONG_STUDIO_CHAT_ID, int(payload["message_id"]))
                if message and message.audio:
                    rows.append((message, payload))
        except Exception:
            logger.exception("Could not read Song Studio playlist index")
        rows.sort(key=lambda row: (float(row[1].get("last_played", 0)), row[0].id))
        return rows

    async def copy_studio_audio(client, chat_id, status, query_or_title):
        """Copy cached MP3 with no source attribution and the standard caption."""
        message = await studio_audio_message(client, query_or_title)
        if not message:
            return False
        try:
            await edit_card(status, "⚡ <b>Sending from Song Studio…</b>")
            await client.copy_message(
                chat_id,
                SONG_STUDIO_CHAT_ID,
                message.id,
                caption=DOWNLOAD_CAPTION,
            )
            await status.delete()
            return True
        except Exception:
            logger.debug("Could not copy Song Studio audio", exc_info=True)
            return False

    def cooldown_left(user_id) -> int:
        if not cfg.cooldown or user_id in cfg.owner_ids:
            return 0
        now = time.monotonic()
        if len(cooldowns) > 2000:
            cooldowns.clear()
        wait = cfg.cooldown - (now - cooldowns.get(user_id, -1e9))
        if wait > 0:
            return math.ceil(wait)
        cooldowns[user_id] = now
        return 0

    def is_owner(user_id: int) -> bool:
        return user_id in cfg.owner_ids

    def is_manager(user_id: int) -> bool:
        return is_owner(user_id) or store.is_admin(user_id)

    def account_status(user_id: int):
        if is_owner(user_id):
            return "👑 OWNER", "♾️ UNLIMITED"
        if store.is_admin(user_id):
            return "🛡 ADMIN", "♾️ UNLIMITED"
        if store.is_premium(user_id):
            return "💎 PREMIUM", "♾️ UNLIMITED"
        return "🆓 FREE", f"{store.user_balance(user_id)} CREDITS"

    def manager_only(message) -> bool:
        return bool(message.from_user and is_manager(message.from_user.id))

    async def user_access(client, chat, uid, require_dm_for_group=True):
        """Return a human-readable access error, or None when allowed."""
        if is_manager(uid) or store.is_premium(uid):
            return None
        if chat.type == ChatType.PRIVATE:
            store.register_user(uid)
            store.daily_credits(uid)
        elif require_dm_for_group:
            if not store.has_user(uid):
                return "💬 <b>Start me in private first</b> to unlock music in this group.\nThen come back here and try again."

        missing = []
        for channel_id, title, username, invite_link in store.force_channels():
            try:
                member = await client.get_chat_member(channel_id, uid)
                if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                    raise ValueError("not a member")
            except Exception:
                link = invite_link or (f"https://t.me/{username}" if username else str(channel_id))
                missing.append(f"▫️ <a href=\"{link}\">{esc(title or username or channel_id)}</a>")
        if missing:
            return "🔒 <b>Join these channels to use F4 Music:</b>\n" + "\n".join(missing) + "\n\nThen tap Verify Membership."
        return None

    async def access_gate(client, message):
        if not message.from_user:
            return "Please use a normal Telegram user account."
        return await user_access(client, message.chat, message.from_user.id)

    async def credit_gate(client, message, cost: int):
        error = await access_gate(client, message)
        if error:
            keyboard = (
                dm_start_kb(await bot_username())
                if message.chat.type in GROUP_TYPES and not store.has_user(message.from_user.id)
                else force_join_kb(store.force_channels()) if store.force_channels() else None
            )
            await message.reply(error, reply_markup=keyboard, disable_web_page_preview=True)
            return False
        if message.chat.type in GROUP_TYPES or is_manager(message.from_user.id) or store.is_premium(message.from_user.id):
            return True
        balance = store.user_balance(message.from_user.id)
        if balance < cost:
            await message.reply(
                f"💳 You need <b>{cost}</b> credits, but have <b>{balance}</b>.\n"
                "You receive 10 credits daily. Use /redeem CODE for more."
            )
            return False
        return True

    # ---- card rendering --------------------------------------------------
    def now_text(chat_id) -> str:
        item = player.current.get(chat_id)
        if not item:
            return "💤 <b>Nothing is playing</b>\n\nUse /play <i>song name</i> to start the music."
        paused = chat_id in player.paused
        elapsed = player.elapsed(chat_id)
        lines = [f"🎧 <b>{'⏸ Paused' if paused else '▶️ Now Playing'}</b>", "", f"<b>{esc(item.title)}</b>"]
        if item.uploader:
            lines.append(f"👤 {esc(item.uploader)}")
        total = f" / {fmt_time(item.duration)}" if item.duration else ""
        lines += ["", f"<code>{bar(elapsed, item.duration)}</code>  {fmt_time(elapsed)}{total}", ""]
        lines.append(
            f"🔁 {LOOP_LABEL[player.loop.get(chat_id, 'off')]}  ·  🔊 {player.volume.get(chat_id, cfg.default_volume)}%"
            f"  ·  🙋 {mention(item.requested_by, item.requester)}"
        )
        waiting = store.list(chat_id)
        if waiting:
            more = f" (+{len(waiting) - 1} more)" if len(waiting) > 1 else ""
            lines.append(f"⏭ Next: {esc(waiting[0].title)}{more}")
        lines += ["", CREDIT_LINE]
        return "\n".join(lines)

    def controls_kb(chat_id):
        c = chat_id
        paused = c in player.paused
        mode = player.loop.get(c, "off")
        return Kb([
            [
                Btn("▶️ Resume" if paused else "⏸ Pause", callback_data=f"m:pp:{c}"),
                Btn("⏭ Skip", callback_data=f"m:skip:{c}"),
                Btn("⏹ Stop", callback_data=f"m:stop:{c}"),
            ],
            [
                Btn(f"🔁 Loop: {LOOP_LABEL[mode]}", callback_data=f"m:loop:{c}"),
                Btn("🔀 Shuffle", callback_data=f"m:shuffle:{c}"),
                Btn("📜 Queue", callback_data=f"m:queue:{c}"),
            ],
            [
                Btn("🔉", callback_data=f"m:vdn:{c}"),
                Btn("🔊", callback_data=f"m:vup:{c}"),
                Btn("🔄 Refresh", callback_data=f"m:refresh:{c}"),
            ],
        ])

    def queue_text(chat_id) -> str:
        current = player.current.get(chat_id)
        items = store.list(chat_id)
        if not current and not items:
            return "📜 <b>Queue is empty</b>\n\nAdd something with /play <i>song name</i>."
        count = len(items) + (1 if current else 0)
        lines = [f"📜 <b>Queue</b> · {count}/{cfg.max_queue}", ""]
        if current:
            lines.append(f"▶️ <b>{esc(current.title)}</b>  <code>{fmt_time(current.duration)}</code>")
        for index, item in enumerate(items, 1):
            lines.append(f"<code>{index:>2}.</code> {esc(item.title)}  <code>{fmt_time(item.duration)}</code>")
        total = sum(item.duration for item in items)
        if total:
            lines += ["", f"⏱ Up next: {fmt_time(total)}"]
        return "\n".join(lines)

    def queue_kb(chat_id):
        items = store.list(chat_id)
        rows = []
        buttons = [Btn(f"✖ {i}", callback_data=f"q:rm{i}:{chat_id}") for i in range(1, len(items) + 1)]
        rows += [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
        rows.append([
            Btn("⬅ Back", callback_data=f"m:now:{chat_id}"),
            Btn("🔀 Shuffle", callback_data=f"q:shuffle:{chat_id}"),
            Btn("🗑 Clear", callback_data=f"q:clear:{chat_id}"),
        ])
        return Kb(rows)

    def mini_kb(chat_id):
        return Kb([[Btn("📜 Queue", callback_data=f"m:queue:{chat_id}"), Btn("🎧 Now", callback_data=f"m:now:{chat_id}")]])

    async def publish_card(chat_id):
        """Send a fresh player card and collapse the previous one."""
        item = player.current.get(chat_id)
        if not item:
            return
        previous = cards.pop(chat_id, None)
        if previous:
            old_message, old_title = previous
            await edit_card(old_message, f"✅ <b>Played</b>\n{esc(old_title)}")
        text, kb = now_text(chat_id), controls_kb(chat_id)
        message = None
        rendered = await asyncio.to_thread(_compose_template_sync, template_path, item.thumb)
        try:
            if rendered:
                message = await app.send_photo(chat_id, str(rendered), caption=text, reply_markup=kb)
            elif item.thumb:
                message = await app.send_photo(chat_id, item.thumb, caption=text, reply_markup=kb)
        except Exception:
            logger.debug("Could not send thumbnail card", exc_info=True)
        finally:
            if rendered:
                shutil.rmtree(rendered.parent, ignore_errors=True)
        if message is None:
            try:
                message = await app.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)
            except Exception:
                logger.exception("Could not send player card")
        cards[chat_id] = (message, item.title)
        old_task = card_refresh_tasks.pop(chat_id, None)
        if old_task:
            old_task.cancel()
        card_refresh_tasks[chat_id] = asyncio.create_task(refresh_clock(chat_id))

    async def refresh_clock(chat_id):
        """Keep the player progress bar current without requiring button taps."""
        try:
            while player.current.get(chat_id) and chat_id in cards:
                duration = player.current.get(chat_id).duration or 0
                await asyncio.sleep(20 if duration > 60 else 11)
                await refresh_card(chat_id)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("Player card refresh stopped", exc_info=True)
        finally:
            task = asyncio.current_task()
            if card_refresh_tasks.get(chat_id) is task:
                card_refresh_tasks.pop(chat_id, None)

    async def refresh_card(chat_id):
        entry = cards.get(chat_id)
        if entry and player.current.get(chat_id):
            await edit_card(entry[0], now_text(chat_id), controls_kb(chat_id))

    async def end_card(chat_id, text):
        task = card_refresh_tasks.pop(chat_id, None)
        if task:
            task.cancel()
        entry = cards.pop(chat_id, None)
        if entry:
            await edit_card(entry[0], text)

    # ---- player hooks ----------------------------------------------------
    async def on_track(chat_id, item):
        await publish_card(chat_id)

    async def smart_next(chat_id):
        """Play a group favorite first, then choose an unseen related track."""
        recent = store.history(chat_id, limit=12)
        recent_sources = {item.source for item in recent}
        favorite_rows = await channel_playlist(app, chat_id)
        if favorite_rows:
            stored_message, payload = favorite_rows[0]
            item = QueueItem(
                title=payload.get("title") or stored_message.audio.title or "Untitled",
                source=payload.get("source") or "",
                requested_by=int(payload.get("added_by") or 0),
                requester=payload.get("by") or "Group playlist",
                duration=int(payload.get("duration") or stored_message.audio.duration or 0),
                uploader=payload.get("uploader") or stored_message.audio.performer or "",
                thumb="",
            )
            try:
                if not item.source:
                    return False
                await player.enqueue_and_play(chat_id, item)
                payload["last_played"] = time.time()
                index, pinned = await load_playlist_index(app)
                for record in index.get("groups", {}).get(str(chat_id), []):
                    if int(record.get("message_id", 0)) == stored_message.id:
                        record["last_played"] = payload["last_played"]
                        break
                await save_playlist_index(app, index, pinned)
                return True
            except Exception:
                logger.exception("Smart playlist item failed in chat %s", chat_id)

        if not recent:
            return False
        try:
            suggestions = await media.related(recent[0].source, recent[0].title, limit=8)
        except Exception:
            logger.debug("Smart autoplay lookup failed for chat %s", chat_id, exc_info=True)
            return False
        suggestion = next(
            (meta for meta in suggestions if meta.get("source") and meta["source"] not in recent_sources),
            None,
        )
        if not suggestion:
            return False
        item = QueueItem(
            title=suggestion["title"], source=suggestion["source"], requested_by=0,
            requester="Smart autoplay", duration=suggestion["duration"],
            uploader=suggestion["uploader"], thumb=suggestion["thumb"],
        )
        try:
            await player.enqueue_and_play(chat_id, item)
            return True
        except Exception:
            logger.exception("Smart autoplay failed in chat %s", chat_id)
            return False

    async def on_end(chat_id, reason, ended_by=""):
        if reason == "closed":
            await end_card(chat_id, "🔇 <b>Voice chat ended</b>\nThe queue was cleared.")
            return
        if reason == "queue_end":
            if await smart_next(chat_id):
                return
            await end_card(chat_id, "✅ <b>All songs played</b>\nUse /play to start again.")
            return
        suffix = f"\n👤 Finished by <b>{esc(ended_by)}</b>" if ended_by else ""
        await end_card(chat_id, f"🏁 <b>Queue finished</b>{suffix}\nUse /play to keep the music going.")

    async def on_error(chat_id, item, exc):
        await app.send_message(
            chat_id, f"⚠️ Skipped <b>{esc(item.title)}</b>\n{friendly_error(exc)}", disable_web_page_preview=True
        )

    player.on_track, player.on_end, player.on_error = on_track, on_end, on_error

    # ---- permissions -----------------------------------------------------
    async def can_control(client, chat_id, user_id) -> bool:
        if user_id in cfg.owner_ids:
            return True
        current = player.current.get(chat_id)
        if current and current.requested_by == user_id:
            return True
        try:
            if is_admin(await client.get_chat_member(chat_id, user_id)):
                return True
        except Exception:
            logger.debug("Could not read member status", exc_info=True)
        return await player.is_participant(chat_id, user_id)

    async def guard(client, message) -> bool:
        chat_id = message.chat.id
        if message.from_user:
            if await can_control(client, chat_id, message.from_user.id):
                return True
        elif message.sender_chat and message.sender_chat.id == chat_id:
            return True  # anonymous group admin
        await message.reply("🔒 Join the voice chat, be a group admin, or be the person who requested the song.")
        return False

    async def check_setup(client, chat):
        """Return an HTML error message, or None when playback can start."""
        if player.priority_blocked(chat.id):
            return (
                "⏳ <b>Assistant is serving the priority group right now.</b>\n"
                f"Try again after <code>{player.priority_chat_id}</code> finishes."
            )
        if time.monotonic() < setup_ok.get(chat.id, 0):
            return None
        me = await client.get_chat_member(chat.id, "me")
        missing = []
        if me.status == ChatMemberStatus.OWNER:
            pass
        elif me.status != ChatMemberStatus.ADMINISTRATOR:
            missing = ["Make the bot an administrator", "Manage video chats", "Invite users"]
        else:
            privileges = me.privileges
            if not privileges or not privileges.can_manage_video_chats:
                missing.append("Manage video chats")
            if not privileges or not privileges.can_invite_users:
                missing.append("Invite users")
        if missing:
            return (
                "⚙️ <b>Setup needed</b>\nPromote the bot and grant:\n"
                + "\n".join(f"▫️ {esc(item)}" for item in missing)
                + "\n\nThen run the command again."
            )

        if login_bridge:
            login_bridge.set_request_context(chat.id, chat.title or "")
        if player.priority_chat_id == chat.id:
            await player.release_other_chats(chat.id)
        await player.start()
        assistant = await player.assistant_me()
        try:
            member = await player.assistant.get_chat_member(chat.id, assistant.id)
        except Exception:
            member = None
        if not member or member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
            try:
                target = chat.username or await client.export_chat_invite_link(chat.id)
                await player.assistant.join_chat(target)
            except Exception as exc:
                logger.warning("Assistant could not join chat %s: %s", chat.id, exc)
                handle = f"@{assistant.username}" if assistant.username else esc(assistant.first_name)
                return (
                    "⚙️ <b>Setup needed</b>\nThe assistant account couldn't join automatically.\n"
                    f"Add <b>{handle}</b> to this group (and unban it if needed), then try again."
                )
            member = await player.assistant.get_chat_member(chat.id, assistant.id)

        # The bot can promote the assistant after it joins when it has
        # can_promote_members. This lets PyTgCalls create a call if none is
        # already running, without requiring a manual promotion step.
        needs_voice_admin = (
            member.status != ChatMemberStatus.OWNER
            and (member.status != ChatMemberStatus.ADMINISTRATOR
                 or not member.privileges
                 or not member.privileges.can_manage_video_chats)
        )
        if needs_voice_admin:
            try:
                await client.promote_chat_member(
                    chat.id,
                    assistant.id,
                    privileges=ChatPrivileges(
                        can_manage_chat=True,
                        can_manage_video_chats=True,
                        can_invite_users=True,
                    ),
                )
            except Exception as exc:
                logger.warning("Assistant could not be promoted in chat %s: %s", chat.id, exc)
                handle = f"@{assistant.username}" if assistant.username else esc(assistant.first_name)
                return (
                    "⚙️ <b>Assistant promotion needed</b>\n"
                    f"I couldn't promote <b>{handle}</b>. Make sure this bot can add administrators, "
                    "then try /play again."
                )
        setup_ok[chat.id] = time.monotonic() + 120
        return None

    # ---- shared flows ----------------------------------------------------
    def too_long(meta) -> bool:
        return bool(cfg.max_duration and meta["duration"] and meta["duration"] > cfg.max_duration)

    async def enqueue_metas(chat_id, uid, uname, status, metas):
        added, skipped, started, full = [], 0, False, False
        for meta in metas:
            if meta.get("live") or too_long(meta):
                skipped += 1
                continue
            item = QueueItem(
                title=meta["title"], source=meta["source"], requested_by=uid, requester=uname,
                duration=meta["duration"], uploader=meta["uploader"], thumb=meta["thumb"],
            )
            try:
                started = (await player.enqueue_and_play(chat_id, item)) or started
            except QueueFull:
                full = True
                break
            added.append(item)
        if not added:
            if full:
                return await edit_card(status, f"📜 <b>Queue is full</b>\nMaximum {cfg.max_queue} songs per group.", mini_kb(chat_id))
            limit = f"{cfg.max_duration // 60} min" if cfg.max_duration else "the limit"
            return await edit_card(status, f"⏭ <b>Nothing added</b>\nTracks longer than {limit} and live streams are skipped.")
        extras = []
        if skipped:
            extras.append(f"{skipped} skipped (too long or live)")
        if full:
            extras.append("queue full")
        note = f"\n<i>{', '.join(extras)}</i>" if extras else ""
        if started:
            await status.delete()
            await publish_card(chat_id)
            if len(added) > 1:
                await app.send_message(
                    chat_id, f"➕ Added <b>{len(added) - 1}</b> more to the queue.{note}", reply_markup=mini_kb(chat_id)
                )
            elif note:
                await app.send_message(chat_id, note.strip())
        elif len(added) == 1:
            item, position = added[0], store.count(chat_id)
            await edit_card(
                status,
                f"➕ <b>Added to queue</b>\n\n<b>{esc(item.title)}</b>\n"
                f"⏱ {fmt_time(item.duration)}  ·  #{position} in line{note}",
                mini_kb(chat_id),
            )
        else:
            await edit_card(status, f"➕ <b>Added {len(added)} songs</b> to the queue.{note}", mini_kb(chat_id))

    async def play_flow(client, chat, uid, uname, status, query=None, metas=None, auto=False):
        try:
            error = await user_access(client, chat, uid)
            if error:
                keyboard = (
                    dm_start_kb(await bot_username())
                    if chat.type in GROUP_TYPES and not store.has_user(uid)
                    else force_join_kb(store.force_channels()) if store.force_channels() else None
                )
                return await edit_card(status, error, keyboard)
            error = await check_setup(client, chat)
            if error:
                return await edit_card(status, error)
            if metas is None:
                metas = await media.lookup(query, limit=cfg.max_queue)
                if auto:
                    seed = metas[0]
                    metas = [seed] + await media.related(seed["source"], seed["title"], cfg.max_queue - 1)
            await enqueue_metas(chat.id, uid, uname, status, metas)
        except (QueueFull, NoActiveCall) as exc:
            await edit_card(status, f"⚠️ {esc(exc)}")
        except EOFError:
            await edit_card(status, "⚠️ Voice assistant login was cancelled. Run the bot in an interactive terminal and complete the one-time login.")
        except Exception as exc:
            logger.exception("Group playback failed")
            first = metas[0] if metas else None
            if cfg.group_audio_fallback and cfg.private_downloads and first and not isinstance(exc, MediaError):
                await edit_card(status, "⚠️ Voice playback failed — sending the track as an MP3 instead.")
                return await deliver(client, chat.id, status, first, "audio")
            await edit_card(status, f"⚠️ Could not play this song.\n{friendly_error(exc)}")

    async def deliver(client, chat_id, status, meta, kind, height=DEFAULT_VIDEO_HEIGHT, quality_locked=False):
        title = meta["title"]
        if kind == "audio":
            if await copy_studio_audio(client, chat_id, status, title):
                return True
        if download_gate.locked():
            await edit_card(status, "🕒 <b>Waiting in line…</b>\nOther downloads are running.")
        async with download_gate:
            try:
                loop = asyncio.get_running_loop()
                last_download_update = [0.0]

                async def show_download_progress(data):
                    downloaded = data.get("downloaded_bytes") or 0
                    total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                    speed = data.get("speed") or 0
                    eta = data.get("eta")
                    percent = int(downloaded * 100 / total) if total else 0
                    size = f"{fmt_bytes(downloaded)} / {fmt_bytes(total)}" if total else fmt_bytes(downloaded)
                    speed_text = f" · {fmt_bytes(speed)}/s" if speed else ""
                    await edit_card(
                        status,
                        f"⬇️ <b>Downloading…</b> {percent}%\n"
                        f"<i>{esc(title)}</i>\n<code>{bar(downloaded, total, 12)}</code>\n"
                        f"{size}{speed_text} · ETA {fmt_eta(eta)}",
                    )

                def download_hook(data):
                    if data.get("status") not in ("downloading", "finished"):
                        return
                    now = time.monotonic()
                    if data.get("status") == "downloading" and now - last_download_update[0] < 2:
                        return
                    last_download_update[0] = now
                    asyncio.run_coroutine_threadsafe(show_download_progress(data), loop)

                await edit_card(status, f"⬇️ <b>Downloading…</b>\n<i>{esc(title)}</i>\nPreparing size and ETA…")
                if kind == "audio":
                    path, title, artist, duration = await media.download(meta["source"], download_hook)
                else:
                    path, title, duration = await media.download_video(meta["source"], height, download_hook)
                if kind == "video":
                    file_size = path.stat().st_size
                    if file_size > MAX_VIDEO_BYTES and not quality_locked and height > LOW_VIDEO_HEIGHT:
                        media.cleanup(path)
                        await edit_card(
                            status,
                            "📉 <b>Medium quality is too large.</b> Trying lower quality…",
                        )
                        path, title, duration = await media.download_video(
                            meta["source"], LOW_VIDEO_HEIGHT, download_hook
                        )
                        file_size = path.stat().st_size
                    if file_size > MAX_VIDEO_BYTES:
                        media.cleanup(path)
                        await edit_card(
                            status,
                            download_error(ValueError("File size is too large")),
                        )
                        return False
                last_edit = 0.0

                async def progress(current, total):
                    nonlocal last_edit
                    if not total or time.monotonic() - last_edit < 3:
                        return
                    last_edit = time.monotonic()
                    await edit_card(
                        status,
                        f"📤 <b>Uploading…</b> {int(current * 100 / total)}%\n"
                        f"<code>{bar(current, total, 12)}</code>\n"
                        f"{fmt_bytes(current)} / {fmt_bytes(total)}",
                    )

                try:
                    await edit_card(status, "📤 <b>Uploading…</b>")
                    if kind == "audio":
                        sent = await client.send_audio(
                            chat_id, str(path), caption=DOWNLOAD_CAPTION, title=title,
                            performer=artist or None, duration=duration, progress=progress,
                        )
                        if chat_id != SONG_STUDIO_CHAT_ID and sent:
                            try:
                                studio_copy = await client.copy_message(
                                    SONG_STUDIO_CHAT_ID,
                                    chat_id,
                                    sent.id,
                                    caption=DOWNLOAD_CAPTION,
                                )
                                if studio_copy:
                                    store.studio_save(title, studio_copy.id)
                            except Exception:
                                logger.warning("Could not cache MP3 in Song Studio", exc_info=True)
                    else:
                        await client.send_video(
                            chat_id, str(path), caption=DOWNLOAD_CAPTION,
                            duration=duration, supports_streaming=True, progress=progress,
                        )
                finally:
                    media.cleanup(path)
                await status.delete()
                return True
            except Exception as exc:
                logger.exception("Download/send failed")
                await edit_card(status, download_error(exc))
                return False

    async def media_command(client, message, kind, query, height=DEFAULT_VIDEO_HEIGHT, quality_locked=False):
        if not cfg.private_downloads:
            return await message.reply("🚫 Downloads are disabled in this bot's configuration.")
        uid, _ = who(message)
        if not await credit_gate(client, message, 1):
            return
        if wait := cooldown_left(uid):
            return await message.reply(f"⏳ Easy there — try again in {wait}s.")
        status = await message.reply("🔎 <b>Searching…</b>")
        try:
            if kind == "audio":
                delivered = await copy_studio_audio(client, message.chat.id, status, query)
                if delivered:
                    charged = 0 if message.chat.type in GROUP_TYPES or store.is_premium(uid) else 1
                    if charged:
                        store.consume_credits(uid, charged)
                    store.log_usage(uid, message.chat.id, kind, query, True, charged)
                    return
            meta = (await media.lookup(query, limit=1))[0]
            if too_long(meta):
                return await edit_card(status, f"⏭ That's longer than {cfg.max_duration // 60} minutes — too big to send.")
            delivered = await deliver(
                client, message.chat.id, status, meta, kind, height, quality_locked
            )
            charged = 0 if message.chat.type in GROUP_TYPES or store.is_premium(uid) else 1
            if delivered and charged:
                store.consume_credits(uid, charged)
            store.log_usage(uid, message.chat.id, kind, query, delivered, charged if delivered else 0)
        except Exception as exc:
            logger.exception("Media request failed")
            await edit_card(status, download_error(exc))

    async def show_results(client, message, query):
        is_group = message.chat.type in GROUP_TYPES
        if not is_group and not cfg.private_downloads:
            return await message.reply("🚫 Downloads are disabled. Use me in a group with /play to stream in a voice chat.")
        uid, _ = who(message)
        if not await credit_gate(client, message, 1 if not is_group else 0):
            return
        if wait := cooldown_left(uid):
            return await message.reply(f"⏳ Easy there — try again in {wait}s.")
        status = await message.reply("🔎 <b>Searching…</b>")
        try:
            results = await media.lookup(query, limit=5) if is_url(query) else await media.search_list(query, limit=5)
        except Exception as exc:
            store.log_usage(uid, message.chat.id, "search", query, False, 0)
            return await edit_card(status, f"⚠️ Search failed.\n{friendly_error(exc)}")
        charged = 0
        if not is_group and not store.is_premium(uid):
            charged = len(results)
            if not store.consume_credits(uid, charged):
                return await edit_card(status, "💳 Not enough credits for these results. Use /balance or /redeem CODE.")
        store.log_usage(uid, message.chat.id, "search", query, True, charged)
        token = picks.put(results)
        lines = [f"<b>Results</b> · <i>{esc(query[:60])}</i>", ""]
        for number, meta in zip(NUMBERS, results):
            extra = " · ".join(part for part in (fmt_time(meta["duration"]) if meta["duration"] else "", esc(meta["uploader"])) if part)
            lines.append(f"{number} <b>{esc(meta['title'])}</b>\n      <i>{extra}</i>")
        rows = []
        if is_group:
            rows.append([Btn(f"PLAY {i + 1}", callback_data=f"s:{token}:{i}:p") for i in range(len(results))])
            rows.append([Btn(f"MUSIC {i + 1}", callback_data=f"s:{token}:{i}:a") for i in range(len(results))])
            rows.append([Btn(f"VIDEO {i + 1}", callback_data=f"s:{token}:{i}:v") for i in range(len(results))])
        else:
            rows.append([Btn(f"MUSIC {i + 1}", callback_data=f"s:{token}:{i}:a") for i in range(len(results))])
            rows.append([Btn(f"VIDEO {i + 1}", callback_data=f"s:{token}:{i}:v") for i in range(len(results))])
        rows.append([Btn("CLOSE", callback_data=f"s:{token}:x:x")])
        await edit_card(status, "\n".join(lines), Kb(rows))

    # ---------------------------------------------------------------------
    # info / menu
    # ---------------------------------------------------------------------
    @app.on_message(filters.command(["start", "menu"]))
    async def menu(client, message):
        display_name = "there"
        referral_notice = ""
        if message.chat.type == ChatType.PRIVATE and message.from_user and not message.from_user.is_bot:
            store.register_user(message.from_user.id, message.from_user.username or "")
            if len(message.command) > 1 and message.command[1].startswith("ref_"):
                raw_referrer = message.command[1][4:]
                if raw_referrer.isdigit():
                    referrer = store.claim_referral(message.from_user.id, int(raw_referrer))
                    if referrer:
                        referral_notice = "\n\n🎁 <b>Referral linked!</b> Your friend earned 5 credits."
                        try:
                            await client.send_message(
                                referrer,
                                f"🎁 <b>New referral!</b> <code>{message.from_user.id}</code> joined. You earned <b>5 credits</b>.\n"
                                f"Updated balance: <b>{store.user_balance(referrer)}</b>.",
                            )
                        except Exception:
                            logger.debug("Could not notify referrer %s", referrer, exc_info=True)
            force_error = await user_access(client, message.chat, message.from_user.id)
            if force_error:
                return await message.reply(
                    force_error,
                    reply_markup=force_join_kb(store.force_channels()),
                    disable_web_page_preview=True,
                )
            display_name = message.from_user.first_name or message.from_user.username or "there"
            plan, power = account_status(message.from_user.id)
        else:
            plan, power = "🆓 FREE", "10 CREDITS DAILY"
        in_group = message.chat.type in GROUP_TYPES
        welcome = group_welcome_text(message.chat.title or "This group") if in_group else welcome_text(display_name, plan, power) + referral_notice
        keyboard = home_kb(
            await bot_username(),
            in_group=in_group,
            show_owner=bool(message.from_user and is_manager(message.from_user.id)),
        )
        command_name = (message.command[0] or "").lower().split("@", 1)[0]
        if not in_group and command_name == "start" and STARTUP_IMAGE.is_file():
            try:
                await message.reply_photo(str(STARTUP_IMAGE), caption=welcome, reply_markup=keyboard)
            except Exception:
                logger.exception("Could not send startup image")
                await message.reply(welcome, reply_markup=keyboard, disable_web_page_preview=True)
        else:
            await message.reply(welcome, reply_markup=keyboard, disable_web_page_preview=True)

    @app.on_message(filters.command(["help"]))
    async def help_command(client, message):
        if message.chat.type == ChatType.PRIVATE and message.from_user and not message.from_user.is_bot:
            store.register_user(message.from_user.id, message.from_user.username or "")
        await message.reply(
            help_text(
                message.chat.type in GROUP_TYPES,
                bool(message.from_user and is_manager(message.from_user.id)),
            ),
            disable_web_page_preview=True,
        )

    @app.on_callback_query(filters.regex(r"^verify_join$"))
    async def verify_join(client, callback):
        if not callback.from_user or not callback.message:
            return await safe_callback_answer(callback, "Open this in private chat.", show_alert=True)
        error = await user_access(client, callback.message.chat, callback.from_user.id)
        if error:
            return await safe_callback_answer(callback, "You have not joined every required channel yet.", show_alert=True)
        await safe_callback_answer(callback, "✅ Verification successful")
        try:
            await callback.message.delete()
        except Exception:
            pass
        user = callback.from_user
        display_name = user.first_name or user.username or "there"
        plan, power = account_status(user.id)
        welcome = welcome_text(display_name, plan, power)
        keyboard = home_kb(await bot_username(), in_group=False, show_owner=is_manager(user.id))
        if STARTUP_IMAGE.is_file():
            try:
                await client.send_photo(user.id, str(STARTUP_IMAGE), caption=welcome, reply_markup=keyboard)
            except Exception:
                logger.exception("Could not send verified startup image")
                await client.send_message(user.id, welcome, reply_markup=keyboard)
        else:
            await client.send_message(user.id, welcome, reply_markup=keyboard)

    @app.on_message(filters.command(["setup"]))
    async def setup(client, message):
        await message.reply(SETUP_TEXT, reply_markup=back_kb())

    @app.on_callback_query(filters.regex(r"^h:(home|search|play|send|video|controls|setup|player_setup)$"))
    async def help_panel(client, callback):
        key = callback.matches[0].group(1)
        await safe_callback_answer(callback)
        if callback.message:
            if key == "home":
                in_group = callback.message.chat.type in GROUP_TYPES
                if in_group:
                    text = group_welcome_text(callback.message.chat.title or "This group")
                else:
                    display_name = callback.from_user.first_name or callback.from_user.username or "there"
                    store.register_user(callback.from_user.id, callback.from_user.username or "")
                    plan, power = account_status(callback.from_user.id)
                    text = welcome_text(display_name, plan, power)
                await edit_card(
                    callback.message,
                    text,
                    home_kb(
                        await bot_username(),
                        in_group=callback.message.chat.type in GROUP_TYPES,
                        show_owner=is_manager(callback.from_user.id),
                    ),
                )
            else:
                await edit_card(callback.message, PANELS[key] + "\n\n" + CREDIT_LINE, back_kb())

    @app.on_message(filters.command(["ping"]))
    async def ping(client, message):
        began = time.perf_counter()
        reply = await message.reply("🏓 <b>Pong…</b>")
        await edit_card(reply, f"🏓 <b>Pong!</b>  <code>{(time.perf_counter() - began) * 1000:.0f} ms</code>")

    @app.on_message(filters.command(["stats"]))
    async def stats(client, message):
        await message.reply(
            "📊 <b>F4 Music</b>\n\n"
            f"⏱ Uptime: <code>{fmt_uptime(time.time() - started_at)}</code>\n"
            f"🎙 Active voice chats: <code>{len(player.active)}</code>\n"
            f"📜 Songs waiting: <code>{store.total()}</code>\n"
            f"👤 Private users: <code>{store.user_count()}</code>\n\n"
            + CREDIT_LINE
        )

    def duration_value(raw: str):
        if raw.lower() in {"perma", "perm", "permanent"}:
            return None
        match = re.fullmatch(r"(\d+)d", raw.lower())
        if not match or int(match.group(1)) < 1:
            raise ValueError("Use 1d, 2d, 7d or perma.")
        return time.time() + int(match.group(1)) * 86400

    def target_user_id(message, index=1):
        if len(message.command) > index and message.command[index].lstrip("-").isdigit():
            return int(message.command[index])
        if message.reply_to_message and message.reply_to_message.from_user:
            return message.reply_to_message.from_user.id
        return None

    @app.on_message(filters.private & filters.command(["panel"]))
    async def panel(client, message):
        if not manager_only(message):
            return await message.reply("⛔ Owner/admin access only.")
        await message.reply(
                "🛠 <b>F4 Music management panel</b>\n\n"
            "Use the buttons below or the admin commands shown in Commands.\n\n"
            + CREDIT_LINE,
            reply_markup=panel_kb(),
        )

    @app.on_message(filters.private & filters.command(["adminlist"]))
    async def adminlist_command(client, message):
        if not manager_only(message):
            return await message.reply("⛔ Owner/admin access only.")
        directory = {uid: username for uid, username, _, _ in store.admin_directory()}
        lines = ["🛡 <b>F4 Music administrators</b>", ""]
        for owner_id in cfg.owner_ids:
            lines.append(f"👑 <code>{owner_id}</code> · OWNER")
        for uid, username in directory.items():
            if not username:
                try:
                    profile = await client.get_users(uid)
                    username = profile.username or ""
                except Exception:
                    pass
            lines.append(f"🛡 <code>{uid}</code> · {('@' + username) if username else 'no username'} · ADMIN")
        await message.reply(("\n".join(lines) if len(lines) > 2 else "🛡 <b>No added admins yet.</b>") + "\n\n" + CREDIT_LINE)

    @app.on_callback_query(filters.regex(r"^panel:(home|back|stats|users|premium|admins|groups|channels|cookies|settings|broadcast|help)$"))
    async def panel_callback(client, callback):
        if not callback.from_user or not is_manager(callback.from_user.id):
            return await safe_callback_answer(callback, "Owner/admin access only.", show_alert=True)
        key = callback.matches[0].group(1)
        await safe_callback_answer(callback)
        if key == "home":
            role = "OWNER" if is_owner(callback.from_user.id) else "ADMIN"
            text = (
                "╭━━━〔 👑 𝐅𝟒 𝐌𝐔𝐒𝐈𝐂 𝐂𝐎𝐍𝐓𝐑𝐎𝐋 〕━━━╮\n"
                "┃\n"
                f"┃  🛠 <b>{role} COMMAND CENTER</b>\n"
                "┃  ⚡ <b>UNLIMITED ACCESS</b>\n"
                f"┃  👤 Users · <code>{store.user_count()}</code>   👥 Groups · <code>{len(store.chats())}</code>\n"
                f"┃  💎 Premium · <code>{len(store.premium_users())}</code>   🛡 Admins · <code>{len(store.admins())}</code>\n"
                "┃\n"
                "╰━━━━━━━━━━━━━━━━━━━━━━╯\n"
                "✦ Select a control below ✦"
            )
            if callback.message:
                await edit_card(callback.message, text + "\n\n" + CREDIT_LINE, panel_kb())
            return
        if key == "back":
            in_group = callback.message.chat.type in GROUP_TYPES
            if in_group:
                text = group_welcome_text(callback.message.chat.title or "This group")
            else:
                display_name = callback.from_user.first_name or callback.from_user.username or "there"
                store.register_user(callback.from_user.id, callback.from_user.username or "")
                plan, power = account_status(callback.from_user.id)
                text = welcome_text(display_name, plan, power)
            if callback.message:
                await edit_card(callback.message, text, home_kb(
                    await bot_username(),
                    in_group=in_group,
                    show_owner=is_manager(callback.from_user.id),
                ))
            return
        if key == "stats":
            text = (
                "📊 <b>Stats</b>\n"
                f"Users: <code>{store.user_count()}</code>\n"
                f"Groups: <code>{len(store.chats())}</code>\n"
                f"Admins: <code>{len(store.admins())}</code>\n"
                f"Premium: <code>{len(store.premium_users())}</code>\n"
                f"Force-join channels: <code>{len(store.force_channels())}</code>"
            )
        elif key == "users":
            rows = store.user_directory()[:30]
            text = "👤 <b>Recent users</b>\n" + ("\n".join(
                f"• <code>{uid}</code> · {('@' + username) if username else 'no username'} · {credits} credits"
                for uid, username, credits in rows
            ) or "None")
        elif key == "premium":
            rows = store.premium_users()
            text = "💎 <b>Premium users</b>\n" + ("\n".join(
                f"<code>{uid}</code> · {'permanent' if expires is None else datetime.fromtimestamp(expires, timezone.utc).strftime('%Y-%m-%d')}"
                for uid, expires in rows
            ) or "None")
        elif key == "admins":
            rows = store.admin_directory()
            text = "🛡 <b>Admins</b>\n" + ("\n".join(
                f"<code>{uid}</code> · {('@' + username) if username else 'no username'}"
                for uid, username, _, _ in rows
            ) or "No added admins yet.")
        elif key == "groups":
            rows = store.chat_directory()[:50]
            text = "👥 <b>Known groups</b>\n" + ("\n".join(
                f"• <code>{gid}</code> · {esc(title or 'Untitled')} · {('@' + username) if username else 'private group'}"
                for gid, title, username in rows
            ) or "None")
        elif key == "channels":
            rows = store.force_channels()
            text = "📢 <b>Force-join channels</b>\n" + ("\n".join(
                f"<code>{cid}</code> · {esc(title or username or 'channel')} · {'link ready' if invite_link else 'no link'}"
                for cid, title, username, invite_link in rows
            ) or "None")
        elif key == "cookies":
            active = bool(media.cookies_file and Path(media.cookies_file).is_file())
            text = (
                "🍪 <b>Download cookies</b>\n"
                f"Status: <b>{'Active' if active else 'Cookie-less (default)'}</b>\n\n"
                "Reply to a Netscape cookies.txt document with <code>/setcookies</code> "
                "or use <code>/delcookies</code> to return to cookie-less mode."
            )
        elif key == "settings":
            text = (
                "⚙️ <b>Runtime settings</b>\n"
                f"Download slots: <code>{cfg.download_slots}</code>\n"
                f"Group MP3 fallback: <code>{'ON' if cfg.group_audio_fallback else 'OFF'}</code>\n"
                f"Cookies: <code>{'ON' if media.cookies_file else 'OFF'}</code>\n\n"
                "Public downloads stay cookie-less unless an admin adds cookies."
            )
        elif key == "broadcast":
            text = "📣 Use <code>/broadcast</code> with text or reply to any message.\nUse <code>/broadcastgc CHAT_ID</code> for one group."
        else:
            text = (
                "🧾 <b>Admin commands</b>\n"
                "/addadmin USER_ID\n/deladmin USER_ID\n/forcejoin @channel\n"
                "/unforcejoin CHAT_ID\n/premium USER_ID 1d|2d|7d|perma\n"
                "/code CREDITS [USES]\n/redeem CODE\n/balance\n/logs [USER_ID]\n"
                "/broadcast TEXT or reply to media\n/broadcastgc CHAT_ID TEXT\n"
                "/setcookies (reply to cookies.txt)\n/setproxy HOST PORT SECRET"
            )
        if callback.message:
            await edit_card(callback.message, text + "\n\n" + CREDIT_LINE, panel_kb())

    @app.on_callback_query(filters.regex(r"^user:(single|bulk|stats|redeem|refer|premium|buy)$"))
    async def user_callback(client, callback):
        uid = callback.from_user.id
        action = callback.matches[0].group(1)
        await safe_callback_answer(callback)
        store.register_user(uid)
        if action == "single":
            text = "⚡ <b>Single mode</b>\nSend a song name or YouTube link here to get one result quickly."
        elif action == "bulk":
            text = "🚀 <b>Bulk mode</b>\nUse /play in a verified group voice chat for unlimited playback."
        elif action == "stats":
            premium = store.is_premium(uid)
            text = (
                "📊 <b>Your stats</b>\n"
                f"💳 Credits: <b>{'♾️ unlimited' if premium else store.user_balance(uid)}</b>\n"
                f"🔗 Referrals: <b>{store.referral_count(uid)}</b>\n"
                f"💎 Plan: <b>{'Premium' if premium else 'Free'}</b>"
            )
        elif action == "redeem":
            text = "🎁 <b>Redeem credits</b>\nUse <code>/redeem ALIEN12345678</code>.\nCodes are issued by the owner/admin."
        elif action == "refer":
            username = await bot_username()
            text = (
                "🔗 <b>Refer & Earn</b>\n"
                f"Invite friends with:\n<code>https://t.me/{username}?start=ref_{uid}</code>\n\n"
                f"You have earned <b>{store.referral_count(uid)}</b> referrals. Each valid referral gives <b>5 credits</b>."
            )
        elif action == "premium":
            if store.is_premium(uid):
                text = "💎 <b>You already have active Premium.</b>\n\n♾️ Unlimited credits are enabled for your account.\nNo purchase is needed.\n\nFor support, DM @f4talkbot."
            else:
                text = "💎 <b>Premium</b>\n₹200 · unlimited credits · priority support.\nPay via UPI and send proof to @f4talkbot."
        else:
            text = (
                "💳 <b>Buy credits</b>\n\n"
                "2 credits · ₹1\n"
                "100 credits · ₹40\n"
                "Premium · ₹200 (unlimited credits + support)\n\n"
                "UPI: <code>faridh@fam</code>\nAfter payment, send the screenshot and your Telegram ID to @f4talkbot."
            )
        if callback.message:
            await edit_card(callback.message, text + "\n\n" + CREDIT_LINE, back_kb())
            if action == "buy" and PAYMENT_QR.is_file():
                try:
                    await callback.message.reply_photo(str(PAYMENT_QR), caption=text, reply_markup=back_kb())
                except Exception:
                    logger.debug("Could not send payment QR", exc_info=True)

    @app.on_message(filters.private & filters.command(["addadmin"]))
    async def add_admin_command(client, message):
        if not is_owner(message.from_user.id if message.from_user else 0):
            return await message.reply("⛔ Owner only.")
        uid = target_user_id(message)
        if not uid:
            return await message.reply("Usage: <code>/addadmin USER_ID</code> or reply to a user's message.")
        store.add_admin(uid, message.from_user.id)
        await message.reply(f"✅ <code>{uid}</code> is now an admin.")
        try:
            await client.send_message(uid, "👑 <b>Admin access granted.</b>\nYou can now open /panel and manage F4 Music.")
        except Exception:
            logger.debug("Could not notify promoted admin %s", uid, exc_info=True)

    @app.on_message(filters.private & filters.command(["deladmin"]))
    async def del_admin_command(client, message):
        if not is_owner(message.from_user.id if message.from_user else 0):
            return await message.reply("⛔ Owner only.")
        uid = target_user_id(message)
        if not uid:
            return await message.reply("Usage: <code>/deladmin USER_ID</code>")
        store.remove_admin(uid)
        await message.reply(f"✅ <code>{uid}</code> was removed from admins.")
        try:
            await client.send_message(uid, "ℹ️ <b>Admin access removed.</b>\nYour normal F4 Music access remains active.")
        except Exception:
            logger.debug("Could not notify demoted admin %s", uid, exc_info=True)

    @app.on_message(filters.private & filters.command(["forcejoin"]))
    async def forcejoin_command(client, message):
        if not is_owner(message.from_user.id if message.from_user else 0):
            return await message.reply("⛔ Owner only.")
        if len(message.command) < 2:
            return await message.reply("Usage: <code>/forcejoin @channel</code>")
        try:
            chat = await client.get_chat(message.command[1])
            invite_link = chat.username and f"https://t.me/{chat.username}"
            if not invite_link:
                invite_link = await client.export_chat_invite_link(chat.id)
            store.add_force_channel(chat.id, chat.title or "", chat.username or "", invite_link, message.from_user.id)
            await message.reply(
                f"✅ Force-join enabled for <b>{esc(chat.title or chat.username or chat.id)}</b>.\n"
                f"Invite link: <code>{esc(invite_link)}</code>"
            )
        except Exception as exc:
            await message.reply(f"⚠️ Could not add channel: {friendly_error(exc)}")

    @app.on_message(filters.private & filters.command(["unforcejoin"]))
    async def unforcejoin_command(client, message):
        if not is_owner(message.from_user.id if message.from_user else 0):
            return await message.reply("⛔ Owner only.")
        if len(message.command) < 2 or not message.command[1].lstrip("-").isdigit():
            return await message.reply("Usage: <code>/unforcejoin CHAT_ID</code>")
        store.remove_force_channel(int(message.command[1]))
        await message.reply("✅ Force-join channel removed.")

    @app.on_message(filters.private & filters.command(["premium"]))
    async def premium_command(client, message):
        if not manager_only(message):
            return await message.reply("⛔ Owner/admin access only.")
        if len(message.command) < 3:
            return await message.reply("Usage: <code>/premium USER_ID 1d|2d|7d|perma</code>")
        try:
            uid = int(message.command[1])
            expires = duration_value(message.command[2])
            store.set_premium(uid, expires, message.from_user.id)
            await message.reply(f"💎 Premium enabled for <code>{uid}</code> ({message.command[2]}).")
            try:
                await client.send_message(
                    uid,
                    "💎 <b>Premium activated!</b>\n"
                    f"Plan: <b>{'Permanent' if expires is None else message.command[2]}</b>\n"
                    "⚡ Power: <b>Unlimited credits</b>\n🎧 Support: @f4talkbot",
                )
            except Exception:
                logger.debug("Could not notify premium user %s", uid, exc_info=True)
        except ValueError as exc:
            await message.reply(f"⚠️ {exc}")

    @app.on_message(filters.private & filters.command(["addcredits", "credit"]))
    async def add_credits_command(client, message):
        if not manager_only(message):
            return await message.reply("⛔ Owner/admin access only.")
        if len(message.command) < 3 or not message.command[1].lstrip("-").isdigit() or not message.command[2].isdigit():
            return await message.reply("Usage: <code>/addcredits USER_ID AMOUNT</code>")
        uid, amount = int(message.command[1]), max(1, int(message.command[2]))
        store.add_credits(uid, amount)
        balance = store.user_balance(uid)
        await message.reply(f"✅ Added <b>{amount}</b> credits to <code>{uid}</code>. New balance: <b>{balance}</b>.")
        try:
            await client.send_message(
                uid,
                f"✅ <b>Payment verified.</b>\nAdded: <b>{amount}</b> credits\nUpdated balance: <b>{balance}</b> credits.",
            )
        except Exception:
            logger.debug("Could not notify credited user %s", uid, exc_info=True)

    @app.on_message(filters.private & filters.command(["setqr"]))
    async def set_qr_command(client, message):
        if not is_owner(message.from_user.id if message.from_user else 0):
            return await message.reply("⛔ Owner only.")
        source = message.reply_to_message
        if not source or (not source.photo and not (source.document and (source.document.mime_type or "").startswith("image/"))):
            return await message.reply("Reply to a QR-code image with <code>/setqr</code>.")
        try:
            PAYMENT_QR.parent.mkdir(parents=True, exist_ok=True)
            await source.download(file_name=str(PAYMENT_QR))
            await message.reply("✅ Payment QR updated. It will appear under Buy Credits.")
        except Exception as exc:
            await message.reply(f"⚠️ Could not save QR: {friendly_error(exc)}")

    @app.on_message(filters.private & filters.command(["setcookies"]))
    async def set_cookies_command(client, message):
        if not manager_only(message):
            return await message.reply("⛔ Owner/admin access only.")
        source = message.reply_to_message
        document = source.document if source else None
        filename = (document.file_name or "").lower() if document else ""
        if not document or not filename.endswith((".txt", ".cookies")):
            return await message.reply(
                "Reply to a Netscape-format <code>cookies.txt</code> document with <code>/setcookies</code>."
            )
        try:
            cfg.download_dir.mkdir(parents=True, exist_ok=True)
            cookie_path = cfg.download_dir / "cookies.txt"
            await source.download(file_name=str(cookie_path))
            media.set_cookies_file(cookie_path)
            await message.reply(
                "✅ Cookies enabled for downloads.\n"
                "Public links still use cookie-less mode unless cookies are needed."
            )
        except Exception as exc:
            await message.reply(f"⚠️ Could not save cookies: {friendly_error(exc)}")

    @app.on_message(filters.private & filters.command(["delcookies"]))
    async def delete_cookies_command(client, message):
        if not manager_only(message):
            return await message.reply("⛔ Owner/admin access only.")
        try:
            cookie_paths = {cfg.download_dir / "cookies.txt"}
            if media.cookies_file:
                cookie_paths.add(Path(media.cookies_file))
            media.set_cookies_file(None)
            for cookie_path in cookie_paths:
                cookie_path.unlink(missing_ok=True)
            await message.reply("✅ Cookies removed. Downloads are now cookie-less.")
        except Exception as exc:
            await message.reply(f"⚠️ Could not remove cookies: {friendly_error(exc)}")

    @app.on_message(filters.private & filters.command(["setproxy"]))
    async def set_proxy_command(client, message):
        if not manager_only(message):
            return await message.reply("⛔ Owner/admin access only.")
        if len(message.command) != 4 or not message.command[2].isdigit():
            return await message.reply("Usage: <code>/setproxy HOST PORT SECRET</code>\nMTProto proxy only.")
        host, port, secret = message.command[1].strip(), int(message.command[2]), message.command[3].strip()
        if not host or not 1 <= port <= 65535 or not secret:
            return await message.reply("Invalid proxy details.")
        proxy_file = cfg.state_db.with_name("telegram_proxy.json")
        try:
            proxy_file.write_text(
                json.dumps({"host": host, "port": port, "secret": secret}),
                encoding="utf-8",
            )
            await message.reply("✅ Telegram MTProto proxy saved.\nRestart the bot to apply it.")
        except Exception as exc:
            await message.reply(f"⚠️ Could not save proxy: {friendly_error(exc)}")

    @app.on_message(filters.private & filters.command(["delproxy"]))
    async def delete_proxy_command(client, message):
        if not manager_only(message):
            return await message.reply("⛔ Owner/admin access only.")
        try:
            cfg.state_db.with_name("telegram_proxy.json").unlink(missing_ok=True)
            await message.reply("✅ Telegram proxy removed. Restart the bot to use direct connections.")
        except Exception as exc:
            await message.reply(f"⚠️ Could not remove proxy: {friendly_error(exc)}")

    @app.on_message(filters.private & filters.command(["assistant"]))
    async def assistant_status_command(client, message):
        if not is_owner(message.from_user.id if message.from_user else 0):
            return await message.reply("⛔ Owner only.")
        phone = cfg.assistant_phone or "not set"
        masked = phone[:3] + "***" + phone[-3:] if len(phone) >= 6 else phone
        await message.reply(
            "🤖 <b>Assistant configuration</b>\n"
            f"Phone: <code>{esc(masked)}</code>\n"
            f"User ID: <code>{cfg.assistant_user_id or 'not set'}</code>\n"
            f"Session: <code>{esc(cfg.assistant_session)}</code>\n"
            "Only the owner can view or change these values."
        )

    @app.on_message(filters.private & filters.command(["setassistant"]))
    async def set_assistant_command(client, message):
        if not is_owner(message.from_user.id if message.from_user else 0):
            return await message.reply("⛔ Owner only.")
        if len(message.command) != 3 or not message.command[2].isdigit():
            return await message.reply("Usage: <code>/setassistant PHONE USER_ID</code>")
        phone, user_id = message.command[1].strip(), int(message.command[2])
        if not re.fullmatch(r"\+?[0-9][0-9 -]{5,20}", phone) or user_id <= 0:
            return await message.reply("Invalid assistant phone or user ID.")
        assistant_file = cfg.state_db.with_name("assistant_config.json")
        try:
            assistant_file.write_text(
                json.dumps({
                    "phone": phone,
                    "user_id": user_id,
                    "session": f"voice_assistant_{user_id}",
                }),
                encoding="utf-8",
            )
            try:
                await message.delete()
            except Exception:
                pass
            await client.send_message(
                message.from_user.id,
                "✅ New assistant configuration saved securely.\n"
                f"User ID: <code>{user_id}</code>\n"
                "Restart the bot to use it. The previous assistant session is no longer selected."
            )
        except Exception as exc:
            await message.reply(f"⚠️ Could not save assistant configuration: {friendly_error(exc)}")

    @app.on_message(filters.private & filters.command(["clearassistant"]))
    async def clear_assistant_command(client, message):
        if not is_owner(message.from_user.id if message.from_user else 0):
            return await message.reply("⛔ Owner only.")
        try:
            cfg.state_db.with_name("assistant_config.json").write_text(
                json.dumps({"phone": "", "user_id": "", "session": "voice_assistant_unconfigured"}),
                encoding="utf-8",
            )
            await message.reply(
                "✅ Assistant configuration cleared. Restart the bot before adding another assistant."
            )
        except Exception as exc:
            await message.reply(f"⚠️ Could not clear assistant configuration: {friendly_error(exc)}")

    @app.on_message(filters.private & filters.command(["claim"]))
    async def claim_payment(client, message):
        uid = message.from_user.id
        store.register_user(uid)
        details = " ".join(message.command[1:]).strip() or "payment claim"
        store.log_usage(uid, message.chat.id, "claim", details, False, 0)
        await message.reply(
            "🧾 <b>Claim received.</b>\nAn owner/admin will verify your payment and update your account.\n"
            "For help, DM @f4talkbot."
        )
        for admin_id in set(cfg.owner_ids) | set(store.admins()):
            try:
                if message.reply_to_message:
                    await client.copy_message(admin_id, message.chat.id, message.reply_to_message.id)
                await client.send_message(admin_id, f"🧾 Payment claim from <code>{uid}</code>: {esc(details)}")
            except Exception:
                logger.debug("Could not notify payment reviewer %s", admin_id, exc_info=True)

    @app.on_message(filters.private & filters.command(["code"]))
    async def code_command(client, message):
        if not manager_only(message):
            return await message.reply("⛔ Owner/admin access only.")
        if len(message.command) < 2 or not message.command[1].isdigit():
            return await message.reply("Usage: <code>/code CREDITS [USES]</code>")
        credits = max(1, int(message.command[1]))
        uses = max(1, int(message.command[2])) if len(message.command) > 2 and message.command[2].isdigit() else 1
        code = store.create_code(credits, uses, message.from_user.id)
        await message.reply(f"🎟 Redeem code: <code>{code}</code>\nCredits: <b>{credits}</b> · Uses: <b>{uses}</b>")

    @app.on_message(filters.private & filters.command(["redeem"]))
    async def redeem_command(client, message):
        if len(message.command) < 2:
            return await message.reply("Usage: <code>/redeem CODE</code>")
        uid = message.from_user.id
        store.register_user(uid)
        amount = store.redeem_code(message.command[1], uid)
        if amount:
            await message.reply(f"✅ Added <b>{amount}</b> credits.\nUpdated balance: <b>{store.user_balance(uid)}</b>.")
        else:
            await message.reply("❌ Invalid or exhausted code.")

    @app.on_message(filters.private & filters.command(["balance"]))
    async def balance_command(client, message):
        uid = message.from_user.id
        store.register_user(uid)
        if is_owner(uid):
            premium = "👑 owner · unlimited credits"
        elif is_manager(uid) or store.is_premium(uid):
            premium = "♾️ unlimited credits"
        else:
            premium = f"{store.user_balance(uid)} credits"
        await message.reply(f"💳 Balance: <b>{premium}</b>\nDaily refill: <b>10 credits</b>.")

    @app.on_message(filters.private & filters.command(["logs"]))
    async def logs_command(client, message):
        if not is_owner(message.from_user.id if message.from_user else 0):
            return await message.reply("⛔ Owner only.")
        uid = int(message.command[1]) if len(message.command) > 1 and message.command[1].isdigit() else None
        rows = store.usage(uid, 40)
        if not rows:
            return await message.reply("No usage logs yet.")
        text = "🧾 <b>Usage logs</b>\n" + "\n".join(
            f"<code>{r[0]}</code> · {esc(r[2])} · {'✅' if r[4] else '❌'} · {esc(r[3][:45])}" for r in rows
        )
        await message.reply(text[:4000])

    @app.on_message(filters.private & filters.command(["broadcastgc"]))
    async def broadcast_gc(client, message):
        if not manager_only(message):
            return await message.reply("⛔ Owner/admin access only.")
        if len(message.command) < 2 or not message.command[1].lstrip("-").isdigit():
            return await message.reply("Usage: <code>/broadcastgc CHAT_ID TEXT</code> or reply to media.")
        chat_id = int(message.command[1])
        raw = message.text or message.caption or ""
        parts = raw.split(maxsplit=2)
        text = parts[2].strip() if len(parts) > 2 else ""
        safe_text = esc(text)
        source = message.reply_to_message
        try:
            if source and not text:
                await client.copy_message(chat_id, source.chat.id, source.id)
            elif text:
                await client.send_message(chat_id, safe_text)
            else:
                return await message.reply("Add text or reply to a message/media.")
            await message.reply("✅ Sent to the group.")
        except Exception as exc:
            await message.reply(f"⚠️ Could not send: {friendly_error(exc)}")

    @app.on_message(filters.group)
    async def remember_group(client, message):
        """Keep a durable list of groups the bot can broadcast to later."""
        store.register_chat(message.chat.id, message.chat.title or "", message.chat.username or "")
        # This bookkeeping handler must not swallow command handlers such as
        # /play, because Pyrogram stops after the first matching handler.
        raise ContinuePropagation

    @app.on_message(filters.command(["broadcast"]) & filters.private)
    async def broadcast(client, message):
        if not manager_only(message):
            return await message.reply("⛔ Owner/admin access only.")
        raw = message.text or message.caption or ""
        parts = raw.split(maxsplit=1)
        text = parts[1].strip() if len(parts) > 1 else ""
        safe_text = esc(text)
        source = message.reply_to_message
        if not text and not source:
            return await message.reply(
                "Usage: <code>/broadcast your message</code>, or reply to any message to copy it."
            )
        status = await message.reply("📣 Sending…")
        sent = failed = 0
        targets = list(dict.fromkeys([*store.users(), *store.chats()]))
        for target_id in targets:
            try:
                if source and not text:
                    await client.copy_message(target_id, source.chat.id, source.id)
                else:
                    await client.send_message(target_id, safe_text)
                sent += 1
            except FloodWait as exc:
                await asyncio.sleep(exc.value)
                try:
                    if source and not text:
                        await client.copy_message(target_id, source.chat.id, source.id)
                    else:
                        await client.send_message(target_id, safe_text)
                    sent += 1
                except Exception:
                    failed += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)
        await edit_card(
            status,
            f"📣 <b>Broadcast done</b>\n✅ {sent} delivered · ⚠️ {failed} failed\n"
            f"👥 {len(store.chats())} groups · 👤 {len(store.users())} users",
        )

    # ---------------------------------------------------------------------
    # search + downloads
    # ---------------------------------------------------------------------
    @app.on_message(filters.command(["search", "s"]))
    async def search_command(client, message):
        query = get_query(message)
        if not query:
            return await message.reply("Usage: <code>/search song name</code>")
        await show_results(client, message, query)

    @app.on_message(filters.private & filters.text & ~filters.regex(r"^/"))
    async def private_text(client, message):
        if message.from_user and not message.from_user.is_bot:
            store.register_user(message.from_user.id, message.from_user.username or "")
        await show_results(client, message, message.text.strip()[:200])

    @app.on_message(filters.group & filters.text & ~filters.regex(r"^/") & filters.regex(r"^https?://", re.IGNORECASE))
    async def group_link(client, message):
        """Show play, MP3, and video choices when a link is pasted in a GC."""
        await show_results(client, message, message.text.strip()[:500])

    @app.on_message(filters.command(["send", "mp3"]))
    async def send_command(client, message):
        query = get_query(message)
        if not query:
            return await message.reply("Usage: <code>/send song name</code>")
        await media_command(client, message, "audio", query)

    @app.on_message(filters.command(["video"]))
    async def video_command(client, message):
        args = message.command[1:]
        height = DEFAULT_VIDEO_HEIGHT
        quality_locked = False
        if args and re.fullmatch(r"\d{3,4}p?", args[0].lower()):
            height = int(args[0].lower().rstrip("p"))
            quality_locked = True
            args = args[1:]
            if height not in VIDEO_QUALITIES:
                return await message.reply("Quality must be " + ", ".join(f"{q}p" for q in VIDEO_QUALITIES) + ".")
        query = " ".join(args).strip()
        if not query and message.reply_to_message:
            query = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
        if not query:
            return await message.reply("Usage: <code>/video 720p song name</code>")
        await media_command(client, message, "video", query[:200], height, quality_locked)

    @app.on_callback_query(filters.regex(r"^s:(\w{6}):(\d|x):(p|a|v|x)$"))
    async def pick_callback(client, callback):
        token, index, mode = callback.matches[0].groups()
        if mode == "x":
            picks.drop(token)
            await safe_callback_answer(callback, "Closed")
            if callback.message:
                try:
                    await callback.message.delete()
                except Exception:
                    pass
            return
        results = picks.get(token)
        if not results or int(index) >= len(results) or not callback.message:
            return await safe_callback_answer(callback, "This list expired — please search again.", show_alert=True)
        meta = results[int(index)]
        chat = callback.message.chat
        user = callback.from_user
        access_error = await user_access(client, chat, user.id)
        if access_error:
            return await safe_callback_answer(callback, access_error, show_alert=True)
        if wait := cooldown_left(user.id):
            return await safe_callback_answer(callback, f"⏳ Try again in {wait}s", show_alert=True)
        if mode == "p":
            if chat.type not in GROUP_TYPES:
                return await safe_callback_answer(callback, "Playback works in groups with an active voice chat.", show_alert=True)
            await safe_callback_answer(callback, "▶️ Adding…")
            status = await client.send_message(chat.id, f"🔎 Adding <b>{esc(meta['title'])}</b>…")
            await play_flow(client, chat, user.id, user.first_name or "Someone", status, metas=[meta])
            picks.drop(token)
            try:
                await callback.message.delete()
            except Exception:
                pass
            return
        if not cfg.private_downloads:
            return await safe_callback_answer(callback, "Downloads are disabled.", show_alert=True)
        await safe_callback_answer(callback, "⬇️ On it…")
        status = await client.send_message(chat.id, f"🔎 <b>Preparing…</b>\n<i>{esc(meta['title'])}</i>")
        action = "audio" if mode == "a" else "video"
        delivered = await deliver(client, chat.id, status, meta, action)
        store.log_usage(user.id, chat.id, action, meta["title"], delivered, 0)
        if delivered:
            picks.drop(token)
            try:
                await callback.message.delete()
            except Exception:
                pass

    # ---------------------------------------------------------------------
    # group playback commands
    # ---------------------------------------------------------------------
    @app.on_message(filters.private & filters.command(GROUP_COMMANDS))
    async def private_hint(client, message):
        await message.reply(
            "👥 Playback commands work in <b>groups</b> with an active voice chat.\n"
            "Here in private you can just send me a song name to get an MP3 or video.",
            reply_markup=home_kb(
                await bot_username(),
                show_owner=bool(message.from_user and is_manager(message.from_user.id)),
            ),
        )

    async def start_play(client, message, auto):
        query = get_query(message)
        if not query:
            return await message.reply(f"Usage: <code>/{'auto' if auto else 'play'} song name or link</code>")
        uid, uname = who(message)
        if wait := cooldown_left(uid):
            return await message.reply(f"⏳ Easy there — try again in {wait}s.")
        status = await message.reply("🔎 <b>Finding your track…</b>")
        await play_flow(client, message.chat, uid, uname, status, query=query, auto=auto)

    @app.on_message(filters.command(["play", "gplay", "p"]))
    async def play_command(client, message):
        if message.chat.type not in GROUP_TYPES:
            return
        await start_play(client, message, auto=False)

    @app.on_message(filters.command(["auto"]))
    async def auto_command(client, message):
        if message.chat.type not in GROUP_TYPES:
            return
        await start_play(client, message, auto=True)

    @app.on_message(filters.group & filters.command(["now"]))
    async def now_command(client, message):
        if not player.current.get(message.chat.id):
            return await message.reply(now_text(message.chat.id))
        await publish_card(message.chat.id)

    @app.on_message(filters.group & filters.command(["playlist"]))
    async def playlist_command(client, message):
        uid, uname = who(message)
        access_error = await user_access(client, message.chat, uid)
        if access_error:
            return await message.reply(access_error, disable_web_page_preview=True)
        args = message.command[1:]
        action = args[0].lower() if args else "list"
        chat_id = message.chat.id

        async def playlist_admin_only():
            if message.sender_chat and message.sender_chat.id == chat_id:
                return True
            if not message.from_user:
                return False
            try:
                return is_admin(await client.get_chat_member(chat_id, message.from_user.id))
            except Exception:
                logger.debug("Could not check group playlist admin", exc_info=True)
                return False

        if action == "list":
            rows = await channel_playlist(client, chat_id)
            if not rows:
                return await message.reply(
                    "⭐ <b>Group playlist is empty.</b>\n\n"
                    "🛡 Admins: <code>/playlist add SONG</code>\n"
                    "👥 Members: wait for an admin to add songs."
                )
            lines = ["⭐ <b>Group smart playlist</b>", ""]
            for index, (stored_message, payload) in enumerate(rows, 1):
                added_by = mention(payload.get("added_by"), payload.get("by") or "someone")
                lines.append(f"<code>{index:>2}.</code> {esc(payload.get('title') or 'Untitled')}\n   Added by {added_by}")
            lines += [
                "",
                "👥 Members: <code>/playlist send NUMBER</code>",
                "🛡 Admins: <code>/playlist add SONG</code> · <code>/playlist remove NUMBER</code> · <code>/playlist clear</code>",
            ]
            return await message.reply("\n".join(lines))
        if action == "add":
            if not await playlist_admin_only():
                return await message.reply("⛔ Only admins of this group can add playlist songs.")
            query = " ".join(args[1:]).strip()
            if not query:
                return await message.reply("Usage: <code>/playlist add song name or link</code>")
            status = await message.reply("⭐ <b>Adding to group playlist…</b>")
            path = None
            try:
                meta = (await media.lookup(query, limit=1))[0]
                rows = await channel_playlist(client, chat_id)
                if any(payload.get("source") == meta["source"] for _, payload in rows):
                    return await edit_card(status, "ℹ️ That song is already in this group's playlist.")
                await edit_card(status, "⬇️ <b>Saving song in Song Studio…</b>")
                path, title, artist, duration = await media.download(meta["source"])
                payload = {
                    "chat_id": chat_id,
                    "group": (message.chat.title or "")[:100],
                    "title": title[:200],
                    "source": meta["source"][:700],
                    "duration": duration,
                    "uploader": (artist or meta.get("uploader") or "")[:100],
                    "added_by": uid,
                    "by": (uname or "")[:64],
                    "last_played": 0,
                }
                stored = await client.send_audio(
                    SONG_STUDIO_CHAT_ID,
                    str(path),
                    caption=playlist_marker(payload),
                    title=title,
                    performer=artist or None,
                    duration=duration or None,
                )
                index, pinned = await load_playlist_index(client)
                index.setdefault("groups", {}).setdefault(str(chat_id), []).append(
                    {**payload, "message_id": stored.id}
                )
                await save_playlist_index(client, index, pinned)
                await edit_card(status, f"✅ Added <b>{esc(title)}</b> to this group's Song Studio playlist.")
            except Exception as exc:
                await edit_card(status, download_error(exc))
            finally:
                if path:
                    media.cleanup(path)
            return
        if action in ("send", "get"):
            if len(args) < 2 or not args[1].isdigit():
                return await message.reply("Usage: <code>/playlist send NUMBER</code>")
            rows = await channel_playlist(client, chat_id)
            index = int(args[1]) - 1
            if index < 0 or index >= len(rows):
                return await message.reply("No playlist item at that number.")
            stored_message, _ = rows[index]
            try:
                await client.copy_message(
                    chat_id, SONG_STUDIO_CHAT_ID, stored_message.id, caption=DOWNLOAD_CAPTION
                )
                return
            except Exception as exc:
                return await message.reply(f"⚠️ Could not send playlist song: {friendly_error(exc)}")
        if action == "remove":
            if not await playlist_admin_only():
                return await message.reply("⛔ Only admins of this group can remove playlist songs.")
            if len(args) < 2 or not args[1].isdigit():
                return await message.reply("Usage: <code>/playlist remove NUMBER</code>")
            rows = await channel_playlist(client, chat_id)
            index = int(args[1]) - 1
            if index < 0 or index >= len(rows):
                return await message.reply("No playlist item at that number.")
            stored_message, payload = rows[index]
            await client.delete_messages(SONG_STUDIO_CHAT_ID, stored_message.id)
            playlist_index, pinned = await load_playlist_index(client)
            playlist_index.setdefault("groups", {})[str(chat_id)] = [
                record for record in playlist_index.get("groups", {}).get(str(chat_id), [])
                if int(record.get("message_id", 0)) != stored_message.id
            ]
            await save_playlist_index(client, playlist_index, pinned)
            return await message.reply(f"🗑 Removed <b>{esc(payload.get('title') or 'song')}</b> from the group playlist.")
        if action == "clear":
            if not await playlist_admin_only():
                return await message.reply("⛔ Only admins of this group can clear the playlist.")
            rows = await channel_playlist(client, chat_id)
            if rows:
                await client.delete_messages(SONG_STUDIO_CHAT_ID, [message.id for message, _ in rows])
            playlist_index, pinned = await load_playlist_index(client)
            playlist_index.setdefault("groups", {}).pop(str(chat_id), None)
            await save_playlist_index(client, playlist_index, pinned)
            return await message.reply("🗑 Group smart playlist cleared.")
        await message.reply(
            "Usage: <code>/playlist add SONG</code> · <code>/playlist list</code> · "
            "<code>/playlist send NUMBER</code> · <code>/playlist remove NUMBER</code> · <code>/playlist clear</code>"
        )

    @app.on_message(filters.group & filters.command(["queue", "gqueue"]))
    async def queue_command(client, message):
        await message.reply(queue_text(message.chat.id), reply_markup=queue_kb(message.chat.id))

    @app.on_message(filters.group & filters.command(["history"]))
    async def history_command(client, message):
        items = store.history(message.chat.id, limit=8)
        if not items:
            return await message.reply("🕘 <b>No history yet</b>\n\nSongs you play will show up here.")
        metas = [
            {"title": i.title, "source": i.source, "duration": i.duration, "uploader": i.uploader, "thumb": i.thumb, "live": False}
            for i in items
        ]
        token = picks.put(metas)
        lines = ["🕘 <b>Recently played</b>", ""]
        lines += [f"{NUMBERS[n]} {esc(i.title)}" for n, i in enumerate(items)]
        rows = [[Btn(f"▶️ {i + 1}", callback_data=f"s:{token}:{i}:p") for i in range(len(items))]]
        rows.append([Btn("✖ Close", callback_data=f"s:{token}:x:x")])
        await message.reply("\n".join(lines), reply_markup=Kb(rows))

    @app.on_message(filters.group & filters.command(["pause", "gpause", "resume", "gresume"]))
    async def pause_resume(client, message):
        chat_id = message.chat.id
        if not player.current.get(chat_id):
            return await message.reply("💤 Nothing is playing.")
        if not await guard(client, message):
            return
        want_pause = message.command[0].lstrip("g").lower() == "pause"
        try:
            if want_pause != (chat_id in player.paused):
                await player.toggle_pause(chat_id)
            await message.reply("⏸ Paused." if want_pause else "▶️ Resumed.")
            await refresh_card(chat_id)
        except Exception as exc:
            logger.exception("Pause/resume failed")
            await message.reply(f"⚠️ Command failed: {friendly_error(exc)}")

    @app.on_message(filters.group & filters.command(["skip", "gskip"]))
    async def skip_command(client, message):
        chat_id = message.chat.id
        if not player.current.get(chat_id):
            return await message.reply("💤 Nothing is playing.")
        if not await guard(client, message):
            return
        try:
            new_item = await player.skip(chat_id)
            await message.reply("⏭ Skipped.")
            if new_item:
                await publish_card(chat_id)
        except NoActiveCall as exc:
            await message.reply(f"⚠️ {esc(exc)}")
        except Exception as exc:
            logger.exception("Skip failed")
            await message.reply(f"⚠️ Command failed: {friendly_error(exc)}")

    @app.on_message(filters.group & filters.command(["stop", "gstop", "leave", "gleave"]))
    async def stop_command(client, message):
        chat_id = message.chat.id
        if not await guard(client, message):
            return
        try:
            await player.stop(chat_id)
            await end_card(chat_id, "⏹ <b>Playback stopped</b>")
            await message.reply("⏹ Stopped, queue cleared and left the voice chat.")
        except Exception as exc:
            logger.exception("Stop failed")
            await message.reply(f"⚠️ Command failed: {friendly_error(exc)}")

    @app.on_message(filters.group & filters.command(["loop"]))
    async def loop_command(client, message):
        chat_id = message.chat.id
        if not await guard(client, message):
            return
        arg = message.command[1].lower() if len(message.command) > 1 else ""
        mode = {"track": "one", "song": "one", "queue": "all"}.get(arg, arg)
        mode = player.set_loop(chat_id, mode) if mode in LOOP_MODES else player.cycle_loop(chat_id)
        await message.reply(f"🔁 Loop: <b>{LOOP_LABEL[mode]}</b>")
        await refresh_card(chat_id)

    @app.on_message(filters.group & filters.command(["shuffle"]))
    async def shuffle_command(client, message):
        chat_id = message.chat.id
        if not await guard(client, message):
            return
        if store.count(chat_id) < 2:
            return await message.reply("🔀 Need at least 2 songs waiting in the queue to shuffle.")
        store.shuffle(chat_id)
        await message.reply("🔀 Queue shuffled.")
        await refresh_card(chat_id)

    @app.on_message(filters.group & filters.command(["volume"]))
    async def volume_command(client, message):
        chat_id = message.chat.id
        if len(message.command) < 2:
            return await message.reply(f"🔊 Volume is <b>{player.volume.get(chat_id, cfg.default_volume)}%</b>. Use /volume <i>1-200</i>.")
        if not await guard(client, message):
            return
        if not message.command[1].isdigit():
            return await message.reply("Usage: <code>/volume 1-200</code>")
        if not player.current.get(chat_id):
            return await message.reply("💤 Start a song first, then set the volume.")
        try:
            value = await player.set_volume(chat_id, int(message.command[1]))
            await message.reply(f"🔊 Volume set to <b>{value}%</b>.")
            await refresh_card(chat_id)
        except Exception as exc:
            await message.reply(f"⚠️ Could not change the volume: {friendly_error(exc)}")

    @app.on_message(filters.group & filters.command(["remove"]))
    async def remove_command(client, message):
        if not await guard(client, message):
            return
        if len(message.command) < 2 or not message.command[1].isdigit():
            return await message.reply("Usage: <code>/remove 2</code> (position in /queue)")
        item = store.remove(message.chat.id, int(message.command[1]))
        await message.reply(f"🗑 Removed <b>{esc(item.title)}</b>." if item else "No song at that position.")
        await refresh_card(message.chat.id)

    @app.on_message(filters.group & filters.command(["clear"]))
    async def clear_command(client, message):
        if not await guard(client, message):
            return
        store.clear(message.chat.id)
        await message.reply("🗑 Cleared the waiting queue. The current song keeps playing.")
        await refresh_card(message.chat.id)

    # ---------------------------------------------------------------------
    # player card buttons
    # ---------------------------------------------------------------------
    @app.on_callback_query(filters.regex(r"^(m|q):(\w+):(-?\d+)$"))
    async def controls(client, callback):
        scope, action, raw = callback.matches[0].groups()
        chat_id = int(raw)
        message = callback.message
        if not message or message.chat.id != chat_id:
            return await safe_callback_answer(callback, "This control belongs to another chat.", show_alert=True)
        view_only = scope == "m" and action in ("refresh", "queue", "now")
        if not view_only and not await can_control(client, chat_id, callback.from_user.id):
            return await safe_callback_answer(callback, "🔒 Join the voice chat or be an admin to use the controls.", show_alert=True)

        async def show_now():
            if player.current.get(chat_id):
                await edit_card(message, now_text(chat_id), controls_kb(chat_id))
            else:
                await edit_card(message, now_text(chat_id))

        async def show_queue():
            await edit_card(message, queue_text(chat_id), queue_kb(chat_id))

        toast = ""
        try:
            if scope == "m" and action in ("refresh", "now"):
                await show_now()
                toast = "🔄 Updated"
            elif scope == "m" and action == "queue":
                await show_queue()
            elif scope == "m" and not player.current.get(chat_id):
                return await safe_callback_answer(callback, "Nothing is playing.", show_alert=True)
            elif scope == "m" and action == "pp":
                toast = "⏸ Paused" if await player.toggle_pause(chat_id) else "▶️ Resumed"
                await show_now()
            elif scope == "m" and action == "skip":
                new_item = await player.skip(chat_id)
                toast = "⏭ Skipped"
                if new_item:
                    await publish_card(chat_id)
                else:
                    await edit_card(message, "✅ <b>All songs played</b>\nUse /play to start again.")
            elif scope == "m" and action == "stop":
                await player.stop(chat_id)
                cards.pop(chat_id, None)
                await edit_card(message, "⏹ <b>Playback stopped</b>\nThe queue was cleared.")
                toast = "⏹ Stopped"
            elif scope == "m" and action == "loop":
                toast = f"🔁 Loop: {LOOP_LABEL[player.cycle_loop(chat_id)]}"
                await show_now()
            elif action in ("vdn", "vup"):
                current = player.volume.get(chat_id, cfg.default_volume)
                value = await player.set_volume(chat_id, current + (10 if action == "vup" else -10))
                toast = f"🔊 {value}%"
                await show_now()
            elif action == "shuffle":
                if store.count(chat_id) < 2:
                    return await safe_callback_answer(callback, "Need 2+ songs waiting to shuffle.", show_alert=True)
                store.shuffle(chat_id)
                toast = "🔀 Shuffled"
                await (show_queue() if scope == "q" else show_now())
            elif scope == "q" and action == "clear":
                store.clear(chat_id)
                toast = "🗑 Cleared"
                await show_queue()
            elif scope == "q" and action.startswith("rm") and action[2:].isdigit():
                removed = store.remove(chat_id, int(action[2:]))
                toast = "🗑 Removed" if removed else "Already gone"
                await show_queue()
        except NoActiveCall as exc:
            return await safe_callback_answer(callback, str(exc)[:190], show_alert=True)
        except Exception as exc:
            logger.exception("Player control failed")
            return await safe_callback_answer(callback, f"⚠️ {str(exc)[:180]}", show_alert=True)
        await safe_callback_answer(callback, toast)
