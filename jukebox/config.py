"""Settings loaded from environment variables (or a local, git-ignored .env file).

The API key is read only on the server side. It is never sent to the browser, and
`redact()` scrubs it from any text that might be logged or shown in an error.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

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
            record.args = tuple(redact(str(a)) for a in record.args)
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
        return bool(self.api_key) and self.api_key != "replace-me" and all(urls)


def load_settings(env_file: str | os.PathLike | None = None) -> Settings:
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)
    e = os.environ.get
    data_dir = Path(e("DATA_DIR", "data"))
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir
    s = Settings(
        api_key=e("CLASS_API_KEY", ""),
        llm_url=e("LLM_BASE_URL", ""),
        llm_model=e("LLM_MODEL", "cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit"),
        image_url=e("IMAGE_BASE_URL", ""),
        image_model=e("IMAGE_MODEL", "black-forest-labs/FLUX.2-klein-4B"),
        music_url=e("MUSIC_BASE_URL", ""),
        music_model=e("MUSIC_MODEL", "ACE-Step-1.5-XL-Turbo"),
        tts_url=e("TTS_BASE_URL", ""),
        tts_model=e("TTS_MODEL", "chatterbox-multilingual"),
        data_dir=data_dir,
        app_password=e("APP_PASSWORD", ""),
        mock=_flag(e("MOCK_SERVICES")),
    )
    register_secret(s.api_key)
    register_secret(s.app_password)
    return s
