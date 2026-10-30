"""Clients for the four class services the jukebox depends on.

| Service                      | Port | Request                                          | Used for        |
|------------------------------|------|--------------------------------------------------|-----------------|
| Qwen3.6-35B-A3B (vLLM)       | 9001 | POST /chat/completions                           | lyrics, DJ text |
| FLUX.2 klein 4B              | 9006 | POST /images/generations                         | cover art       |
| ACE-Step 1.5 XL Turbo        | 9007 | POST /release_task, POST /query_result, GET file | music           |
| Chatterbox Multilingual V3   | 9008 | POST /audio/speech (+ /audio/speech/upload)      | DJ speech       |

Every request format lives in this module, so a change in a service's documented API is a
change here only. `MockServices` fakes all four offline (MOCK_SERVICES=true) for UI work
and tests; it never runs unless explicitly enabled.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import math
import re
import struct
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from PIL import Image, ImageDraw

from .config import Settings, redact

log = logging.getLogger(__name__)


class ServiceError(RuntimeError):
    """A class service failed. The message is safe to show to users (secrets redacted)."""


@dataclass
class Audio:
    data: bytes
    ext: str  # "mp3" or "wav"


class Services(Protocol):
    def chat(self, messages: list[dict], schema: dict | None = None,
             max_tokens: int = 1500, temperature: float = 0.8) -> str: ...
    def image(self, prompt: str, size: int = 1024) -> bytes: ...
    def music(self, caption: str, lyrics: str, duration_s: int, language: str = "en",
              seed: int | None = None) -> Audio: ...
    def speech(self, text: str, voice_path: Path | None = None, language: str = "en") -> Audio: ...
    def status(self) -> dict[str, dict]: ...


def extract_json(text: str) -> Any:
    """Parse JSON from model output, tolerating <think> blocks and code fences."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            raise
        return json.loads(m.group(0))


_FRIENDLY = {
    401: "the service rejected our credentials (check CLASS_API_KEY)",
    403: "the service rejected our credentials (check CLASS_API_KEY)",
    404: "the service URL looks wrong (check the *_BASE_URL settings)",
    413: "the request was too large",
    429: "the service is busy (rate limited); wait a moment and retry",
    502: "the service is temporarily unavailable; retry in a moment",
    503: "the service is busy or restarting; retry in a moment",
    504: "the service timed out; retry in a moment",
}


