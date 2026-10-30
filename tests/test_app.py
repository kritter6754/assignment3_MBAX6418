"""End-to-end API tests with offline mock services (no network)."""
from conftest import SECRET, make_settings, silent_wav, wait_job, wav_bytes
from fastapi.testclient import TestClient

from jukebox.app import create_app

SONG = {"style": "Synthwave", "mood": "Dreamy", "duration_s": 60, "vocal": True, "theme": "neon city"}


def test_samples_listed_with_media(client):
    tracks = client.get("/api/tracks").json()
    assert [t["title"] for t in tracks] == ["Test Sample"]
    t = tracks[0]
    assert client.get(t["audio_url"]).status_code == 200
    assert client.get(t["cover_url"]).status_code == 200


def test_config_has_no_secrets(client):
    r = client.get("/api/config")
    assert r.json()["configured"] is True
    assert SECRET not in r.text and "http://llm" not in r.text


def test_vocal_song_generation_adds_track_with_cover(client, mock_services):
    job = client.post("/api/generate", json=SONG).json()
    job = wait_job(client, job["id"])
    assert job["status"] == "done", job
    assert [s["state"] for s in job["steps"]] == ["done", "done", "done"]
    tracks = {t["id"]: t for t in client.get("/api/tracks").json()}
    new = tracks[job["track_id"]]
    assert new["vocal"] and "[Chorus]" in new["lyrics"]
    assert new["style"] == "Synthwave" and new["duration_s"] == 60
    assert client.get(new["audio_url"]).status_code == 200
    assert client.get(new["cover_url"]).headers["content-type"] == "image/jpeg"
    # the cover prompt Qwen wrote is what FLUX received
    image_prompt = next(a for n, a in mock_services.calls if n == "image")
    assert "no text" in image_prompt


def test_instrumental_song_has_no_lyrics(client, mock_services):
    job = client.post("/api/generate", json={**SONG, "vocal": False}).json()
    job = wait_job(client, job["id"])
    track = next(t for t in client.get("/api/tracks").json() if t["id"] == job["track_id"])
    assert track["lyrics"] == "[Instrumental]" and track["vocal"] is False
    plan_prompt = next(a for n, a in mock_services.calls if n == "chat")
    assert "instrumental" in plan_prompt.lower()


def test_invalid_duration_rejected(client):
    r = client.post("/api/generate", json={**SONG, "duration_s": 5000})
    assert r.status_code == 400 and "Duration" in r.json()["detail"]


def test_failed_music_can_be_retried_without_replanning(client, mock_services):
    mock_services.fail = {"music"}
    job = wait_job(client, client.post("/api/generate", json=SONG).json()["id"])
    assert job["status"] == "failed"
    assert "simulated failure" in job["error"]
    states = {s["key"]: s["state"] for s in job["steps"]}
    assert states == {"plan": "done", "cover": "done", "music": "failed"}
    assert len(client.get("/api/tracks").json()) == 1  # not added to the library

    chats = sum(1 for n, _ in mock_services.calls if n == "chat")
    images = sum(1 for n, _ in mock_services.calls if n == "image")
    mock_services.fail = set()
    client.post(f"/api/jobs/{job['id']}/retry")
    job = wait_job(client, job["id"])
    assert job["status"] == "done"
    assert sum(1 for n, _ in mock_services.calls if n == "chat") == chats      # plan reused
    assert sum(1 for n, _ in mock_services.calls if n == "image") == images    # cover reused
    assert len(client.get("/api/tracks").json()) == 2


def test_dj_clip_between_songs_is_cached(client, mock_services):
    job = wait_job(client, client.post("/api/generate", json=SONG).json()["id"])
    sample = client.get("/api/tracks").json()[0]["id"]
    clip = client.post("/api/dj", json={"prev_id": sample, "next_id": job["track_id"]}).json()
    assert clip["script"] and clip["audio_url"].startswith("/media/dj/")
    assert client.get(clip["audio_url"]).status_code == 200
    dj_prompt = [a for n, a in mock_services.calls if n == "chat"][-1]
    assert "Test Sample" in dj_prompt and "Mock title" in dj_prompt  # both songs named
    n = len(mock_services.calls)
    assert client.post("/api/dj", json={"prev_id": sample, "next_id": job["track_id"]}).json() == clip
    assert len(mock_services.calls) == n


