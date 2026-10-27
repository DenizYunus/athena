"""SAM highlight, voice transcription paths, and the skill compiler."""

from __future__ import annotations

import io
import json
import wave

import pytest

from app import asr, sam
from app.compiler import Capture, compile_skill
from app.schemas import Highlight, Skill
from app.store import SessionStore, SkillStore


# ---------------------------------------------------------------- SAM


async def test_highlight_parses_sam_wire(fake_client, jpeg_bytes):
    h = await sam.highlight(fake_client, jpeg_bytes, ["black box"])
    assert isinstance(h, Highlight)
    assert h.concept == "black box"
    # frame is the prepared image (2000x1500 -> 1280x960), not the mask bounds
    assert (h.frame_width, h.frame_height) == (1280, 960)
    assert len(h.objects) == 1
    o = h.objects[0]
    assert (o.box.x1, o.box.y1) == (413, 490)
    assert 543 <= o.box.x2 <= 544 and 632 <= o.box.y2 <= 633  # parser reports exclusive edges
    assert 0.5 < o.confidence < 0.6
    assert o.mask is not None
    assert (o.mask.left, o.mask.top) == (413, 490)
    assert o.mask.width > 100 and o.mask.height > 100
    assert o.mask.path.startswith("M")
    call = fake_client.calls[-1]
    assert call["model"] == "sam-3.1"
    assert call["metadata"] == {"include_confidence": "true", "mask_encoding": "one_bit"}
    assert call["input"][0]["content"][1]["image_url"].startswith("data:image/jpeg;base64,")


async def test_highlight_prefers_first_concept_that_hits(fake_client, jpeg_bytes):
    fake_client.sam_hits = {"small black box"}
    h = await sam.highlight(fake_client, jpeg_bytes, ["start relay", "small black box", "black box"])
    assert h is not None and h.concept == "small black box"
    assert [c["input"][0]["content"][0]["text"] for c in fake_client.calls] == ["start relay", "small black box", "black box"]


async def test_highlight_none_when_nothing_matches(fake_client, jpeg_bytes):
    fake_client.sam_hits = set()
    assert await sam.highlight(fake_client, jpeg_bytes, ["unicorn"]) is None
    assert await sam.highlight(fake_client, jpeg_bytes, []) is None


# ---------------------------------------------------------------- voice


def _wav(sample_rate=44100, channels=2, seconds=0.5) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * channels * int(sample_rate * seconds))
    return out.getvalue()