class ClassServices:
    """Real HTTP client for the class endpoints."""

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None,
                 poll_interval_s: float = 2.0):
        self.s = settings
        self.poll_interval_s = poll_interval_s
        # A short connect timeout makes a wrong or unreachable URL fail in seconds, while slow
        # generations still get the full read timeout.
        self._http = httpx.Client(
            timeout=httpx.Timeout(settings.timeout_s, connect=10.0), transport=transport,
            headers={"Authorization": f"Bearer {settings.api_key}"},
        )

    # -- plumbing -----------------------------------------------------------
    def _request(self, name: str, method: str, url: str, **kw) -> httpx.Response:
        for attempt in range(3):  # retry dropped connections and 502/503/504 with backoff
            host = _host(url)
            try:
                r = self._http.request(method, url, **kw)
            except httpx.ConnectTimeout:
                raise ServiceError(f"{name}: could not connect to {host} (check its *_BASE_URL setting)") from None
            except httpx.TimeoutException:
                raise ServiceError(f"{name}: {host} did not answer in time; the service may be busy, retry shortly") from None
            except httpx.HTTPError as e:
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise ServiceError(f"{name}: could not reach {host} ({type(e).__name__})") from None
            if r.status_code in (502, 503, 504) and attempt < 2:
                time.sleep(2.0 * (attempt + 1))
                continue
            if r.status_code >= 400:
                why = _FRIENDLY.get(r.status_code, f"HTTP {r.status_code}")
                log.warning("%s %s -> %s: %s", method, url, r.status_code, r.text[:300])
                raise ServiceError(redact(f"{name}: {why}"))
            return r
        raise AssertionError("unreachable")

    @staticmethod
    def _url(base: str, path: str) -> str:
        return base.strip().rstrip("/") + path

    def status(self) -> dict[str, dict]:
        """Quick reachability check of each service, as seen from this server."""
        checks = {"text": (self.s.llm_url, "/models"), "image": (self.s.image_url, "/models"),
                  "music": (self.s.music_url, "/health"), "speech": (self.s.tts_url, "/models")}
        out = {}
        for key, (base, path) in checks.items():
            url = self._url(base, path)
            t = time.monotonic()
            try:
                r = self._http.get(url, timeout=httpx.Timeout(10.0, connect=5.0))
                ok, detail = r.status_code == 200, f"HTTP {r.status_code}"
            except httpx.HTTPError as e:
                ok, detail = False, type(e).__name__
            out[key] = {"ok": ok, "host": _host(url), "detail": detail,
                        "ms": round((time.monotonic() - t) * 1000)}
        return out

    # -- Qwen (text) --------------------------------------------------------
    def chat(self, messages: list[dict], schema: dict | None = None,
             max_tokens: int = 1500, temperature: float = 0.8) -> str:
        payload: dict[str, Any] = {
            "model": self.s.llm_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            # Qwen "thinks" before answering by default; off keeps creative calls fast.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema", "json_schema": {"name": "output", "schema": schema}}
        r = self._request("Text service (Qwen)", "POST",
                          self._url(self.s.llm_url, "/chat/completions"), json=payload)
        return r.json()["choices"][0]["message"]["content"] or ""

    # -- FLUX (cover art) ---------------------------------------------------
    def image(self, prompt: str, size: int = 1024) -> bytes:
        payload = {"model": self.s.image_model, "prompt": prompt, "n": 1,
                   "size": f"{size}x{size}", "response_format": "b64_json"}
        r = self._request("Cover-art service (FLUX)", "POST",
                          self._url(self.s.image_url, "/images/generations"), json=payload)
        if r.headers.get("content-type", "").startswith("image/"):
            return r.content
        item = r.json()["data"][0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            return self._request("Cover-art service (FLUX)", "GET", item["url"]).content
        raise ServiceError("Cover-art service (FLUX): the response contained no image")

    # -- ACE-Step (music) ---------------------------------------------------
    def music(self, caption: str, lyrics: str, duration_s: int, language: str = "en",
              seed: int | None = None) -> Audio:
        """Submit a generation task, poll until it finishes, then download the audio."""
        name = "Music service (ACE-Step)"
        payload: dict[str, Any] = {
            "model": self.s.music_model,
            "prompt": caption,           # style/mood caption
            "lyrics": lyrics,            # "[Instrumental]" for instrumental tracks
            "audio_duration": duration_s,
            "vocal_language": language,
            "audio_format": "mp3",
            "batch_size": 1,
            "use_random_seed": seed is None,
        }
        if seed is not None:
            payload["seed"] = seed
        r = self._request(name, "POST", self._url(self.s.music_url, "/release_task"), json=payload)
        if r.headers.get("content-type", "").startswith("audio/"):
            return Audio(r.content, _audio_ext(r.headers["content-type"]))
        body = r.json()
        task_id = (body.get("data") or {}).get("task_id") or body.get("task_id")
        if not task_id:
            raise ServiceError(f"{name}: no task id in the response")

        deadline = time.monotonic() + self.s.music_timeout_s
        while time.monotonic() < deadline:
            time.sleep(self.poll_interval_s)
            q = self._request(name, "POST", self._url(self.s.music_url, "/query_result"),
                              json={"task_id_list": [task_id]}).json()
            items = q.get("data") or []
            item = items[0] if items else {}
            status = item.get("status")
            if status in (0, "queued", "running", "pending", None):
                continue
            if status in (2, "failed", "error"):
                raise ServiceError(f"{name}: generation failed on the server; retry, or try a shorter duration")
            result = item.get("result")
            results = json.loads(result) if isinstance(result, str) else result
            if isinstance(results, dict):
                results = [results]
            file_ref = next((x.get("file") or x.get("url") for x in results or [] if isinstance(x, dict)), None)
            if not file_ref:
                raise ServiceError(f"{name}: the finished task had no audio file")
            url = file_ref if file_ref.startswith("http") else self._url(self.s.music_url, file_ref)
            a = self._request(name, "GET", url, timeout=self.s.timeout_s)
            return Audio(a.content, _audio_ext(a.headers.get("content-type", ""), file_ref))
        raise ServiceError(f"{name}: still not finished after {int(self.s.music_timeout_s)} s; retry later")

    # -- Chatterbox (DJ speech) ---------------------------------------------
    def speech(self, text: str, voice_path: Path | None = None, language: str = "en") -> Audio:
        name = "DJ voice service (Chatterbox)"
        req = {"model": self.s.tts_model, "input": text, "language_id": language,
               "response_format": "mp3"}
        if voice_path is None:
            r = self._request(name, "POST", self._url(self.s.tts_url, "/audio/speech"), json=req)
        else:
            # Reference recording goes as the multipart "file"; the speech settings as a JSON string.
            mime = "audio/mpeg" if voice_path.suffix.lower() == ".mp3" else "audio/wav"
            with voice_path.open("rb") as f:
                r = self._request(name, "POST", self._url(self.s.tts_url, "/audio/speech/upload"),
                                  data={"request": json.dumps(req)},
                                  files={"file": (voice_path.name, f, mime)})
        ctype = r.headers.get("content-type", "")
        if ctype.startswith("application/json"):
            raise ServiceError(f"{name}: expected audio but got JSON")
        return Audio(r.content, _audio_ext(ctype))


def _host(url: str) -> str:
    try:
        u = httpx.URL(url)
        return f"{u.host}:{u.port}" if u.port else u.host or url
    except Exception:  # noqa: BLE001 - only used for messages
        return repr(url)


def _audio_ext(content_type: str, ref: str = "") -> str:
    if "mpeg" in content_type or "mp3" in content_type or ref.lower().endswith(".mp3"):
        return "mp3"
    if "flac" in content_type or ref.lower().endswith(".flac"):
        return "flac"
    return "wav"


# -- offline fakes -----------------------------------------------------------

def _tone_wav(seconds: float, seed: str, voice: bool = False) -> bytes:
    """A short synthetic melody (or a buzzy 'voice') as 16-bit mono WAV."""
    rate = 16000
    h = int(hashlib.sha1(seed.encode()).hexdigest(), 16)
    scale = [261.6, 293.7, 329.6, 392.0, 440.0, 523.3]
    note_len = 0.25 if voice else 0.5
    frames = bytearray()
    for i in range(int(seconds * rate)):
        t = i / rate
        f = scale[(h >> (int(t / note_len) % 40)) % len(scale)] * (0.5 if voice else 1)
        env = min(1.0, (t % note_len) * 20) * (1 - (t % note_len) / note_len * 0.7)
        v = math.sin(2 * math.pi * f * t) * env * 0.3
        frames += struct.pack("<h", int(v * 32767))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


class MockServices:
    """Deterministic offline stand-ins. Development and tests only."""

    def __init__(self, fail: set[str] | None = None):
        self.fail = fail or set()  # names of methods that should raise, for failure tests
        self.calls: list[tuple[str, Any]] = []

    def _check(self, name: str, arg: Any) -> None:
        self.calls.append((name, arg))
        if name in self.fail:
            raise ServiceError(f"Mock {name} service: simulated failure")

    def chat(self, messages, schema=None, max_tokens=1500, temperature=0.8) -> str:
        self._check("chat", messages[-1]["content"])
        if schema is None:
            return "That was a mock track. Up next, another one. Stay tuned!"
        props = schema.get("properties", {})
        out = {k: f"Mock {k}" for k in props}
        if "lyrics" in props:
            out["lyrics"] = "[Verse]\nMock words in the night\n\n[Chorus]\nMock song, sing along"
        if "bpm" in props:
            out["bpm"] = 100
        return json.dumps(out)

    def image(self, prompt: str, size: int = 1024) -> bytes:
        self._check("image", prompt)
        h = hashlib.sha1(prompt.encode()).digest()
        im = Image.new("RGB", (512, 512), tuple(h[:3]))
        d = ImageDraw.Draw(im)
        for i in range(0, 256, 32):
            d.ellipse((i, i, 512 - i, 512 - i), outline=tuple(h[3:6]), width=6)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()

    def music(self, caption, lyrics, duration_s, language="en", seed=None) -> Audio:
        self._check("music", caption)
        return Audio(_tone_wav(min(duration_s, 8), caption), "wav")

    def status(self) -> dict[str, dict]:
        return {k: {"ok": True, "host": "mock", "detail": "mock", "ms": 0}
                for k in ("text", "image", "music", "speech")}

    def speech(self, text, voice_path=None, language="en") -> Audio:
        self._check("speech", text)
        return Audio(_tone_wav(2.0, text, voice=True), "wav")


def make_services(settings: Settings) -> Services:
    return MockServices() if settings.mock else ClassServices(settings)
