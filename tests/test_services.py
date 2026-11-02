"""Request formats sent to the class services, checked against a fake HTTP server."""
import base64
import json

import httpx
import pytest
from conftest import SECRET, make_settings

from jukebox.creative import SongRequest, clean_speech, plan_song
from jukebox.services import ClassServices, ServiceError


def svc_with(handler, tmp_path):
    return ClassServices(make_settings(tmp_path), transport=httpx.MockTransport(handler), poll_interval_s=0)


def test_chat_payload_and_auth(tmp_path):
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    assert svc_with(handler, tmp_path).chat([{"role": "user", "content": "x"}]) == "hi"
    assert seen["url"] == "http://llm/v1/chat/completions"
    assert seen["auth"] == f"Bearer {SECRET}"
    assert seen["body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_image_b64(tmp_path):
    def handler(req):
        assert req.url.path == "/v1/images/generations"
        assert json.loads(req.content)["prompt"] == "a cat"
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"PNGDATA").decode()}]})

    assert svc_with(handler, tmp_path).image("a cat") == b"PNGDATA"


def test_music_task_polling(tmp_path):
    polls = {"n": 0}

    def handler(req):
        if req.url.path == "/release_task":
            body = json.loads(req.content)
            assert body["lyrics"] == "[Instrumental]" and body["audio_duration"] == 30
            return httpx.Response(200, json={"data": {"task_id": "t1"}})
        if req.url.path == "/query_result":
            polls["n"] += 1
            if polls["n"] < 3:
                return httpx.Response(200, json={"data": [{"task_id": "t1", "status": 0}]})
            result = json.dumps([{"file": "/v1/audio?path=out.mp3"}])
            return httpx.Response(200, json={"data": [{"task_id": "t1", "status": 1, "result": result}]})
        if req.url.path == "/v1/audio":
            return httpx.Response(200, content=b"ID3" + b"\0" * 2000, headers={"content-type": "audio/mpeg"})
        return httpx.Response(404)

    a = svc_with(handler, tmp_path).music("lofi", "[Instrumental]", 30)
    assert a.ext == "mp3" and a.data.startswith(b"ID3") and polls["n"] == 3


def test_music_failure_status(tmp_path):
    def handler(req):
        if req.url.path == "/release_task":
            return httpx.Response(200, json={"data": {"task_id": "t1"}})
        return httpx.Response(200, json={"data": [{"status": 2}]})

    with pytest.raises(ServiceError, match="generation failed"):
        svc_with(handler, tmp_path).music("x", "[Instrumental]", 30)


def test_speech_default_and_uploaded_voice(tmp_path):
    seen = []

    def handler(req):
        seen.append((req.url.path, req.headers["content-type"], req.content))
        return httpx.Response(200, content=b"RIFFxxxxWAVE", headers={"content-type": "audio/wav"})

    svc = svc_with(handler, tmp_path)
    svc.speech("hello there")
    ref = tmp_path / "me.wav"
    ref.write_bytes(b"RIFF....WAVEdata")
    svc.speech("hello there", ref)
    body = json.loads(seen[0][2])
    assert seen[0][0] == "/v1/audio/speech" and body["input"] == "hello there" and body["language_id"] == "en"
    assert seen[1][0] == "/v1/audio/speech/upload" and seen[1][1].startswith("multipart/form-data")
    assert b'name="file"' in seen[1][2] and b'name="request"' in seen[1][2]


def test_errors_are_friendly_and_redacted(tmp_path):
    def handler(req):
        return httpx.Response(401, text=f"bad key {SECRET}")

    with pytest.raises(ServiceError) as e:
        svc_with(handler, tmp_path).image("x")
    assert "CLASS_API_KEY" in str(e.value) and SECRET not in str(e.value)


def test_plan_song_parses_fenced_json(tmp_path):
    class Fake:
        def chat(self, messages, schema=None, **kw):
            return '```json\n{"title":"Neon Rain","summary":"s","caption":"synthwave","bpm":96,' \
                   '"lyrics":"[Verse]\\nline one\\nline two","cover_prompt":"city"}\n```'

    plan = plan_song(Fake(), SongRequest("Synthwave", "Dreamy", 60, True))
    assert plan.title == "Neon Rain" and "96 bpm" in plan.caption and "no text" in plan.cover_prompt


def test_clean_speech_strips_stage_directions():
    assert clean_speech('*laughs* Hey there! 🎵 (pause) That was "Neon Rain".') == 'Hey there! That was "Neon Rain".'


def test_unreachable_service_names_the_host(tmp_path):
    def handler(req):
        raise httpx.ConnectTimeout("no route", request=req)

    with pytest.raises(ServiceError, match=r"could not connect to img "):
        svc_with(handler, tmp_path).image("x")


def test_status_reports_each_service(tmp_path):
    def handler(req):
        return httpx.Response(200 if req.url.host != "tts" else 503, json={})

    st = svc_with(handler, tmp_path).status()
    assert st["text"]["ok"] and st["music"]["host"] == "music" and not st["speech"]["ok"]


def test_bad_request_includes_service_message(tmp_path):
    def handler(req):
        return httpx.Response(400, json={"error": {"message": "Input should be 'chatterbox-v3'"}})

    with pytest.raises(ServiceError, match="Input should be 'chatterbox-v3'"):
        svc_with(handler, tmp_path).speech("hi")


def test_normalize_lyrics_repairs_run_on_text():
    from jukebox.creative import normalize_lyrics
    raw = "[Verse]Golden light spills across the sand,Waves whisper soft in a rhythmic band,No one else is here to see.[Chorus]Oh, let the evening carry us away,Where the stars begin to play."
    lines = normalize_lyrics(raw).splitlines()
    assert lines[0] == "[Verse]" and lines[1] == "Golden light spills across the sand,"
    assert "[Chorus]" in lines and lines[-1] == "Where the stars begin to play."
    good = "[Verse]\nShort line one\nShort line two, and more\n\n[Chorus]\nHold on"
    assert normalize_lyrics(good) == good
    assert normalize_lyrics("[Instrumental]") == "[Instrumental]"


def test_duplicate_title_gets_suffix():
    class Fake:
        def chat(self, messages, schema=None, **kw):
            assert "Neon Rain" in messages[-1]["content"]  # existing titles are passed to Qwen
            return '{"title":"Neon Rain","summary":"s","caption":"c","bpm":96,"lyrics":"[Verse]\\nline one here\\nline two here","cover_prompt":"x"}'

    plan = plan_song(Fake(), SongRequest("Synth-Pop", "Dreamy", 60, True), avoid_titles=["Neon Rain", "neon rain II"])
    assert plan.title == "Neon Rain III"