@pytest.mark.skipif(not asr.ffmpeg_available(), reason="ffmpeg not installed")
async def test_media_to_wav_converts_to_16k_mono():
    data = await asr.media_to_wav(_wav())
    with wave.open(io.BytesIO(data), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 16000
        assert w.getsampwidth() == 2
        assert abs(w.getnframes() - 8000) < 100


async def test_transcribe_wav_request_shape(monkeypatch):
    import httpx

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.content
        return httpx.Response(200, json={"sessionId": "x", "transcript": " Is this the relay? ", "audioDurationMs": 1200, "turns": []})

    real = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    text = await asr.transcribe_wav("LLM|1|k", b"RIFF....", ["start relay"])
    assert text == "Is this the relay?"
    assert seen["url"].endswith("/asr/transcribe")
    assert seen["auth"] == "Bearer LLM|1|k"
    body = seen["body"]
    assert b'name="request"' in body and b'"keywords": ["start relay"]' in body
    assert b'name="audio"; filename="clip.wav"' in body
    assert b'"audioEncoding": "WAV"' in body


def test_turn_uses_clip_transcription_when_no_transcript(app_client, fake_client, monkeypatch):
    from app import main

    async def fake_transcribe(api_key, media, keywords=None):
        assert api_key == fake_client.api_key
        assert "start relay" in (keywords or [])
        return "is this the relay?"

    monkeypatch.setattr(main.asr, "transcribe_clip", fake_transcribe)
    sid = app_client.post("/sessions", json={"skill_id": "fridge-start-relay"}).json()["id"]
    r = app_client.post(f"/sessions/{sid}/turn", files={"clip": ("c.mp4", b"\x00" * 64, "video/mp4")})
    assert r.status_code == 200, r.text
    assert r.json()["transcript"] == "is this the relay?"
    user = fake_client.calls[-1]["input"][-1]
    assert user["content"][0] == {"type": "input_text", "text": "is this the relay?"}
    assert user["content"][1]["type"] == "input_video"


def test_turn_survives_transcription_failure(app_client, fake_client, monkeypatch):
    from app import main

    async def boom(api_key, media, keywords=None):
        raise RuntimeError("asr down")

    monkeypatch.setattr(main.asr, "transcribe_clip", boom)
    sid = app_client.post("/sessions", json={"skill_id": "fridge-start-relay"}).json()["id"]
    r = app_client.post(f"/sessions/{sid}/turn", files={"clip": ("c.mp4", b"\x00" * 64, "video/mp4")})
    assert r.status_code == 200
    assert r.json()["transcript"] == ""
    assert r.json()["reply"].startswith("Yes")


def test_author_transcribe_endpoint(app_client, monkeypatch):
    from app import main

    async def fake_transcribe(api_key, media, keywords=None):
        return f"note with {len(media)} bytes, bias {keywords}"

    monkeypatch.setattr(main.asr, "transcribe_clip", fake_transcribe)
    r = app_client.post("/author/transcribe", data={"keywords": "caliper, rotor"}, files={"clip": ("n.mp4", b"abc", "video/mp4")})
    assert r.status_code == 200
    assert r.json()["transcript"] == "note with 3 bytes, bias ['caliper', 'rotor']"


def test_asr_socket_relays(app_client, monkeypatch):
    from app import main

    async def fake_relay(ws, api_key, keywords, sample_rate=16000):
        await ws.send_text(json.dumps({"type": "ready", "keywords": keywords, "rate": sample_rate}))
        chunk = await ws.receive_bytes()
        await ws.send_text(json.dumps({"type": "transcript", "transcript": f"{len(chunk)} bytes", "final": True}))

    monkeypatch.setattr(main.asr, "relay", fake_relay)
    with app_client.websocket_connect("/asr") as ws:
        ws.send_text(json.dumps({"type": "start", "skill_id": "fridge-start-relay", "api_key": "LLM|1|k", "sample_rate": 16000}))
        ready = json.loads(ws.receive_text())
        assert ready["type"] == "ready" and "start relay" in ready["keywords"] and ready["rate"] == 16000
        ws.send_bytes(b"\x00" * 640)
        assert json.loads(ws.receive_text())["transcript"] == "640 bytes"


def test_asr_socket_rejects_bad_start(app_client):
    with app_client.websocket_connect("/asr") as ws:
        ws.send_text(json.dumps({"type": "audio"}))
        assert json.loads(ws.receive_text())["type"] == "error"


# ---------------------------------------------------------------- compiler


async def test_compile_skill_builds_skill_and_media(fake_client, jpeg_bytes):
    captures = [Capture(photo=jpeg_bytes, note="wheel off"), Capture(photo=jpeg_bytes, note="pads in"), Capture(photo=None, note="takes 15 minutes")]
    skill, media = await compile_skill(fake_client, captures, author="Dana", title_hint="brake pads")
    assert isinstance(skill, Skill)
    assert skill.id.startswith("replace-a-bike-brake-pad-")
    assert skill.author == "Dana" and skill.compiled is True
    assert [s.n for s in skill.steps] == [1, 2]
    assert skill.steps[0].reference_image == f"compiled/media/{skill.id}/photo_1.jpg"
    assert skill.steps[1].reference_image == f"compiled/media/{skill.id}/photo_2.jpg"
    assert skill.steps[0].gate_requirement is None
    assert skill.steps[1].watch_me is True
    assert set(media) == {f"compiled/media/{skill.id}/photo_1.jpg", f"compiled/media/{skill.id}/photo_2.jpg"}
    call = fake_client.calls[-1]
    assert call["text"]["format"]["strict"] is True and call["text"]["format"]["name"] == "CompiledSkill"
    blocks = call["input"][1]["content"]
    kinds = [b["type"] for b in blocks]
    assert kinds == ["input_text", "input_text", "input_image", "input_text", "input_image", "input_text", "input_text"]
    assert "brake pads" in blocks[0]["text"]
    assert blocks[1]["text"] == "Photo 1. Expert's note: wheel off"
    assert blocks[5]["text"].startswith("Expert's note without a photo")


def test_compiled_schema_is_strict_safe():
    from app.compiler import COMPILED_SKILL_JSON_SCHEMA

    def walk(node):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            assert set(node["required"]) == set(node["properties"].keys())
            for child in node["properties"].values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])
        assert "anyOf" not in node and "oneOf" not in node and "allOf" not in node

    walk(COMPILED_SKILL_JSON_SCHEMA)


