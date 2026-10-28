import asyncio
import dataclasses
import logging
import time

from pyrogram import raw
import pyrogram.errors as pyrogram_errors
from pyrogram.errors import ChatAdminRequired
from pyrogram.raw.functions.phone import JoinGroupCall

# PyTgCalls sends public_key, while Pyrogram 2.0.106 removed that argument
# from JoinGroupCall. The Telegram method no longer needs it.
if "public_key" not in str(getattr(JoinGroupCall.__init__, "__signature__", "")):
    _join_group_call_init = JoinGroupCall.__init__

    def _compatible_join_group_call_init(self, **kwargs):
        kwargs.pop("public_key", None)
        _join_group_call_init(self, **kwargs)

    JoinGroupCall.__init__ = _compatible_join_group_call_init

# PyTgCalls still imports legacy names from Pyrogram 2.0.106.
if not hasattr(pyrogram_errors, "GroupcallForbidden"):
    pyrogram_errors.GroupcallForbidden = pyrogram_errors.GroupCallInvalid
if not hasattr(pyrogram_errors, "GroupcallInvalid"):
    pyrogram_errors.GroupcallInvalid = pyrogram_errors.GroupCallInvalid
if not hasattr(raw.types, "InputGroupCallSlug"):
    class InputGroupCallSlug:
        def __init__(self, slug):
            self.slug = slug
    raw.types.InputGroupCallSlug = InputGroupCallSlug
if not hasattr(raw.types, "PhoneCallDiscardReasonMigrateConferenceCall"):
    class PhoneCallDiscardReasonMigrateConferenceCall:
        pass
    raw.types.PhoneCallDiscardReasonMigrateConferenceCall = PhoneCallDiscardReasonMigrateConferenceCall

from pytgcalls import PyTgCalls
from pytgcalls.exceptions import NoActiveGroupCall, PyTgCallsAlreadyRunning
from pytgcalls.types import MediaStream, StreamEnded

try:  # not present in every PyTgCalls release
    from pytgcalls.types import ChatUpdate
except ImportError:  # pragma: no cover
    ChatUpdate = None

from .state import QueueItem, QueueStore

logger = logging.getLogger(__name__)

LOOP_MODES = ("off", "one", "all")
PRIMARY_VOICE_CHAT_ID = -1004220556565


class NoActiveCall(RuntimeError):
    pass


class QueueFull(RuntimeError):
    pass


def _closing_mask():
    """Flags meaning the voice chat is gone or the assistant was removed."""
    if ChatUpdate is None:
        return None
    mask = None
    for name in ("KICKED", "LEFT_GROUP", "CLOSED_VOICE_CHAT", "DISCARDED_CALL"):
        flag = getattr(ChatUpdate.Status, name, None)
        if flag is not None:
            mask = flag if mask is None else mask | flag
    return mask


