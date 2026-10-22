import io
import json
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jukebox.app import create_app
from jukebox.config import Settings
from jukebox.services import MockServices, _tone_wav

SECRET = "sk-test-secret-123456"


def make_settings(tmp_path: Path, **kw) -> Settings:
    base = dict(api_key=SECRET, llm_url="http://llm/v1", llm_model="qwen", image_url="http://img/v1",
                image_model="flux", music_url="http://music", music_model="ace", tts_url="http://tts/v1",
                tts_model="chatterbox", data_dir=tmp_path / "data")
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def samples_dir(tmp_path: Path) -> Path:
    d = tmp_path / "samples" / "01-test-sample"
    d.mkdir(parents=True)
    (d / "audio.wav").write_bytes(_tone_wav(1, "sample"))
    (d / "cover.jpg").write_bytes(b"\xff\xd8fakejpeg")
    (d / "meta.json").write_text(json.dumps({
        "title": "Test Sample", "style": "Jazz", "mood": "Chill", "duration_s": 30, "vocal": False,
        "lyrics": "[Instrumental]", "audio": "audio.wav", "cover": "cover.jpg"}))
    return d.parent


@pytest.fixture
def mock_services() -> MockServices:
    return MockServices()


@pytest.fixture
def client(tmp_path, samples_dir, mock_services):
    app = create_app(make_settings(tmp_path), mock_services, samples_dir=samples_dir)
    with TestClient(app, headers={"X-Session": "testsession01"}) as c:
        yield c


def wait_job(client, job_id: str, timeout: float = 10) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        job = next(j for j in client.get("/api/jobs").json() if j["id"] == job_id)
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def wav_bytes(seconds: float = 4) -> bytes:
    return _tone_wav(seconds, "voice")


def silent_wav(seconds: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * int(8000 * seconds))
    return buf.getvalue()
