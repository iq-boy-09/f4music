import asyncio
import hashlib
import logging
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

if sys.version_info >= (3, 14):
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client, idle
from pyrogram.connection.transport.tcp.tcp import TCP
from pyrogram.enums import ParseMode
from pyrogram.types import BotCommand

from .config import Config, ConfigError
from .handlers import HOME_TEXT, register
from .media import YouTubeMedia
from .state import IST, QueueStore
from .voice import VoicePlayer


def _patch_pyrogram_disconnect_race():
    """Avoid an unhandled worker task when Pyrogram clears a TCP reader first.

    Pyrofork's receive worker can reach TCP.recv during a normal reconnect or
    shutdown after the connection has already set ``reader`` to None. Treat
    that state as a clean disconnect instead of leaking an AttributeError.
    """
    if getattr(TCP.recv, "_obito_disconnect_safe", False):
        return
    original_recv = TCP.recv

    async def safe_recv(self, length=0):
        if self.reader is None:
            return None
        try:
            return await original_recv(self, length)
        except AttributeError as exc:
            if "object has no attribute 'read'" in str(exc):
                return None
            raise

    safe_recv._obito_disconnect_safe = True
    TCP.recv = safe_recv


_patch_pyrogram_disconnect_race()


def pid_alive(pid: int) -> bool:
    """Check whether a process exists WITHOUT signalling it.

    os.kill(pid, 0) is safe on POSIX, but on Windows it terminates the target
    process, so Windows needs an OpenProcess query instead.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class InstanceLock:
    def __init__(self, bot_token):
        token_hash = hashlib.sha256(bot_token.encode()).hexdigest()[:16]
        self.path = Path(tempfile.gettempdir()) / f"obito_music_{token_hash}.lock"

    def acquire(self):
        try:
            self._create()
        except FileExistsError:
            try:
                pid = int(self.path.read_text(encoding="ascii").strip())
            except (ValueError, OSError):
                pid = 0
            if pid and pid != os.getpid() and pid_alive(pid):
                raise RuntimeError(f"Another copy of this bot is already running (PID {pid}).")
            self.path.unlink(missing_ok=True)  # stale lock from a crashed run
            self._create()

    def _create(self):
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
        finally:
            os.close(fd)

    def release(self):
        self.path.unlink(missing_ok=True)


def cleanup_session_files(*session_names):
    """Remove Pyrogram session databases and journals after shutdown."""
    for session_name in set(session_names):
        if not session_name:
            continue
        base = Path(session_name)
        session_path = base if base.suffix == ".session" else Path(f"{base}.session")
        for path in (session_path, Path(f"{session_path}-journal")):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).warning("Could not remove session file %s", path)


BOT_COMMANDS = [
    BotCommand("start", "open the menu"),
    BotCommand("send", "get an MP3"),
    BotCommand("video", "get a video"),
    BotCommand("play", "play in the voice chat"),
    BotCommand("search", "search and pick a result"),
    BotCommand("ping", "check the bot"),
    BotCommand("help", "show all commands and uses"),
]


def main():
    try:
        config = Config.load()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}. Copy .env.example to .env and fill it in.")

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("pyrogram").setLevel(logging.WARNING)

    instance_lock = InstanceLock(config.bot_token)
    try:
        instance_lock.acquire()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    async def run():
        log = logging.getLogger(__name__)
        # PyTgCalls stores the current asyncio loop during construction, so all
        # clients and services must be created inside this asyncio.run loop.
        bot = Client(
            config.bot_session,
            api_id=config.api_id,
            api_hash=config.api_hash,
            bot_token=config.bot_token,
            parse_mode=ParseMode.HTML,
        )
        assistant = Client(
            config.assistant_session,
            api_id=config.api_id,
            api_hash=config.api_hash,
            phone_number=config.assistant_phone,
        )
        store = QueueStore(config.state_db)
        store.clear_all()
        YouTubeMedia.cleanup_stale_downloads()
        media = YouTubeMedia(config.download_dir, config.cookies_file, config.cookies_from_browser)
        player = VoicePlayer(assistant, store, media, config.max_queue, config.default_volume)
        register(bot, player, media, store, config)
        daily_credit_task = None

        async def daily_credit_notifier():
            """Refill regular accounts at 00:01 Asia/Kolkata and notify recipients."""
            tz = IST
            while True:
                now = datetime.now(tz)
                target = now.replace(hour=0, minute=1, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                await asyncio.sleep(max(1, (target - now).total_seconds()))
                for user_id in store.grant_daily_credits():
                    try:
                        await bot.send_message(
                            user_id,
                            "🎁 <b>Daily credits added!</b>\n"
                            "Your account received <b>10 credits</b> for today.\n"
                            f"Updated balance: <b>{store.user_balance(user_id)}</b> credits.",
                        )
                    except Exception:
                        log.debug("Could not notify daily credit user %s", user_id, exc_info=True)
                    await asyncio.sleep(0.05)
        try:
            for attempt in range(1, 4):
                try:
                    await bot.start()
                    break
                except Exception as exc:
                    log.exception("Startup attempt %s failed", attempt)
                    if bot.is_connected:
                        await bot.stop()
                    if attempt == 3:
                        if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower():
                            raise RuntimeError(
                                "Telegram session database is locked. Stop every other bot process and use a new BOT_SESSION name in .env."
                            ) from exc
                        if isinstance(exc, EOFError):
                            raise RuntimeError(
                                "Voice assistant login was cancelled. Run the file in an interactive terminal and enter the assistant account phone number and Telegram code."
                            ) from exc
                        raise
                    await asyncio.sleep(attempt * 2)
            log.info("Bot is live")
            await bot.set_bot_commands(BOT_COMMANDS)
            daily_credit_task = asyncio.create_task(daily_credit_notifier())
            if config.startup_chat_id is not None:
                try:
                    await bot.send_message(config.startup_chat_id, HOME_TEXT, disable_web_page_preview=True)
                except Exception:
                    log.exception("Could not send startup message")
            await idle()
        finally:
            if daily_credit_task:
                daily_credit_task.cancel()
                try:
                    await daily_credit_task
                except asyncio.CancelledError:
                    pass
            await player.shutdown()
            if assistant.is_connected:
                await assistant.stop()
            if bot.is_connected:
                await bot.stop()
            store.close()
            YouTubeMedia.cleanup_stale_downloads()
            if config.cleanup_sessions_on_exit:
                cleanup_session_files(config.bot_session, config.assistant_session)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        instance_lock.release()
