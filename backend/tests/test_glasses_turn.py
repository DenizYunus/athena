"""The glasses path: the question arrives as an audio file (no video track) with a photo
from the glasses camera. The audio is transcribed; the guide sees text plus the photo."""

from __future__ import annotations

import json

from app import asr

M4A = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A mp42isom" + b"\x00" * 64
WAV = b"RIFF\x24\x08\x00\x00WAVEfmt " + b"\x00" * 64


def test_is_audio_only_from_content_type_or_header():
    assert asr.is_audio_only("audio/mp4", b"\x00" * 16)
    assert asr.is_audio_only("audio/wav", b"")
    assert asr.is_audio_only(None, M4A)
    assert asr.is_audio_only("application/octet-stream", WAV)
    assert asr.is_audio_only(None, b"ID3\x04" + b"\x00" * 20)
    assert not asr.is_audio_only("video/mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    assert not asr.is_audio_only(None, b"\x00" * 64)
    assert not asr.is_audio_only(None, b"")


def test_audio_clip_with_photo_is_transcribed_not_sent_as_video(app_client, fake_client, monkeypatch, jpeg_bytes):
    from app import main

    seen = {}

    async def fake_transcribe(api_key, media, keywords=None):
        seen["media"] = media
        seen["keywords"] = keywords
        return "one pin looks bent"

    monkeypatch.setattr(main.asr, "transcribe_clip", fake_transcribe)
    sid = app_client.post("/sessions", json={"skill_id": "fridge-start-relay"}).json()["id"]
    r = app_client.post(
        f"/sessions/{sid}/turn",
        files={"clip": ("question.m4a", M4A, "audio/mp4"), "photo": ("glasses.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["transcript"] == "one pin looks bent"
    assert seen["media"] == M4A and "start relay" in seen["keywords"]
    content = fake_client.calls[-1]["input"][-1]["content"]
    assert [b["type"] for b in content] == ["input_text", "input_image"]
    assert content[0]["text"] == "one pin looks bent"
    history = app_client.get(f"/sessions/{sid}").json()["history"]
    assert history[0]["had_photo"] is True and history[0]["had_clip"] is False


def test_streamed_audio_clip_detected_by_header(app_client, fake_client, monkeypatch):
    from app import main

    async def fake_transcribe(api_key, media, keywords=None):
        return "done"

    monkeypatch.setattr(main.asr, "transcribe_clip", fake_transcribe)
    sid = app_client.post("/sessions", json={"skill_id": "fridge-start-relay"}).json()["id"]
    r = app_client.post(f"/sessions/{sid}/turn?stream=true", files={"clip": ("q.bin", WAV, "application/octet-stream")})
    assert r.status_code == 200
    events = [json.loads(line[5:].strip()) for line in r.text.splitlines() if line.startswith("data:")]
    assert events[0] == {"transcribing": True}
    assert events[1] == {"transcript": "done"}
    assert events[-1]["done"] is True
    content = fake_client.calls[-1]["input"][-1]["content"]
    assert [b["type"] for b in content] == ["input_text"]


def test_video_clip_still_goes_to_the_guide(app_client, fake_client):
    sid = app_client.post("/sessions", json={"skill_id": "fridge-start-relay"}).json()["id"]
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    r = app_client.post(f"/sessions/{sid}/turn", data={"transcript": "look"}, files={"clip": ("c.mp4", mp4, "video/mp4")})
    assert r.status_code == 200
    content = fake_client.calls[-1]["input"][-1]["content"]
    assert [b["type"] for b in content] == ["input_text", "input_video"]


def test_audio_clip_transcription_failure_fails_the_turn(app_client, fake_client, monkeypatch, jpeg_bytes):
    """The question audio never reaches the guide, so a failed transcription must not be swallowed."""
    from app import main

    async def boom(api_key, media, keywords=None):
        raise RuntimeError("ffmpeg not found")

    monkeypatch.setattr(main.asr, "transcribe_clip", boom)
    sid = app_client.post("/sessions", json={"skill_id": "fridge-start-relay"}).json()["id"]
    calls_before = len(fake_client.calls)
    r = app_client.post(
        f"/sessions/{sid}/turn",
        files={"clip": ("question.m4a", M4A, "audio/mp4"), "photo": ("glasses.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert r.status_code == 502
    assert r.json()["detail"].startswith("Could not transcribe your question")
    assert len(fake_client.calls) == calls_before  # the guide was not asked

    r = app_client.post(f"/sessions/{sid}/turn?stream=true", files={"clip": ("question.m4a", M4A, "audio/mp4")})
    events = [json.loads(line[5:].strip()) for line in r.text.splitlines() if line.startswith("data:")]
    assert events[0] == {"transcribing": True}
    assert events[-1]["error"].startswith("Could not transcribe your question")
    assert not any("done" in e for e in events)
    assert app_client.get(f"/sessions/{sid}").json()["history"] == []


def test_upstream_status_is_not_shown_to_the_app(app_client, monkeypatch):
    """The app maps a bare "503" to "no API key"; a failing speech service must not read like that."""
    from app import main

    async def overloaded(api_key, media, keywords=None):
        raise asr.TranscribeError("transcribe failed (503): upstream overloaded", 503)

    monkeypatch.setattr(main.asr, "transcribe_clip", overloaded)
    sid = app_client.post("/sessions", json={"skill_id": "fridge-start-relay"}).json()["id"]
    r = app_client.post(f"/sessions/{sid}/turn", files={"clip": ("question.m4a", M4A, "audio/mp4")})
    assert r.status_code == 502
    assert "503" not in r.json()["detail"] and "speech service" in r.json()["detail"]


def test_silent_audio_clip_without_photo_is_refused(app_client, fake_client, monkeypatch):
    from app import main

    async def silence(api_key, media, keywords=None):
        return ""

    monkeypatch.setattr(main.asr, "transcribe_clip", silence)
    sid = app_client.post("/sessions", json={"skill_id": "fridge-start-relay"}).json()["id"]
    before = len(fake_client.calls)
    r = app_client.post(f"/sessions/{sid}/turn", files={"clip": ("question.m4a", M4A, "audio/mp4")})
    assert r.status_code == 422 and r.json()["detail"].startswith("Didn't catch your question")
    r = app_client.post(f"/sessions/{sid}/turn?stream=true", files={"clip": ("question.m4a", M4A, "audio/mp4")})
    events = [json.loads(line[5:].strip()) for line in r.text.splitlines() if line.startswith("data:")]
    assert events[-1]["error"].startswith("Didn't catch your question")
    assert len(fake_client.calls) == before  # the guide was never asked


async def test_transcribe_wav_network_error_is_a_runtime_error(monkeypatch):
    import httpx
    import pytest

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    real = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    with pytest.raises(asr.TranscribeError, match="transcribe failed"):
        await asr.transcribe_wav("LLM|1|k", b"RIFF....")


def test_video_clip_survives_a_network_failure_of_the_transcription(app_client, fake_client, monkeypatch):
    import httpx

    from app import main

    async def timeout_as_runtime(api_key, media, keywords=None):
        try:
            raise httpx.ReadTimeout("slow")
        except httpx.HTTPError as exc:  # what transcribe_wav now does with it
            raise RuntimeError(f"transcribe failed: {exc!r}") from exc

    monkeypatch.setattr(main.asr, "transcribe_clip", timeout_as_runtime)
    sid = app_client.post("/sessions", json={"skill_id": "fridge-start-relay"}).json()["id"]
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    r = app_client.post(f"/sessions/{sid}/turn", files={"clip": ("c.mp4", mp4, "video/mp4")})
    assert r.status_code == 200 and r.json()["transcript"] == ""
    assert [b["type"] for b in fake_client.calls[-1]["input"][-1]["content"]] == ["input_text", "input_video"]


def test_rejected_key_on_the_speech_service_says_so(app_client, monkeypatch):
    from app import main

    async def rejected(api_key, media, keywords=None):
        raise asr.TranscribeError("transcribe failed (401): bad key", 401)

    monkeypatch.setattr(main.asr, "transcribe_clip", rejected)
    sid = app_client.post("/sessions", json={"skill_id": "fridge-start-relay"}).json()["id"]
    r = app_client.post(f"/sessions/{sid}/turn", files={"clip": ("question.m4a", M4A, "audio/mp4")})
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "API key" in detail and "401" not in detail
