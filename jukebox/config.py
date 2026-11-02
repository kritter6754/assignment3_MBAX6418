"""Settings loaded from environment variables (or a local, git-ignored .env file).

The API key is read only on the server side. It is never sent to the browser, and
`redact()` scrubs it from any text that might be logged or shown in an error.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = PROJECT_ROOT / "assets" / "samples"

_SECRETS: set[str] = set()


def register_secret(value: str) -> None:
    if value and len(value) >= 6:
        _SECRETS.add(value)


def redact(text: str) -> str:
    for s in _SECRETS:
        text = text.replace(s, "***")
    return text


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))
        if record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        return True


def install_log_redaction() -> None:
    for h in logging.getLogger().handlers:
        h.addFilter(_RedactFilter())


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)  # never printed by repr()
    llm_url: str
    llm_model: str
    image_url: str
    image_model: str
    music_url: str
    music_model: str
    tts_url: str
    tts_model: str
    data_dir: Path
    app_password: str = field(default="", repr=False)
    mock: bool = False
    timeout_s: float = 180.0
    music_timeout_s: float = 900.0

    @property
    def configured(self) -> bool:
        if self.mock:
            return True
        urls = (self.llm_url, self.image_url, self.music_url, self.tts_url)
        return bool(self.api_key) and all(urls)


ENV_FOR_PORT = {9001: "LLM_BASE_URL", 9006: "IMAGE_BASE_URL", 9007: "MUSIC_BASE_URL", 9008: "TTS_BASE_URL"}


def _placeholder(value: str | None) -> bool:
    """Values copied unchanged from .env.example count as unset."""
    v = (value or "").strip()
    return not v or v == "replace-me" or "example.com" in v


def _env(key: str, default: str = "") -> str:
    v = os.environ.get(key, "")
    return default if _placeholder(v) else v.strip()


def load_endpoints_file(path: Path) -> None:
    """Fill in settings from the class's class-endpoints.txt, if present in the project folder.

    Lowest priority: real environment variables and .env win. Accepts KEY=VALUE lines (spaces
    around '=' are fine); URLs without a known key are matched to a service by their port.
    """
    if not path.is_file():
        return
    text = path.read_text(errors="replace")
    values = {k.strip().upper(): (v or "").strip() for k, v in dotenv_values(path).items() if k}
    if "API_KEY" in values and "CLASS_API_KEY" not in values:
        values["CLASS_API_KEY"] = values["API_KEY"]
    for url, port in re.findall(r"(https?://[^\s\"'<>]+?:(\d{4,5})[^\s\"'<>]*)", text):
        key = ENV_FOR_PORT.get(int(port))
        if key and not values.get(key):
            values[key] = url
    for key in ("CLASS_API_KEY", *ENV_FOR_PORT.values()):
        if not _placeholder(values.get(key)) and _placeholder(os.environ.get(key)):
            os.environ[key] = values[key]


def api_base(url: str, v1: bool) -> str:
    """Normalise a service URL: trim spaces and trailing slashes, and add or drop the /v1 the
    service expects, so URLs work whether or not they were copied with /v1."""
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if url.endswith("/v1"):
        url = url[:-3]
    return url + "/v1" if v1 else url


def load_settings(env_file: str | os.PathLike | None = None) -> Settings:
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)
    load_endpoints_file(Path(os.environ.get("CLASS_ENDPOINTS_FILE", PROJECT_ROOT / "class-endpoints.txt")))
    e = os.environ.get
    data_dir = Path(e("DATA_DIR", "data"))
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir
    s = Settings(
        api_key=_env("CLASS_API_KEY"),
        llm_url=api_base(_env("LLM_BASE_URL"), v1=True),
        llm_model=e("LLM_MODEL", "cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit"),
        image_url=api_base(_env("IMAGE_BASE_URL"), v1=True),
        image_model=e("IMAGE_MODEL", "black-forest-labs/FLUX.2-klein-4B"),
        music_url=api_base(_env("MUSIC_BASE_URL"), v1=False),  # ACE-Step routes are not under /v1
        music_model=e("MUSIC_MODEL", "acestep/acestep-v15-xl-turbo"),
        tts_url=api_base(_env("TTS_BASE_URL"), v1=True),
        tts_model=e("TTS_MODEL", "chatterbox-v3"),
        data_dir=data_dir,
        app_password=e("APP_PASSWORD", ""),
        mock=_flag(e("MOCK_SERVICES")),
    )
    register_secret(s.api_key)
    register_secret(s.app_password)
    return s
