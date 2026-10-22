"""The AI DJ: Qwen writes a song-specific line, Chatterbox speaks it.

Voice references are uploaded per browser session and stored under <DATA_DIR>/voices
(git-ignored). They are never served back to any browser and are deleted on request.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import threading
import wave
from concurrent.futures import Future
from pathlib import Path

from .config import redact
from .creative import write_dj_line
from .library import Library
from .services import ServiceError, Services

log = logging.getLogger(__name__)

MAX_VOICE_BYTES = 10 * 1024 * 1024
_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class VoiceStore:
    def __init__(self, data_dir: Path):
        self.dir = data_dir / "voices"
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def check_session(session: str) -> str:
        if not _SESSION_RE.match(session or ""):
            raise ValueError("Missing or invalid session id.")
        return session

    def path(self, session: str) -> Path | None:
        self.check_session(session)
        return next(iter(sorted(self.dir.glob(f"{session}.*"))), None)

    def save(self, session: str, filename: str, data: bytes) -> dict:
        self.check_session(session)
        ext = Path(filename or "").suffix.lower().lstrip(".")
        if ext not in ("mp3", "wav"):
            raise ValueError("Upload an MP3 or WAV file.")
        if len(data) > MAX_VOICE_BYTES:
            raise ValueError("The voice file is larger than 10 MB; upload a shorter clip (10-20 s is ideal).")
        if len(data) < 2000:
            raise ValueError("The voice file is too small to be a recording.")
        is_wav = data[:4] == b"RIFF" and data[8:12] == b"WAVE"
        is_mp3 = data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)
        if (ext == "wav" and not is_wav) or (ext == "mp3" and not is_mp3):
            raise ValueError(f"That file does not look like a real {ext.upper()} recording.")
        seconds = None
        if ext == "wav":
            try:
                with wave.open(io.BytesIO(data)) as w:
                    seconds = w.getnframes() / float(w.getframerate())
            except (wave.Error, EOFError, ZeroDivisionError):
                seconds = None  # e.g. float WAVs the stdlib cannot parse; the TTS service decides
            if seconds is not None and seconds < 3:
                raise ValueError("The voice clip is shorter than 3 seconds; upload 10-20 seconds of clear speech.")
        self.delete(session)
        (self.dir / f"{session}.{ext}").write_bytes(data)
        return {"has_voice": True, "format": ext.upper(),
                "seconds": round(seconds, 1) if seconds else None}

    def delete(self, session: str) -> None:
        self.check_session(session)
        for p in self.dir.glob(f"{session}.*"):
            p.unlink(missing_ok=True)

    def fingerprint(self, session: str) -> str:
        p = self.path(session)
        return hashlib.sha1(p.read_bytes()).hexdigest()[:12] if p else "default"


class DJ:
    """Creates (and caches) the spoken clip that plays between two tracks."""

    def __init__(self, services: Services, library: Library, voices: VoiceStore, data_dir: Path):
        self.svc = services
        self.lib = library
        self.voices = voices
        self.dir = data_dir / "dj"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._inflight: dict[str, Future] = {}
        self._done: dict[str, dict] = {}

    def clip(self, prev_id: str | None, next_id: str, session: str | None = None,
             dj_name: str = "DJ Nova") -> dict:
        nxt = self.lib.get(next_id)
        prev = self.lib.get(prev_id) if prev_id else None
        if nxt is None or (prev_id and prev is None):
            raise KeyError("Unknown track.")
        voice = self.voices.fingerprint(session) if session else "default"
        key = hashlib.sha1(f"{prev_id}|{next_id}|{voice}|{dj_name}".encode()).hexdigest()[:16]

        # The browser prefetches the next clip; identical concurrent requests share one job.
        with self._lock:
            if key in self._done:
                return self._done[key]
            fut = self._inflight.get(key)
            owner = fut is None
            if owner:
                fut = self._inflight[key] = Future()
        if not owner:
            return fut.result()
        try:
            result = self._make(key, prev, nxt, session if voice != "default" else None, dj_name)
            with self._lock:
                self._done[key] = result
            fut.set_result(result)
            return result
        except BaseException as e:
            fut.set_exception(e)
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)

    def _make(self, key: str, prev: dict | None, nxt: dict, session: str | None, dj_name: str) -> dict:
        script = write_dj_line(self.svc, prev, nxt, dj_name)
        warning = ""
        voice_path = self.voices.path(session) if session else None
        try:
            audio = self.svc.speech(script, voice_path)
        except ServiceError as e:
            if voice_path is None:
                raise
            # A bad reference recording should not silence the DJ: fall back to the default voice.
            log.warning("custom voice failed, using default: %s", redact(str(e)))
            warning = "Your uploaded voice could not be used, so the default DJ voice was used. " + str(e)
            audio = self.svc.speech(script, None)
        name = f"{key}.{audio.ext}"
        (self.dir / name).write_bytes(audio.data)
        return {"id": key, "script": script, "audio_url": f"/media/dj/{name}", "warning": warning,
                "prev_id": prev["id"] if prev else None, "next_id": nxt["id"]}