class VoicePlayer:
    def __init__(self, assistant, store: QueueStore, media, max_queue: int = 10, default_volume: int = 100, assistant_user_id: int | None = None, login_success_callback=None, priority_chat_id: int | None = PRIMARY_VOICE_CHAT_ID):
        self.assistant = assistant
        self.expected_assistant_user_id = assistant_user_id
        self.login_success_callback = login_success_callback
        self.priority_chat_id = priority_chat_id
        self.store = store
        self.media = media
        self.max_queue = max_queue
        self.default_volume = default_volume
        self.calls = None
        self.started = False
        self.active = set()
        self.current = {}
        self.paused = set()
        self.loop = {}
        self.volume = {}
        self.locks = {}
        # Optional async hooks set by the UI layer.
        self.on_track = None   # (chat_id, item) -> None, when the queue advances by itself
        self.on_end = None     # (chat_id, reason) -> None, "queue_end" | "closed"
        self.on_error = None   # (chat_id, item, exc) -> None, when a queued track can't be played
        self._skip_until = {}
        self._started_at = {}
        self._paused_at = {}
        self._paused_total = {}
        self._tasks = set()
        self._me = None
        self._mask = _closing_mask()
        self._new_calls()

    # ---- plumbing --------------------------------------------------------
    def _new_calls(self):
        self.calls = PyTgCalls(self.assistant)
        self.calls.on_update()(self._on_update)

    def _lock(self, chat_id):
        return self.locks.setdefault(chat_id, asyncio.Lock())

    def _notify(self, hook, *args):
        """Run a UI hook without blocking (or deadlocking) the player."""
        if hook is None:
            return

        async def runner():
            try:
                await hook(*args)
            except Exception:
                logger.exception("Player hook failed")

        task = asyncio.create_task(runner())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def start(self):
        if self.started:
            return
        try:
            await self.calls.start()
        except (EOFError, PyTgCallsAlreadyRunning):
            # Login failures can leave the running flag set. Recreate the
            # wrapper so a later /play gets a clean login attempt.
            self._new_calls()
            raise
        except Exception:
            self._new_calls()
            raise
        else:
            self.started = True
            if self.login_success_callback:
                await self.login_success_callback()

    async def assistant_me(self):
        if self._me is None:
            self._me = await self.assistant.get_me()
            if self.expected_assistant_user_id and self._me.id != self.expected_assistant_user_id:
                raise RuntimeError(
                    f"The logged-in assistant ID is {self._me.id}, but ASSISTANT_USER_ID is "
                    f"{self.expected_assistant_user_id}. Update the assistant session or .env."
                )
        return self._me

    # ---- state helpers ---------------------------------------------------
    def elapsed(self, chat_id) -> int:
        start = self._started_at.get(chat_id)
        if start is None:
            return 0
        end = self._paused_at.get(chat_id) or time.monotonic()
        return max(0, int(end - start - self._paused_total.get(chat_id, 0)))

    def _forget(self, chat_id):
        self.active.discard(chat_id)
        self.paused.discard(chat_id)
        self.current.pop(chat_id, None)
        self._started_at.pop(chat_id, None)
        self._paused_at.pop(chat_id, None)
        self._paused_total.pop(chat_id, None)

    async def _hang_up(self, chat_id):
        self.active.discard(chat_id)
        self.paused.discard(chat_id)
        try:
            await self.calls.leave_call(chat_id)
        except Exception:
            logger.debug("Call %s was already closed", chat_id, exc_info=True)

    async def _finish(self, chat_id, reason="queue_end"):
        ended_by = ""
        if reason == "queue_end":
            item = self.current.get(chat_id)
            ended_by = getattr(item, "requester", "") if item else ""
        await self._hang_up(chat_id)
        self._forget(chat_id)
        self._notify(self.on_end, chat_id, reason, ended_by)

    async def release_other_chats(self, keep_chat_id: int):
        """Keep the priority group exclusive when it requests the assistant."""
        for chat_id in list(self.active):
            if chat_id == keep_chat_id:
                continue
            await self._hang_up(chat_id)
            self.store.clear(chat_id)
            self._forget(chat_id)
            self._notify(self.on_end, chat_id, "priority_switch", "")

    def priority_blocked(self, chat_id: int) -> bool:
        return bool(
            self.priority_chat_id
            and chat_id != self.priority_chat_id
            and self.priority_chat_id in self.active
        )

    # ---- playback --------------------------------------------------------
    async def enqueue_and_play(self, chat_id: int, item: QueueItem) -> bool:
        """Queue a track. Returns True if it started playing immediately."""
        await self.start()
        async with self._lock(chat_id):
            waiting = self.store.count(chat_id)
            total = waiting + (1 if chat_id in self.active else 0)
            if total >= self.max_queue:
                raise QueueFull(f"This group's queue is full (maximum {self.max_queue} songs).")
            self.store.add(chat_id, item)
            if chat_id in self.active:
                return False
            await self._play_next_locked(chat_id, strict=True)
            return True

    async def _play_next_locked(self, chat_id, strict=False, force_next=False):
        prev = self.current.get(chat_id)
        mode = self.loop.get(chat_id, "off")
        item = None
        if prev is not None:
            if mode == "one" and not force_next:
                item = prev
            elif mode == "all":
                self.store.add(chat_id, prev)
        while True:
            if item is None:
                item = self.store.pop(chat_id)
            if item is None:
                await self._finish(chat_id)
                return None
            try:
                return await self._start_stream(chat_id, item)
            except NoActiveCall:
                raise
            except Exception as exc:
                logger.warning("Could not play %r in chat %s: %s", item.title, chat_id, exc)
                if strict:
                    raise
                self._notify(self.on_error, chat_id, item, exc)
                item = None

    async def _start_stream(self, chat_id, item: QueueItem) -> QueueItem:
        # Stream URLs expire, so resolve at the moment playback starts.
        meta = await self.media.resolve_stream(item.source)
        # This is an audio-only voice player. Explicitly require the audio
        # pipeline instead of relying on AUTO_DETECT, which can produce a
        # connected but silent call for direct YouTube URLs.
        stream = MediaStream(
            meta["url"],
            audio_flags=MediaStream.Flags.REQUIRED,
            video_flags=MediaStream.Flags.IGNORE,
        )
        for attempt in range(1, 4):
            try:
                await self.calls.play(chat_id, stream)
                break
            except NoActiveGroupCall as exc:
                raise NoActiveCall("Start a Telegram voice/video chat in this group first, then use /play again.") from exc
            except ChatAdminRequired as exc:
                raise NoActiveCall(
                    "Telegram requires the assistant account to be a group admin with Manage Video Chats, "
                    "or start a voice chat in this group first."
                ) from exc
            except Exception:
                if attempt == 3:
                    self.active.discard(chat_id)
                    raise
                logger.warning("Playback attempt %s failed for chat %s; retrying", attempt, chat_id, exc_info=True)
                await asyncio.sleep(attempt)
        # Unmute and volume are best-effort: a successful play must not be
        # reported as an error because Telegram delays an update.
        try:
            unmuted = await self.calls.unmute(chat_id)
            if unmuted is False:
                logger.warning("Telegram accepted playback but the assistant audio remained muted in %s", chat_id)
        except Exception:
            logger.warning("Could not unmute chat %s immediately", chat_id, exc_info=True)
        volume = self.volume.setdefault(chat_id, self.default_volume)
        try:
            await self.calls.change_volume_call(chat_id, volume)
        except Exception:
            logger.warning("Could not apply voice volume %s%% in chat %s", volume, chat_id, exc_info=True)
        if not item.duration and meta.get("duration"):
            item = dataclasses.replace(item, duration=meta["duration"])
        if not item.thumb and meta.get("thumb"):
            item = dataclasses.replace(item, thumb=meta["thumb"])
        self.active.add(chat_id)
        self.paused.discard(chat_id)
        self.current[chat_id] = item
        self._started_at[chat_id] = time.monotonic()
        self._paused_at.pop(chat_id, None)
        self._paused_total[chat_id] = 0
        self.store.add_history(chat_id, item)
        return item

    async def skip(self, chat_id):
        """Skip to the next track. Returns the new item, or None if the queue ended."""
        async with self._lock(chat_id):
            # A clean rejoin avoids replaying the previous stream while
            # switching sources on some Telegram clients.
            self._skip_until[chat_id] = time.monotonic() + 3
            await self._hang_up(chat_id)
            return await self._play_next_locked(chat_id, force_next=True)

    async def pause(self, chat_id):
        await self.calls.pause(chat_id)
        if chat_id not in self.paused:
            self.paused.add(chat_id)
            self._paused_at[chat_id] = time.monotonic()

    async def resume(self, chat_id):
        await self.calls.resume(chat_id)
        if chat_id in self.paused:
            self.paused.discard(chat_id)
            paused_at = self._paused_at.pop(chat_id, None)
            if paused_at is not None:
                self._paused_total[chat_id] = self._paused_total.get(chat_id, 0) + time.monotonic() - paused_at

    async def toggle_pause(self, chat_id) -> bool:
        """Returns True if playback is now paused."""
        if chat_id in self.paused:
            await self.resume(chat_id)
            return False
        await self.pause(chat_id)
        return True

    async def set_volume(self, chat_id, volume: int):
        volume = max(1, min(200, int(volume)))
        await self.calls.change_volume_call(chat_id, volume)
        self.volume[chat_id] = volume
        return volume

    def set_loop(self, chat_id, mode: str):
        if mode not in LOOP_MODES:
            raise ValueError(mode)
        self.loop[chat_id] = mode
        return mode

    def cycle_loop(self, chat_id):
        mode = self.loop.get(chat_id, "off")
        return self.set_loop(chat_id, LOOP_MODES[(LOOP_MODES.index(mode) + 1) % len(LOOP_MODES)])

    async def stop(self, chat_id):
        """Stop playback, clear the queue and leave the call."""
        async with self._lock(chat_id):
            self.store.clear(chat_id)
            self._skip_until[chat_id] = time.monotonic() + 3
            await self._hang_up(chat_id)
            self._forget(chat_id)

    leave = stop

    async def shutdown(self):
        for chat_id in list(self.active):
            await self._hang_up(chat_id)

    async def is_participant(self, chat_id, user_id) -> bool:
        if not self.started:
            return False
        try:
            participants = await self.calls.get_participants(chat_id)
        except Exception:
            logger.debug("Could not read participants for %s", chat_id, exc_info=True)
            return False
        return any(getattr(p, "user_id", None) == user_id for p in (participants or []))

    # ---- PyTgCalls events ------------------------------------------------
    async def _on_update(self, _, update):
        chat_id = getattr(update, "chat_id", None)
        if chat_id is None:
            return
        within_skip = time.monotonic() <= self._skip_until.get(chat_id, 0)

        if ChatUpdate is not None and isinstance(update, ChatUpdate):
            if self._mask is None or within_skip or not (update.status & self._mask):
                return
            async with self._lock(chat_id):
                self.store.clear(chat_id)
                await self._hang_up(chat_id)
                had_track = chat_id in self.current
                self._forget(chat_id)
            if had_track:
                self._notify(self.on_end, chat_id, "closed")
            return

        if not isinstance(update, StreamEnded):
            return
        deadline = self._skip_until.pop(chat_id, None)
        if deadline is not None and time.monotonic() <= deadline:
            return  # echo of the stream we just replaced
        async with self._lock(chat_id):
            try:
                item = await self._play_next_locked(chat_id)
            except NoActiveCall:
                self.store.clear(chat_id)
                self._forget(chat_id)
                self._notify(self.on_end, chat_id, "closed")
                return
            except Exception:
                logger.exception("Could not advance queue for chat %s", chat_id)
                return
        if item is not None:
            self._notify(self.on_track, chat_id, item)


