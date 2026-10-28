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
