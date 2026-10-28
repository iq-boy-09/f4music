import asyncio
import logging
import re
import shutil
import tempfile
from pathlib import Path

from yt_dlp import YoutubeDL

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"^https?://", re.I)
VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})")
BARE_ID_RE = re.compile(r"^[\w-]{11}$")
YOUTUBE_HOST_RE = re.compile(r"(?:youtube\.com|youtu\.be)$", re.I)

# Keep the default client first; explicit clients are only fallbacks.
PLAYER_CLIENTS = (None, "web", "mweb", "ios", "tv_samsung")
FATAL_MARKERS = (
    "video unavailable", "private video", "has been removed", "copyright",
    "members-only", "members only", "account associated", "this video is not available",
)
NOISE_WORDS = ("cover", "remix", "reaction", "karaoke", "slowed", "sped up", "nightcore", "8d", "tutorial")


class _YtDlpLogger:
    """Keep yt-dlp extractor diagnostics inside the bot logger."""

    def debug(self, message):
        # yt-dlp sends ordinary request chatter through debug().
        return None

    def warning(self, message):
        logger.debug("yt-dlp: %s", message)

    def error(self, message):
        logger.debug("yt-dlp: %s", message)


class MediaError(RuntimeError):
    pass


def is_url(text: str) -> bool:
    return bool(URL_RE.match(text or ""))