class _PoolMap:
    def __init__(self, pool, attribute):
        self.pool = pool
        self.attribute = attribute

    def _map(self, key):
        player = self.pool.for_chat(key)
        return getattr(player, self.attribute) if player else {}

    def get(self, key, default=None):
        return self._map(key).get(key, default)

    def __contains__(self, key):
        return key in self._map(key)

    def __getitem__(self, key):
        return self._map(key)[key]

    def __setitem__(self, key, value):
        self._map(key)[key] = value

    def pop(self, key, default=None):
        return self._map(key).pop(key, default)


class _PoolSet:
    def __init__(self, pool, attribute):
        self.pool = pool
        self.attribute = attribute

    def __contains__(self, key):
        return any(key in getattr(player, self.attribute) for player in self.pool.players.values())

    def __len__(self):
        return sum(len(getattr(player, self.attribute)) for player in self.pool.players.values())

    def __iter__(self):
        values = set()
        for player in self.pool.players.values():
            values.update(getattr(player, self.attribute))
        return iter(values)


class AssistantPool:
    """Route each group to an owner-assigned assistant VoicePlayer."""

    def __init__(self, players, bridges, assignment_path, priority_chat_id=PRIMARY_VOICE_CHAT_ID, owner_ids=()):
        self.players = {int(user_id): player for user_id, player in players.items()}
        self.bridges = {int(user_id): bridge for user_id, bridge in bridges.items()}
        self.assignment_path = assignment_path
        self.priority_chat_id = priority_chat_id
        self.owner_ids = tuple(owner_ids)
        self.current = _PoolMap(self, "current")
        self.paused = _PoolSet(self, "paused")
        self.loop = _PoolMap(self, "loop")
        self.volume = _PoolMap(self, "volume")
        self.active = _PoolSet(self, "active")
        self._active_bridge = None
        self._request_context = (None, "")
        self.on_track = None
        self.on_end = None
        self.on_error = None
        for player in self.players.values():
            player.priority_chat_id = None
            player.on_track = self._forward_track
            player.on_end = self._forward_end
            player.on_error = self._forward_error

    def _assignment_data(self):
        try:
            import json
            from pathlib import Path
            data = json.loads(Path(self.assignment_path).read_text(encoding="utf-8"))
            return data.get("assistants", [])
        except Exception:
            return []

    def _allowed(self, user_id, chat_id):
        for entry in self._assignment_data():
            if int(entry.get("user_id", 0)) != int(user_id):
                continue
            assignments = entry.get("assignments", "all")
            if assignments == "all" or int(chat_id) in {int(value) for value in assignments}:
                return True
        return False

    def for_chat(self, chat_id):
        exact, universal = [], []
        entries = self._assignment_data()
        if not entries:
            universal = list(self.players)
        for user_id in self.players:
            if any(int(item.get("user_id", 0)) == user_id and int(chat_id) in {int(v) for v in item.get("assignments", [])} for item in entries if item.get("assignments") != "all"):
                exact.append(user_id)
            if any(int(item.get("user_id", 0)) == user_id and item.get("assignments") == "all" for item in entries):
                universal.append(user_id)
        candidates = exact or universal
        if not candidates:
            return None
        # Prefer an idle assistant; keep the current owner of this group stable.
        candidates.sort(key=lambda user_id: (0 if chat_id in self.players[user_id].active else len(self.players[user_id].active), user_id))
        for user_id in candidates:
            candidate = self.players[user_id]
            if chat_id in candidate.active or not candidate.active:
                return candidate
        # The priority group may reclaim its assigned assistant; the caller
        # releases other calls before starting it.
        if chat_id == self.priority_chat_id:
            return self.players[candidates[0]]
        return None

    def _bridge_for(self, player):
        for user_id, candidate in self.players.items():
            if candidate is player:
                return self.bridges.get(user_id)
        return None

    async def _forward_track(self, *args):
        if self.on_track:
            await self.on_track(*args)

    async def _forward_end(self, *args):
        if self.on_end:
            await self.on_end(*args)

    async def _forward_error(self, *args):
        if self.on_error:
            await self.on_error(*args)

    async def start(self):
        requested_chat = self._request_context[0]
        player = self.for_chat(requested_chat if requested_chat is not None else (self.priority_chat_id or 0))
        if player:
            bridge = self._bridge_for(player)
            self._active_bridge = bridge
            if bridge:
                bridge.set_request_context(*self._request_context)
            await player.start()

    async def assistant_me(self, chat_id):
        player = self.for_chat(chat_id)
        if not player:
            return None
        return await player.assistant_me()

    async def enqueue_and_play(self, chat_id, item):
        player = self.for_chat(chat_id)
        if not player:
            raise NoActiveCall("No Music Assistant is available. Try again later.")
        self._active_bridge = self._bridge_for(player)
        return await player.enqueue_and_play(chat_id, item)

    def priority_blocked(self, chat_id):
        return bool(self.priority_chat_id and chat_id != self.priority_chat_id and self.priority_chat_id in self.active)

    async def release_other_chats(self, keep_chat_id):
        for player in self.players.values():
            for chat_id in list(player.active):
                if chat_id != keep_chat_id:
                    await player.stop(chat_id)

    def _route(self, chat_id):
        player = self.for_chat(chat_id)
        if not player:
            raise NoActiveCall("No Music Assistant is available. Try again later.")
        self._active_bridge = self._bridge_for(player)
        return player

    async def skip(self, chat_id): return await self._route(chat_id).skip(chat_id)
    async def toggle_pause(self, chat_id): return await self._route(chat_id).toggle_pause(chat_id)
    async def set_volume(self, chat_id, volume): return await self._route(chat_id).set_volume(chat_id, volume)
    def set_loop(self, chat_id, mode): return self._route(chat_id).set_loop(chat_id, mode)
    def cycle_loop(self, chat_id): return self._route(chat_id).cycle_loop(chat_id)
    async def stop(self, chat_id): return await self._route(chat_id).stop(chat_id)
    async def is_participant(self, chat_id, user_id): return await self._route(chat_id).is_participant(chat_id, user_id)

    async def shutdown(self):
        for player in self.players.values():
            await player.shutdown()

    @property
    def assistant(self):
        player = self.for_chat(self.priority_chat_id or 0)
        return player.assistant if player else None

    @property
    def started(self):
        return any(player.started for player in self.players.values())

    # LoginBridge-compatible methods used by handlers.
    @property
    def active_login(self):
        return bool(self._active_bridge and self._active_bridge.active)

    def set_request_context(self, chat_id, title=""):
        self._request_context = (chat_id, title or "")
        if self._active_bridge:
            self._active_bridge.set_request_context(chat_id, title)

    async def submit(self, value):
        return bool(self._active_bridge and await self._active_bridge.submit(value))

    async def input(self, prompt="", hide=False):
        if not self._active_bridge:
            raise EOFError("No assistant login is active")
        return await self._active_bridge.input(prompt, hide)

    async def notify_success(self):
        if self._active_bridge:
            await self._active_bridge.notify_success()