def test_compile_endpoint_publishes_and_deletes(app_client, jpeg_bytes, tmp_path):
    r = app_client.post(
        "/skills/compile",
        data={"author": "Dana", "title_hint": "brake", "notes": ["wheel off", "pads in"]},
        files=[("photos", ("a.jpg", jpeg_bytes, "image/jpeg")), ("photos", ("b.jpg", jpeg_bytes, "image/jpeg"))],
    )
    assert r.status_code == 201, r.text
    skill = r.json()
    assert skill["compiled"] is True and skill["author"] == "Dana"
    assert (tmp_path / "compiled" / f"{skill['id']}.json").exists()
    assert (tmp_path / "compiled" / "media" / skill["id"] / "photo_2.jpg").exists()
    ids = [s["id"] for s in app_client.get("/skills").json()]
    assert skill["id"] in ids and "fridge-start-relay" in ids

    media = app_client.get(f"/skills/{skill['id']}/media/photo_1.jpg")
    assert media.status_code == 200 and media.headers["content-type"] == "image/jpeg"
    assert app_client.get(f"/skills/{skill['id']}/media/../x").status_code in (404, 422)

    # a session on the compiled skill verifies against its reference photo
    sid = app_client.post("/sessions", json={"skill_id": skill["id"]}).json()["id"]
    v = app_client.post(f"/sessions/{sid}/verify", files={"photo": ("p.jpg", jpeg_bytes, "image/jpeg")})
    assert v.status_code == 200, v.text
    assert v.json()["highlight"]["concept"] == "metal clamp"

    assert app_client.delete("/skills/fridge-start-relay").status_code == 403
    assert app_client.delete(f"/skills/{skill['id']}").status_code == 204
    assert app_client.get(f"/skills/{skill['id']}").status_code == 404
    assert not (tmp_path / "compiled" / f"{skill['id']}.json").exists()


def test_compile_endpoint_needs_input(app_client):
    assert app_client.post("/skills/compile", data={"author": "x"}).status_code == 422


# ---------------------------------------------------------------- verify + highlight


def test_verify_returns_highlight_on_fail(app_client, fake_client, jpeg_bytes):
    fake_client.set_verdict(status="fail", confidence=0.9, what_to_fix="Pull the plug fully out.")
    sid = app_client.post("/sessions", json={"skill_id": "fridge-start-relay"}).json()["id"]
    r = app_client.post(f"/sessions/{sid}/verify", files={"photo": ("p.jpg", jpeg_bytes, "image/jpeg")})
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"]["status"] == "fail" and body["advanced"] is False
    assert body["highlight"]["concept"] == "power cord plug"
    assert body["highlight"]["objects"][0]["mask"]["path"].startswith("M")
    sam_calls = [c for c in fake_client.calls if c["model"] == "sam-3.1"]
    assert len(sam_calls) == 3  # every concept tried at once


def test_verify_can_skip_highlight(app_client, fake_client, jpeg_bytes):
    sid = app_client.post("/sessions", json={"skill_id": "fridge-start-relay"}).json()["id"]
    r = app_client.post(f"/sessions/{sid}/verify?highlight=false", files={"photo": ("p.jpg", jpeg_bytes, "image/jpeg")})
    assert r.status_code == 200 and r.json()["highlight"] is None
    assert not [c for c in fake_client.calls if c["model"] == "sam-3.1"]


def test_highlight_endpoint_with_named_concept(app_client, fake_client, jpeg_bytes):
    sid = app_client.post("/sessions", json={"skill_id": "fridge-start-relay"}).json()["id"]
    r = app_client.post(f"/sessions/{sid}/highlight", data={"concept": "copper pipe"}, files={"photo": ("p.jpg", jpeg_bytes, "image/jpeg")})
    assert r.status_code == 200 and r.json()["concept"] == "copper pipe"


def test_seed_skill_reference_photo_resolves():
    skill = SkillStore().get("fridge-start-relay")
    from app.media import image_block_from_source

    block = image_block_from_source(skill.step(2).reference_image)
    assert block["image_url"].startswith("data:image/jpeg;base64,")
    assert all(s.sam_concepts for s in skill.steps)
