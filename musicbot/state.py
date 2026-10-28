from dataclasses import dataclass
import random
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import List

IST = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class QueueItem:
    title: str
    source: str  # page URL; the temporary stream URL is resolved when playback starts
    requested_by: int = 0
    requester: str = ""
    duration: int = 0
    uploader: str = ""
    thumb: str = ""
    id: int = 0


_COLUMNS = "title,source,requested_by,requester,duration,uploader,thumb"


class QueueStore:
    def __init__(self, path):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        # The queue is runtime-only, so an old-format table is simply replaced.
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(queue)")}
        if columns and "source" not in columns:
            self._db.execute("DROP TABLE queue")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                requested_by INTEGER NOT NULL DEFAULT 0,
                requester TEXT NOT NULL DEFAULT '',
                duration INTEGER NOT NULL DEFAULT 0,
                uploader TEXT NOT NULL DEFAULT '',
                thumb TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_queue_chat ON queue(chat_id, id);
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL DEFAULT '',
                joined REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                joined REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                requested_by INTEGER NOT NULL DEFAULT 0,
                requester TEXT NOT NULL DEFAULT '',
                duration INTEGER NOT NULL DEFAULT 0,
                uploader TEXT NOT NULL DEFAULT '',
                thumb TEXT NOT NULL DEFAULT '',
                played_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_history_chat ON history(chat_id, id);
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER NOT NULL,
                added_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS force_channels (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                invite_link TEXT NOT NULL DEFAULT '',
                added_by INTEGER NOT NULL,
                added_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS premium_users (
                user_id INTEGER PRIMARY KEY,
                expires_at REAL,
                granted_by INTEGER NOT NULL,
                granted_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS redeem_codes (
                code TEXT PRIMARY KEY,
                credits INTEGER NOT NULL,
                uses_left INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS referrals (
                referred_id INTEGER PRIMARY KEY,
                referrer_id INTEGER NOT NULL,
                rewarded_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                query TEXT NOT NULL DEFAULT '',
                success INTEGER NOT NULL DEFAULT 0,
                credits INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_logs(user_id, id);
            CREATE TABLE IF NOT EXISTS studio_audio (
                title_key TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                requested_by INTEGER NOT NULL DEFAULT 0,
                requester TEXT NOT NULL DEFAULT '',
                duration INTEGER NOT NULL DEFAULT 0,
                uploader TEXT NOT NULL DEFAULT '',
                thumb TEXT NOT NULL DEFAULT '',
                added_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_playlists_chat ON playlists(chat_id, id);
            """
        )
        self._ensure_column("users", "credits", "INTEGER NOT NULL DEFAULT 10")
        self._ensure_column("users", "credit_day", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("users", "username", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("chats", "username", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("force_channels", "invite_link", "TEXT NOT NULL DEFAULT ''")
        self._db.commit()

    def _ensure_column(self, table: str, name: str, definition: str):
        columns = {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}
        if name not in columns:
            self._db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    # ---- queue -----------------------------------------------------------
    @staticmethod
    def _values(item: QueueItem):
        return (item.title, item.source, item.requested_by, item.requester,
                item.duration, item.uploader, item.thumb)

    def add(self, chat_id: int, item: QueueItem):
        with self._lock:
            self._db.execute(
                f"INSERT INTO queue(chat_id,{_COLUMNS}) VALUES(?,?,?,?,?,?,?,?)",
                (chat_id, *self._values(item)),
            )
            self._db.commit()

    def list(self, chat_id: int) -> List[QueueItem]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_COLUMNS},id FROM queue WHERE chat_id=? ORDER BY id", (chat_id,)
            ).fetchall()
        return [QueueItem(*row[:7], id=row[7]) for row in rows]

    def count(self, chat_id: int) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM queue WHERE chat_id=?", (chat_id,)).fetchone()[0]

    def total(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM queue").fetchone()[0]

    def pop(self, chat_id: int):
        with self._lock:
            row = self._db.execute(
                f"SELECT {_COLUMNS},id FROM queue WHERE chat_id=? ORDER BY id LIMIT 1", (chat_id,)
            ).fetchone()
            if not row:
                return None
            self._db.execute("DELETE FROM queue WHERE id=?", (row[7],))
            self._db.commit()
        return QueueItem(*row[:7])

    def remove(self, chat_id: int, position: int):
        """Remove the 1-based position from the waiting queue."""
        if position < 1:
            return None
        with self._lock:
            row = self._db.execute(
                f"SELECT {_COLUMNS},id FROM queue WHERE chat_id=? ORDER BY id LIMIT 1 OFFSET ?",
                (chat_id, position - 1),
            ).fetchone()
            if not row:
                return None
            self._db.execute("DELETE FROM queue WHERE id=?", (row[7],))
            self._db.commit()
        return QueueItem(*row[:7])

    def shuffle(self, chat_id: int) -> int:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_COLUMNS} FROM queue WHERE chat_id=? ORDER BY id", (chat_id,)
            ).fetchall()
            if len(rows) < 2:
                return len(rows)
            random.shuffle(rows)
            self._db.execute("DELETE FROM queue WHERE chat_id=?", (chat_id,))
            self._db.executemany(
                f"INSERT INTO queue(chat_id,{_COLUMNS}) VALUES(?,?,?,?,?,?,?,?)",
                [(chat_id, *row) for row in rows],
            )
            self._db.commit()
        return len(rows)

    def clear(self, chat_id: int):
        with self._lock:
            self._db.execute("DELETE FROM queue WHERE chat_id=?", (chat_id,))
            self._db.commit()

    def clear_all(self):
        # Playback rows are not reliable after a process restart.
        with self._lock:
            self._db.execute("DELETE FROM queue")
            self._db.commit()

    # ---- group playlists -----------------------------------------------
    def playlist_add(self, chat_id: int, item: QueueItem) -> bool:
        with self._lock:
            exists = self._db.execute(
                "SELECT 1 FROM playlists WHERE chat_id=? AND source=? LIMIT 1",
                (chat_id, item.source),
            ).fetchone()
            if exists:
                return False
            self._db.execute(
                "INSERT INTO playlists(chat_id,title,source,requested_by,requester,duration,uploader,thumb,added_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (chat_id, item.title, item.source, item.requested_by, item.requester,
                 item.duration, item.uploader, item.thumb, time.time()),
            )
            self._db.commit()
            return True

    def playlist(self, chat_id: int, limit: int = 50):
        with self._lock:
            rows = self._db.execute(
                "SELECT title,source,requested_by,requester,duration,uploader,thumb,id "
                "FROM playlists WHERE chat_id=? ORDER BY id LIMIT ?",
                (chat_id, limit),
            ).fetchall()
        return [(QueueItem(*row[:7], id=row[7]), row[7]) for row in rows]

    def playlist_remove(self, chat_id: int, position: int):
        if position < 1:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT title,source,requested_by,requester,duration,uploader,thumb,id "
                "FROM playlists WHERE chat_id=? ORDER BY id LIMIT 1 OFFSET ?",
                (chat_id, position - 1),
            ).fetchone()
            if not row:
                return None
            self._db.execute("DELETE FROM playlists WHERE id=?", (row[7],))
            self._db.commit()
        return QueueItem(*row[:7], id=row[7])

    def playlist_remove_id(self, item_id: int):
        with self._lock:
            self._db.execute("DELETE FROM playlists WHERE id=?", (item_id,))
            self._db.commit()

    def playlist_clear(self, chat_id: int):
        with self._lock:
            self._db.execute("DELETE FROM playlists WHERE chat_id=?", (chat_id,))
            self._db.commit()

    # ---- users -----------------------------------------------------------
    def register_user(self, user_id: int, username: str = ""):
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO users(user_id, username, joined, credits, credit_day) VALUES(?,?,?,?,?)",
                (user_id, username or "", time.time(), 10, self._today()),
            )
            if username:
                self._db.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))
            self._db.commit()

    @staticmethod
    def _today():
        return datetime.now(IST).date().isoformat()

    def daily_credits(self, user_id: int, amount: int = 10):
        self.register_user(user_id)
        with self._lock:
            today = self._today()
            self._db.execute(
                "UPDATE users SET credits=credits+?, credit_day=? WHERE user_id=? AND credit_day<>?",
                (amount, today, user_id, today),
            )
            self._db.commit()

    def grant_daily_credits(self, amount: int = 10):
        """Grant today's refill once and return users who received it."""
        today = self._today()
        with self._lock:
            rows = self._db.execute(
                "SELECT user_id FROM users WHERE credit_day<>? OR credit_day=''", (today,)
            ).fetchall()
            if rows:
                self._db.execute(
                    "UPDATE users SET credits=credits+?, credit_day=? WHERE credit_day<>? OR credit_day=''",
                    (amount, today, today),
                )
                self._db.commit()
        return [int(row[0]) for row in rows]

    def user_balance(self, user_id: int):
        self.daily_credits(user_id)
        with self._lock:
            row = self._db.execute("SELECT credits FROM users WHERE user_id=?", (user_id,)).fetchone()
            return int(row[0]) if row else 0

    def is_premium(self, user_id: int) -> bool:
        with self._lock:
            row = self._db.execute("SELECT expires_at FROM premium_users WHERE user_id=?", (user_id,)).fetchone()
        return bool(row and (row[0] is None or row[0] > time.time()))

    def consume_credits(self, user_id: int, amount: int) -> bool:
        if self.is_premium(user_id):
            return True
        self.daily_credits(user_id)
        with self._lock:
            cur = self._db.execute(
                "UPDATE users SET credits=credits-? WHERE user_id=? AND credits>=?",
                (amount, user_id, amount),
            )
            self._db.commit()
            return cur.rowcount == 1

    def add_credits(self, user_id: int, amount: int):
        self.register_user(user_id)
        with self._lock:
            self._db.execute("UPDATE users SET credits=credits+? WHERE user_id=?", (amount, user_id))
            self._db.commit()

    def users(self) -> List[int]:
        with self._lock:
            return [row[0] for row in self._db.execute("SELECT user_id FROM users")]

    def remove_user(self, user_id: int):
        """Remove a Telegram bot account accidentally recorded as a user."""
        with self._lock:
            self._db.execute("DELETE FROM users WHERE user_id=?", (user_id,))
            self._db.commit()

    def has_user(self, user_id: int) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is not None

    def user_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def register_chat(self, chat_id: int, title: str = "", username: str = ""):
        with self._lock:
            self._db.execute(
                "INSERT INTO chats(chat_id,title,username,joined) VALUES(?,?,?,?) "
                "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, username=excluded.username",
                (chat_id, title or "", username or "", time.time()),
            )
            self._db.commit()

    def chats(self) -> List[int]:
        with self._lock:
            return [row[0] for row in self._db.execute("SELECT chat_id FROM chats")]

    def user_directory(self):
        with self._lock:
            return self._db.execute("SELECT user_id,username,credits FROM users ORDER BY joined DESC").fetchall()

    def chat_directory(self):
        with self._lock:
            return self._db.execute("SELECT chat_id,title,username FROM chats ORDER BY joined DESC").fetchall()

    # ---- access control -------------------------------------------------
    def add_admin(self, user_id: int, added_by: int):
        with self._lock:
            self._db.execute(
                "INSERT INTO admins(user_id,added_by,added_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET added_by=excluded.added_by, added_at=excluded.added_at",
                (user_id, added_by, time.time()),
            )
            self._db.commit()

    def remove_admin(self, user_id: int):
        with self._lock:
            self._db.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
            self._db.commit()

    def is_admin(self, user_id: int) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone() is not None

    def admins(self) -> List[int]:
        with self._lock:
            return [row[0] for row in self._db.execute("SELECT user_id FROM admins ORDER BY added_at")]

    def admin_directory(self):
        with self._lock:
            return self._db.execute(
                "SELECT a.user_id, COALESCE(u.username, ''), a.added_by, a.added_at "
                "FROM admins a LEFT JOIN users u ON u.user_id=a.user_id ORDER BY a.added_at"
            ).fetchall()

    def add_force_channel(self, chat_id: int, title: str, username: str, invite_link: str, added_by: int):
        with self._lock:
            self._db.execute(
                "INSERT INTO force_channels(chat_id,title,username,invite_link,added_by,added_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, username=excluded.username, invite_link=excluded.invite_link",
                (chat_id, title or "", username or "", invite_link or "", added_by, time.time()),
            )
            self._db.commit()

    def remove_force_channel(self, chat_id: int):
        with self._lock:
            self._db.execute("DELETE FROM force_channels WHERE chat_id=?", (chat_id,))
            self._db.commit()

    def force_channels(self):
        with self._lock:
            return self._db.execute("SELECT chat_id,title,username,invite_link FROM force_channels ORDER BY added_at").fetchall()

    def set_premium(self, user_id: int, expires_at: float | None, granted_by: int):
        self.register_user(user_id)
        with self._lock:
            self._db.execute(
                "INSERT INTO premium_users(user_id,expires_at,granted_by,granted_at) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at, granted_by=excluded.granted_by, granted_at=excluded.granted_at",
                (user_id, expires_at, granted_by, time.time()),
            )
            self._db.commit()

    def premium_users(self):
        with self._lock:
            return self._db.execute("SELECT user_id,expires_at FROM premium_users ORDER BY granted_at DESC").fetchall()

    def create_code(self, credits: int, uses: int, created_by: int) -> str:
        while True:
            code = f"ALIEN{secrets.randbelow(100000000):08d}"
            try:
                with self._lock:
                    self._db.execute(
                        "INSERT INTO redeem_codes(code,credits,uses_left,created_by,created_at) VALUES(?,?,?,?,?)",
                        (code, credits, uses, created_by, time.time()),
                    )
                    self._db.commit()
                return code
            except sqlite3.IntegrityError:
                continue

    def redeem_code(self, code: str, user_id: int):
        with self._lock:
            row = self._db.execute("SELECT credits,uses_left FROM redeem_codes WHERE code=?", (code.upper(),)).fetchone()
            if not row or row[1] <= 0:
                return None
            self._db.execute("UPDATE redeem_codes SET uses_left=uses_left-1 WHERE code=?", (code.upper(),))
            self._db.commit()
        self.add_credits(user_id, int(row[0]))
        return int(row[0])

    def claim_referral(self, referred_id: int, referrer_id: int, reward: int = 5):
        if not referrer_id or referred_id == referrer_id:
            return None
        self.register_user(referred_id)
        self.register_user(referrer_id)
        with self._lock:
            row = self._db.execute("SELECT referrer_id FROM referrals WHERE referred_id=?", (referred_id,)).fetchone()
            if row:
                return None
            self._db.execute(
                "INSERT INTO referrals(referred_id,referrer_id,rewarded_at) VALUES(?,?,?)",
                (referred_id, referrer_id, time.time()),
            )
            self._db.commit()
        self.add_credits(referrer_id, reward)
        return referrer_id

    def referral_count(self, user_id: int) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (user_id,)).fetchone()[0]

    def log_usage(self, user_id: int, chat_id: int, action: str, query: str, success: bool, credits: int = 0):
        with self._lock:
            self._db.execute(
                "INSERT INTO usage_logs(user_id,chat_id,action,query,success,credits,created_at) VALUES(?,?,?,?,?,?,?)",
                (user_id, chat_id, action, query[:500], int(success), credits, time.time()),
            )
            self._db.commit()

    def usage(self, user_id: int | None = None, limit: int = 30):
        with self._lock:
            if user_id is None:
                return self._db.execute(
                    "SELECT user_id,chat_id,action,query,success,credits,created_at FROM usage_logs ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return self._db.execute(
                "SELECT user_id,chat_id,action,query,success,credits,created_at FROM usage_logs WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()

    # ---- Song Studio cache ----------------------------------------------
    @staticmethod
    def _studio_key(title: str) -> str:
        return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in (title or "")).split())

    def studio_save(self, title: str, message_id: int):
        key = self._studio_key(title)
        if not key:
            return
        with self._lock:
            self._db.execute(
                "INSERT INTO studio_audio(title_key,title,message_id,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(title_key) DO UPDATE SET title=excluded.title, message_id=excluded.message_id, created_at=excluded.created_at",
                (key, title, int(message_id), time.time()),
            )
            self._db.commit()

    def studio_find(self, title: str):
        key = self._studio_key(title)
        if not key:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT title,message_id FROM studio_audio WHERE title_key=?", (key,)
            ).fetchone()
            if row:
                return row
            # Match a short request against a previously cached full title.
            rows = self._db.execute("SELECT title,message_id FROM studio_audio ORDER BY created_at DESC LIMIT 500").fetchall()
        query_words = set(key.split())
        for cached_title, message_id in rows:
            cached_words = set(self._studio_key(cached_title).split())
            if query_words and (query_words <= cached_words or cached_words <= query_words):
                return cached_title, message_id
        return None

    # ---- history ---------------------------------------------------------
    def add_history(self, chat_id: int, item: QueueItem):
        with self._lock:
            self._db.execute(
                f"INSERT INTO history(chat_id,{_COLUMNS},played_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (chat_id, *self._values(item), time.time()),
            )
            self._db.execute(
                "DELETE FROM history WHERE chat_id=? AND id NOT IN "
                "(SELECT id FROM history WHERE chat_id=? ORDER BY id DESC LIMIT 100)",
                (chat_id, chat_id),
            )
            self._db.commit()

    def history(self, chat_id: int, limit: int = 8) -> List[QueueItem]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_COLUMNS},MAX(played_at) AS t FROM history WHERE chat_id=? "
                "GROUP BY source ORDER BY t DESC LIMIT ?",
                (chat_id, limit),
            ).fetchall()
        return [QueueItem(*row[:7]) for row in rows]

    def close(self):
        with self._lock:
            self._db.close()
