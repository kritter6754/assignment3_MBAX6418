"""Settings loading: the professor may paste URLs with or without /v1, or just drop in class-endpoints.txt."""
import os

import pytest

from jukebox.config import api_base, load_settings

KEYS = ("CLASS_API_KEY", "LLM_BASE_URL", "IMAGE_BASE_URL", "MUSIC_BASE_URL", "TTS_BASE_URL", "CLASS_ENDPOINTS_FILE",
        "SPEECH_BASE_URL", "FLUX_URL", "API_KEY", "MY_DJ_VOICE_URL")


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


def test_alternative_setting_names(clean_env, tmp_path):
    """A teammate named the voice URL SPEECH_BASE_URL; that must still configure the DJ."""
    env = tmp_path / ".env"
    env.write_text("API_KEY=k-123456\nLLM_BASE_URL=http://h:9001/v1\nFLUX_URL=http://h:9006/v1\n"
                   "MUSIC_BASE_URL=http://h:9007\nSPEECH_BASE_URL=http://h:9008\n")
    s = load_settings(env_file=env)
    assert s.tts_url == "http://h:9008/v1" and s.image_url == "http://h:9006/v1"
    assert s.api_key == "k-123456" and s.configured


def test_unknown_url_name_matched_by_port(clean_env, tmp_path):
    env = tmp_path / ".env"
    env.write_text("CLASS_API_KEY=k-123456\nLLM_BASE_URL=http://h:9001/v1\nIMAGE_BASE_URL=http://h:9006/v1\n"
                   "MUSIC_BASE_URL=http://h:9007\nMY_DJ_VOICE_URL=http://h:9008/v1\n")
    assert load_settings(env_file=env).tts_url == "http://h:9008/v1"


def test_short_keys_are_redacted():
    from jukebox.config import redact, register_secret
    register_secret("k9z4")
    assert redact("HTTP 401 for key k9z4") == "HTTP 401 for key ***"


OFFICIAL_LAYOUT = """Class Model API Endpoints

Hermes Primary Model — 9000
Base URL: http://h:9000/v1
API Key: key-for-9000
Model: some/model

Hermes Auxiliary Vision Model / Classification LLM — 9001
Base URL: http://h:9001/v1
API Key: shared-key-1
Model: cyankiwi/Qwen

Image Generation and Editing — 9006
Base URL: http://h:9006/v1
API Key: shared-key-1

Music Generation — 9007
Base URL: http://h:9007
API Key: shared-key-1

Text-to-Speech and Voice Cloning — 9008
Base URL: http://h:9008
API Key: shared-key-1
Hugging Face: https://huggingface.co/ResembleAI/chatterbox

Notes for agents
- Chatterbox (9008): docs at http://h:9008/docs
"""


def test_official_class_file_layout(clean_env, tmp_path, capsys):
    f = tmp_path / "class-endpoints.txt"
    f.write_text(OFFICIAL_LAYOUT)
    clean_env.setenv("CLASS_ENDPOINTS_FILE", str(f))
    s = load_settings(env_file=tmp_path / "no.env")
    assert (s.llm_url, s.image_url, s.music_url, s.tts_url) == (
        "http://h:9001/v1", "http://h:9006/v1", "http://h:9007", "http://h:9008/v1")
    assert s.api_key == "shared-key-1" and s.configured  # the 9000 block is ignored
    assert "could not parse" not in capsys.readouterr().err
