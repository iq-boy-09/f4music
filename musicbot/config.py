from dataclasses import dataclass
import json
import os
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _flag(name: str, default: bool) -> bool:
    raw = _env(name)
    return default if not raw else raw.lower() in {"1", "true", "yes", "on"}


def _number(name: str, default: int, low: int, high: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number in .env") from exc
    return max(low, min(high, value))


def _id_list(name: str) -> frozenset:
    ids = set()
    for part in _env(name).replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as exc:
            raise ConfigError(f"{name} must be a comma-separated list of Telegram user IDs") from exc
    return frozenset(ids)


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    bot_token: str
    bot_session: str
    assistant_session: str
    assistant_phone: str | None
    assistant_user_id: int | None
    assistant_username: str | None
    startup_chat_id: int | None
    state_db: Path
    download_dir: Path
    private_downloads: bool
    group_audio_fallback: bool
    cleanup_sessions_on_exit: bool
    log_level: str
    owner_ids: frozenset
    superowner_ids: frozenset
    max_queue: int
    max_duration: int  # seconds, 0 = unlimited
    download_slots: int
    cooldown: int  # seconds between heavy commands per user
    default_volume: int
    cookies_file: Path | None
    cookies_from_browser: str | None
    telegram_proxy: dict | None

    @classmethod
    def load(cls):
        load_dotenv()
        try:
            api_id = int(_env("API_ID"))
        except ValueError as exc:
            raise ConfigError("API_ID must be a number in .env") from exc
        api_hash = _env("API_HASH")
        bot_token = _env("BOT_TOKEN")
        if not api_hash:
            raise ConfigError("API_HASH is missing in .env")
        if not bot_token:
            raise ConfigError("BOT_TOKEN is missing in .env")
        startup_chat = _env("STARTUP_CHAT_ID")
        try:
            startup_chat_id = int(startup_chat) if startup_chat else None
        except ValueError as exc:
            raise ConfigError("STARTUP_CHAT_ID must be a Telegram chat ID") from exc

        cookies = _env("COOKIES_FILE")
        cookies_file = Path(cookies).expanduser() if cookies else None
        if cookies_file and not cookies_file.is_file():
            raise ConfigError(f"COOKIES_FILE points to a missing file: {cookies_file}")
        cookies_from_browser = _env("COOKIES_FROM_BROWSER") or None
        root = Path(__file__).resolve().parent.parent
        state_db = Path(_env("STATE_DB", str(root / "musicbot.sqlite3")))
        assistant_file = state_db.with_name("assistant_config.json")
        assistant_override = {}
        if assistant_file.is_file():
            try:
                assistant_override = json.loads(assistant_file.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                assistant_override = {}
        proxy_file = state_db.with_name("telegram_proxy.json")
        telegram_proxy = None
        if proxy_file.is_file():
            try:
                saved_proxy = json.loads(proxy_file.read_text(encoding="utf-8"))
                if saved_proxy.get("host") and int(saved_proxy.get("port", 0)) and saved_proxy.get("secret"):
                    telegram_proxy = {
                        "scheme": "mtproto",
                        "hostname": str(saved_proxy["host"]),
                        "port": int(saved_proxy["port"]),
                        "secret": str(saved_proxy["secret"]),
                    }
            except (ValueError, OSError, TypeError, json.JSONDecodeError):
                telegram_proxy = None
        assistant_user = str(
            assistant_override["user_id"]
            if "user_id" in assistant_override else _env("ASSISTANT_USER_ID")
        ).strip()
        try:
            assistant_user_id = int(assistant_user) if assistant_user else None
        except ValueError as exc:
            raise ConfigError("ASSISTANT_USER_ID must be a Telegram user ID") from exc

        owner_ids = _id_list("OWNER_IDS")
        superowner_ids = _id_list("SUPEROWNER_IDS")
        return cls(
            api_id=api_id,
            api_hash=api_hash,
            bot_token=bot_token,
            bot_session=_env("BOT_SESSION", "command_bot_live"),
            assistant_session=str(
                assistant_override.get("session") or _env("ASSISTANT_SESSION", "voice_assistant")
            ).strip(),
            assistant_phone=(str(
                assistant_override["phone"]
                if "phone" in assistant_override else _env("ASSISTANT_PHONE")
            ).strip() or None),
            assistant_user_id=assistant_user_id,
            assistant_username=_env("ASSISTANT_USERNAME") or None,
            startup_chat_id=startup_chat_id,
            state_db=state_db,
            download_dir=Path(_env("DOWNLOAD_DIR", str(root / "downloads"))),
            private_downloads=_flag("PRIVATE_DOWNLOADS", False),
            group_audio_fallback=_flag("GROUP_AUDIO_FALLBACK", True),
            cleanup_sessions_on_exit=_flag("CLEANUP_SESSIONS_ON_EXIT", True),
            log_level=_env("LOG_LEVEL", "INFO").upper(),
            owner_ids=owner_ids | superowner_ids,
            superowner_ids=superowner_ids,
            max_queue=_number("MAX_QUEUE", 10, 1, 30),
            max_duration=_number("MAX_DURATION_MINUTES", 60, 0, 720) * 60,
            download_slots=_number("DOWNLOAD_SLOTS", 2, 1, 8),
            cooldown=_number("COOLDOWN_SECONDS", 3, 0, 60),
            default_volume=_number("DEFAULT_VOLUME", 100, 1, 200),
            cookies_file=cookies_file,
            cookies_from_browser=cookies_from_browser,
            telegram_proxy=telegram_proxy,
        )
