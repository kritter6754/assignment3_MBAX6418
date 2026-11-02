"""Settings loading: the professor may paste URLs with or without /v1, or just drop in class-endpoints.txt."""
import os

import pytest

from jukebox.config import api_base, load_settings

KEYS = ("CLASS_API_KEY", "LLM_BASE_URL", "IMAGE_BASE_URL", "MUSIC_BASE_URL", "TTS_BASE_URL", "CLASS_ENDPOINTS_FILE")


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    # A private copy of the environment, so values the loader writes never leak into other tests.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for k in KEYS:
        os.environ.pop(k, None)
    monkeypatch.setenv("CLASS_ENDPOINTS_FILE", str(tmp_path / "missing.txt"))
    return monkeypatch


@pytest.mark.parametrize("raw,v1,want", [
    (" http://h:9008 ", True, "http://h:9008/v1"),
    ("http://h:9008/v1/", True, "http://h:9008/v1"),
    ("http://h:9007/v1", False, "http://h:9007"),
    ("http://h:9007", False, "http://h:9007"),
    ("", True, ""),
])
def test_api_base(raw, v1, want):
    assert api_base(raw, v1) == want


def test_class_endpoints_file_is_read(clean_env, tmp_path):
    f = tmp_path / "class-endpoints.txt"
    f.write_text("\nLLM_BASE_URL= http://h:9001/v1 \nIMAGE_BASE_URL= http://h:9006/v1 \n"
                 "MUSIC_BASE_URL= http://h:9007\nTTS_BASE_URL= http://h:9008\n")
    clean_env.setenv("CLASS_ENDPOINTS_FILE", str(f))
    clean_env.setenv("CLASS_API_KEY", "key-from-env-123")
    s = load_settings(env_file=tmp_path / "no.env")
    assert (s.llm_url, s.image_url, s.music_url, s.tts_url) == (
        "http://h:9001/v1", "http://h:9006/v1", "http://h:9007", "http://h:9008/v1")
    assert s.configured


def test_env_wins_over_endpoints_file_and_urls_matched_by_port(clean_env, tmp_path):
    f = tmp_path / "class-endpoints.txt"
    f.write_text("Qwen text: http://h:9001/v1\nFLUX: http://h:9006/v1\nACE-Step http://h:9007\n"
                 "Chatterbox -> http://h:9008\nAPI_KEY=key-in-file-456\n")
    clean_env.setenv("CLASS_ENDPOINTS_FILE", str(f))
    clean_env.setenv("TTS_BASE_URL", "http://other:9008/v1")
    s = load_settings(env_file=tmp_path / "no.env")
    assert s.llm_url == "http://h:9001/v1" and s.music_url == "http://h:9007"
    assert s.tts_url == "http://other:9008/v1" and s.api_key == "key-in-file-456"


def test_example_placeholders_do_not_hide_endpoints_file(clean_env, tmp_path):
    """Copying .env.example to .env unchanged must not override class-endpoints.txt."""
    f = tmp_path / "class-endpoints.txt"
    f.write_text("LLM_BASE_URL= http://h:9001/v1\nIMAGE_BASE_URL= http://h:9006/v1\n"
                 "MUSIC_BASE_URL= http://h:9007\nTTS_BASE_URL= http://h:9008\n")
    env = tmp_path / ".env"
    env.write_text("CLASS_API_KEY=real-key-789\nLLM_BASE_URL=http://example.com:9001/v1\n"
                   "IMAGE_BASE_URL=http://example.com:9006/v1\nMUSIC_BASE_URL=http://example.com:9007\n"
                   "TTS_BASE_URL=http://example.com:9008/v1\n")
    clean_env.setenv("CLASS_ENDPOINTS_FILE", str(f))
    s = load_settings(env_file=env)
    assert s.llm_url == "http://h:9001/v1" and s.tts_url == "http://h:9008/v1" and s.configured


def test_unedited_example_is_not_configured(clean_env, tmp_path):
    env = tmp_path / ".env"
    env.write_text((__import__("pathlib").Path(__file__).parent.parent / ".env.example").read_text())
    assert not load_settings(env_file=env).configured