class YouTubeMedia:
    def __init__(
        self,
        download_dir: Path,
        cookies_file: Path | None = None,
        cookies_from_browser: str | None = None,
    ):
        # Downloads are written to the system temp directory, never here.
        self.download_dir = download_dir
        self.cookies_file = cookies_file
        self.cookies_from_browser = cookies_from_browser
        self.runtimes = {name: {} for name in ("deno", "node", "bun") if shutil.which(name)}

    def set_cookies_file(self, path: Path | None):
        """Update cookies at runtime without changing the default cookie-less mode."""
        self.cookies_file = Path(path) if path else None

    # ---- helpers ---------------------------------------------------------
    def _opts(self, **extra):
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "retries": 3,
            "extractor_retries": 3,
            "fragment_retries": 3,
            "file_access_retries": 3,
            "socket_timeout": 15,
            "geo_bypass": True,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
            "logger": _YtDlpLogger(),
        }
        if self.runtimes:
            opts["js_runtimes"] = self.runtimes
        if self.cookies_file:
            opts["cookiefile"] = str(self.cookies_file)
        if self.cookies_from_browser:
            opts["cookiesfrombrowser"] = self.cookies_from_browser
        opts.update(extra)
        return opts

    @staticmethod
    def _meta(entry: dict, fallback_url: str = "") -> dict:
        vid = entry.get("id") or ""
        url = entry.get("webpage_url") or entry.get("original_url") or entry.get("url") or fallback_url or ""
        if url and not is_url(url) and BARE_ID_RE.match(url):
            url = f"https://www.youtube.com/watch?v={url}"
        if not url and BARE_ID_RE.match(vid):
            url = f"https://www.youtube.com/watch?v={vid}"
        found = VIDEO_ID_RE.search(url)
        # Prefer the source thumbnail when yt-dlp provides one; otherwise use
        # YouTube's larger thumbnail variant for cleaner player cards.
        thumb = (entry.get("thumbnail") or "")
        if not thumb and found:
            thumb = f"https://i.ytimg.com/vi/{found.group(1)}/maxresdefault.jpg"
        elif "i.ytimg.com" in thumb and "hqdefault" in thumb:
            thumb = thumb.replace("hqdefault", "maxresdefault")
        return {
            "title": (entry.get("title") or "Untitled").strip(),
            "source": url,
            "duration": int(entry.get("duration") or 0),
            "uploader": (entry.get("uploader") or entry.get("channel") or "").strip(),
            "thumb": thumb,
            "live": bool(entry.get("is_live") or entry.get("live_status") in ("is_live", "is_upcoming")),
        }

    @staticmethod
    def _score(meta: dict, query: str) -> int:
        q = query.lower()
        title = meta["title"].lower()
        words = re.findall(r"\w+", q)
        title_words = set(re.findall(r"\w+", title))
        score = sum(word in title_words for word in words) * 10
        score += 5 if title.startswith(q) else 0
        score -= sum(3 for term in NOISE_WORDS if term in title and term not in q)
        duration = meta["duration"]
        if duration and not 60 <= duration <= 900:  # prefer normal song lengths
            score -= 4
        return score

    @staticmethod
    def _fatal(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(marker in text for marker in FATAL_MARKERS)

    @staticmethod
    def _bot_check(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(marker in text for marker in (
            "sign in to confirm", "not a bot", "page needs to be reloaded",
            "confirm you're not a bot", "confirm you’re not a bot",
        ))

    @staticmethod
    def _auth_message() -> str:
        return (
            "YouTube sign-in verification blocked this request. "
            "Set COOKIES_FROM_BROWSER=chrome/edge/firefox or COOKIES_FILE in .env and restart."
        )

    # ---- search / lookup -------------------------------------------------
    def _search_sync(self, query: str, limit: int = 8) -> list:
        with YoutubeDL(self._opts(extract_flat=True, skip_download=True)) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        results = []
        for entry in (info or {}).get("entries") or []:
            if not entry:
                continue
            meta = self._meta(entry)
            if meta["source"] and not meta["live"]:
                results.append(meta)
        if not results:
            raise MediaError(f"No YouTube results found for: {query}")
        return results

    def _lookup_sync(self, query: str, limit: int) -> list:
        if is_url(query):
            playlist = "/playlist" in query
            opts = self._opts(
                extract_flat="in_playlist",
                noplaylist=not playlist,
                skip_download=True,
                ignore_no_formats_error=True,
            )
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(query, download=False)
            if not info:
                raise MediaError(f"Could not read that link: {query}")
            entries = [entry for entry in (info.get("entries") or []) if entry]
            metas = [self._meta(entry) for entry in entries[:limit]] if entries else [self._meta(info, query)]
            metas = [meta for meta in metas if meta["source"]]
            if not metas:
                raise MediaError(f"Nothing playable found at: {query}")
            return metas
        results = self._search_sync(query, 8)
        return [max(results, key=lambda meta: self._score(meta, query))]

    def _related_sync(self, source: str, title: str, limit: int) -> list:
        found = VIDEO_ID_RE.search(source or "")
        metas = []
        if found:
            try:
                with YoutubeDL(self._opts(
                    extract_flat="in_playlist", noplaylist=False, skip_download=True, playlistend=limit + 3,
                )) as ydl:
                    info = ydl.extract_info(f"https://www.youtube.com/playlist?list=RD{found.group(1)}", download=False)
                metas = [self._meta(entry) for entry in (info or {}).get("entries") or [] if entry]
            except Exception:
                logger.info("YouTube mix unavailable; falling back to search", exc_info=True)
        if len(metas) < limit:
            try:
                metas += self._search_sync(title, limit + 3)
            except MediaError:
                pass
        seen = {source}
        unique = []
        for meta in metas:
            if meta["source"] and meta["source"] not in seen and not meta["live"]:
                seen.add(meta["source"])
                unique.append(meta)
        return unique[:limit]

    def _resolve_stream_sync(self, source: str) -> dict:
        last = None
        clients = PLAYER_CLIENTS if self._is_youtube(source) else (None,)
        for client in clients:
            extra = {"format": "bestaudio/best", "fragment_retries": 3}
            if client and self._is_youtube(source):
                extra["extractor_args"] = {"youtube": {"player_client": [client]}}
            try:
                with YoutubeDL(self._opts(**extra)) as ydl:
                    info = ydl.extract_info(source, download=False)
                if info and info.get("entries"):
                    info = next((entry for entry in info["entries"] if entry), None)
                if info and info.get("url"):
                    meta = self._meta(info, source)
                    meta["url"] = info["url"]
                    return meta
            except Exception as exc:
                last = exc
                if self._bot_check(exc) and not (self.cookies_file or self.cookies_from_browser):
                    last = MediaError(self._auth_message())
                    logger.warning("YouTube blocked stream extraction; configure cookies in .env")
                    break
                logger.info("Stream resolve failed (client=%s): %s", client or "default", exc)
                if self._fatal(exc):
                    break
        raise MediaError(f"Could not get a playable stream: {last or 'no audio format found'}") from last

    # ---- downloads -------------------------------------------------------
    def _extract_download(self, source: str, opts: dict, progress=None):
        last = None
        clients = PLAYER_CLIENTS if self._is_youtube(source) else (None,)
        for client in clients:
            attempt = dict(opts)
            if progress:
                attempt["progress_hooks"] = [progress]
            if client and self._is_youtube(source):
                attempt["extractor_args"] = {"youtube": {"player_client": [client]}}
            try:
                with YoutubeDL(attempt) as ydl:
                    info = ydl.extract_info(source, download=True)
                    if info and info.get("entries"):
                        info = next((entry for entry in info["entries"] if entry), info)
                    return info, Path(ydl.prepare_filename(info))
            except Exception as exc:
                last = exc
                if self._bot_check(exc) and not (self.cookies_file or self.cookies_from_browser):
                    last = MediaError(self._auth_message())
                    logger.warning("YouTube blocked download; configure cookies in .env")
                    break
                logger.info("Download failed (client=%s): %s", client or "default", exc)
                if self._fatal(exc):
                    break
        raise MediaError(f"Download failed: {last}") from last

    @staticmethod
    def _is_youtube(source: str) -> bool:
        host = re.sub(r"^www\.", "", (re.match(r"https?://([^/]+)", source or "", re.I) or (None, ""))[1].lower())
        return bool(YOUTUBE_HOST_RE.fullmatch(host))

    def _download_sync(self, source: str, progress=None):
        if not shutil.which("ffmpeg"):
            raise MediaError("FFmpeg is required to convert YouTube audio to MP3")
        def download_once(quality: str):
            temp_dir = Path(tempfile.mkdtemp(prefix="f4_music_"))
            try:
                info, _ = self._extract_download(source, self._opts(
                    # A capped audio bitrate keeps Telegram uploads small and fast.
                    format="bestaudio[abr<=128]/bestaudio/best",
                    continuedl=True,
                    concurrent_fragment_downloads=8,
                    fragment_retries=3,
                    outtmpl=str(temp_dir / "%(id)s.%(ext)s"),
                    postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": quality}],
                ), progress)
                path = next(iter(sorted(temp_dir.glob("*.mp3"))), None)
                if path is None:
                    raise MediaError("YouTube did not produce an audio file")
                return info, path
            except Exception:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise

        info, path = download_once("128")
        # If a long track is still large, transparently retry at 96 kbps.
        if path.stat().st_size > 20 * 1024 * 1024:
            shutil.rmtree(path.parent, ignore_errors=True)
            info, path = download_once("96")
        raw_title = info.get("title") or "Untitled"
        title = info.get("track") or raw_title.split(" | ", 1)[0].strip()
        artist = info.get("artist") or info.get("album_artist") or info.get("creator") or info.get("uploader") or ""
        return path, title, artist, int(info.get("duration") or 0)

    def _download_video_sync(self, source: str, max_height: int, progress=None):
        if not shutil.which("ffmpeg"):
            raise MediaError("FFmpeg is required to download and convert video")
        temp_dir = Path(tempfile.mkdtemp(prefix="f4_music_"))
        try:
            info, prepared = self._extract_download(source, self._opts(
                # Prefer the requested height, but fall back to any playable
                # video when YouTube does not expose a matching format.
                format=(
                    f"bestvideo[height<={max_height}]+bestaudio/"
                    f"best[height<={max_height}]/bestvideo+bestaudio/best"
                ),
                continuedl=True,
                concurrent_fragment_downloads=4,
                fragment_retries=3,
                merge_output_format="mp4",
                outtmpl=str(temp_dir / "%(id)s.%(ext)s"),
                postprocessors=[{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
            ), progress)
            candidates = [prepared.with_suffix(".mp4"), *sorted(temp_dir.glob("*.mp4")), *sorted(temp_dir.glob("*.*"))]
            path = next((c for c in candidates if c.exists() and c.suffix != ".part"), None)
            if path is None:
                raise MediaError("YouTube download did not produce a video file")
            return path, info.get("title") or "Video", int(info.get("duration") or 0)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @staticmethod
    def cleanup(path):
        """Remove the temporary download and its directory."""
        path = Path(path)
        if path.name and path.parent.name.startswith(("f4_music_", "obito_music_")):
            shutil.rmtree(path.parent, ignore_errors=True)

    @staticmethod
    def cleanup_stale_downloads():
        """Remove temporary files left behind by an interrupted process."""
        for prefix in ("f4_music_*", "obito_music_*"):
            for directory in Path(tempfile.gettempdir()).glob(prefix):
                if directory.is_dir():
                    shutil.rmtree(directory, ignore_errors=True)

    # ---- async API -------------------------------------------------------
    async def lookup(self, query: str, limit: int = 1) -> list:
        """Fast metadata lookup. Text -> best match; playlist URL -> several."""
        return await asyncio.to_thread(self._lookup_sync, query, limit)

    async def search_list(self, query: str, limit: int = 5) -> list:
        results = await asyncio.to_thread(self._search_sync, query, max(limit, 8))
        return results[:limit]

    async def related(self, source: str, title: str, limit: int = 5) -> list:
        return await asyncio.to_thread(self._related_sync, source, title, limit)

    async def resolve_stream(self, source: str) -> dict:
        return await asyncio.to_thread(self._resolve_stream_sync, source)

    async def download(self, source: str, progress=None):
        return await asyncio.to_thread(self._download_sync, source, progress)

    async def download_video(self, source: str, max_height: int, progress=None):
        return await asyncio.to_thread(self._download_video_sync, source, max_height, progress)