def test_dj_unknown_track_404(client):
    assert client.post("/api/dj", json={"next_id": "nope"}).status_code == 404


def test_voice_upload_validation_and_use(client, mock_services):
    bad = client.post("/api/voice", files={"file": ("notes.txt", b"hello" * 1000, "text/plain")})
    assert bad.status_code == 400 and "MP3 or WAV" in bad.json()["detail"]
    fake = client.post("/api/voice", files={"file": ("x.wav", b"\x00" * 5000, "audio/wav")})
    assert fake.status_code == 400
    short = client.post("/api/voice", files={"file": ("x.wav", silent_wav(1), "audio/wav")})
    assert short.status_code == 400 and "3 seconds" in short.json()["detail"]
    ok = client.post("/api/voice", files={"file": ("me.wav", wav_bytes(4), "audio/wav")})
    assert ok.status_code == 200 and ok.json()["has_voice"]
    assert client.get("/api/voice").json()["has_voice"] is True
    # another browser session does not see it
    assert client.get("/api/voice", headers={"X-Session": "othersession"}).json()["has_voice"] is False
    assert client.delete("/api/voice").json()["has_voice"] is False


def test_custom_voice_failure_falls_back_to_default(tmp_path, samples_dir):
    from jukebox.services import MockServices, ServiceError

    class FlakyVoice(MockServices):
        def speech(self, text, voice_path=None, language="en"):
            if voice_path is not None:
                raise ServiceError("DJ voice service (Chatterbox): bad reference")
            return super().speech(text, voice_path, language)

    app = create_app(make_settings(tmp_path), FlakyVoice(), samples_dir=samples_dir)
    with TestClient(app, headers={"X-Session": "testsession01"}) as c:
        c.post("/api/voice", files={"file": ("me.wav", wav_bytes(4), "audio/wav")})
        sid = c.get("/api/tracks").json()[0]["id"]
        clip = c.post("/api/dj", json={"next_id": sid}).json()
        assert "default DJ voice" in clip["warning"] and clip["audio_url"]


def test_media_path_traversal_blocked(client):
    assert client.get("/media/samples/../conftest.py").status_code == 404
    assert client.get("/media/samples/01-test-sample/meta.json").status_code == 404
    assert client.get("/media/nope/x").status_code == 404


def test_unconfigured_server_still_plays_samples(tmp_path, samples_dir):
    app = create_app(make_settings(tmp_path, api_key=""), samples_dir=samples_dir)
    with TestClient(app) as c:
        assert c.get("/api/config").json()["configured"] is False
        assert len(c.get("/api/tracks").json()) == 1
        r = c.post("/api/generate", json=SONG)
        assert r.status_code == 503 and "CLASS_API_KEY" in r.json()["detail"]


def test_app_password(tmp_path, samples_dir, mock_services):
    app = create_app(make_settings(tmp_path, app_password="letmein1"), mock_services, samples_dir=samples_dir)
    with TestClient(app) as c:
        assert c.get("/api/tracks").status_code == 401
        assert c.get("/healthz").status_code == 200
        assert c.get("/api/tracks", auth=("any", "letmein1")).status_code == 200


def test_generated_songs_survive_restart(tmp_path, samples_dir, mock_services):
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings, mock_services, samples_dir=samples_dir)) as c:
        job = wait_job(c, c.post("/api/generate", json=SONG).json()["id"])
    with TestClient(create_app(settings, mock_services, samples_dir=samples_dir)) as c:
        assert job["track_id"] in [t["id"] for t in c.get("/api/tracks").json()]


def test_status_endpoint_has_no_secrets(client):
    r = client.get("/api/status")
    assert r.json()["services"]["text"]["ok"] and SECRET not in r.text


def test_style_dropdown_list(client):
    groups = client.get("/api/config").json()["style_groups"]
    styles = [s for g in groups.values() for s in g]
    assert len(styles) == 50 == len(set(styles))
    assert styles[0] == "Classic Rock" and styles[-1] == "Torch Song"
