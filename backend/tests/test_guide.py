from __future__ import annotations

import base64

import pytest

from app import guide
from app.media import prepare_image
from app.schemas import VERDICT_JSON_SCHEMA, Skill, StepVerdict
from app.store import SessionStore, SkillStore


@pytest.fixture
def skill() -> Skill:
    return SkillStore().get("fridge-start-relay")


def test_seed_skill_loads(skill):
    assert skill.title.startswith("Replace a refrigerator")
    assert skill.step(1).safety_gate is True
    assert len(skill.steps) == 7


def test_prepare_image_resizes_long_side(jpeg_bytes):
    from PIL import Image
    import io

    out = prepare_image(jpeg_bytes)
    with Image.open(io.BytesIO(out)) as img:
        assert max(img.size) == 1280
        assert img.format == "JPEG"


def test_verdict_schema_is_strict_safe():
    def walk(node):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            assert set(node["required"]) == set(node["properties"].keys())
            for child in node["properties"].values():
                walk(child)
        assert "anyOf" not in node and "oneOf" not in node and "allOf" not in node

    walk(VERDICT_JSON_SCHEMA)


async def test_turn_sends_clip_and_transcript(fake_client, skill, jpeg_bytes):
    session = SessionStore().create(skill)
    session.current_step = 4
    clip = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100

    reply = await guide.turn(fake_client, skill, session, "is this the relay?", clip_mp4=clip)

    assert reply == "Yes, that's the relay. Grip the body."
    call = fake_client.calls[-1]
    assert call["model"] == "muse-spark-1.3"
    assert call["stream"] is True
    system = call["input"][0]
    assert system["role"] == "system"
    assert "Current step: 4" in system["content"]
    user = call["input"][-1]
    assert user["role"] == "user"
    kinds = [b["type"] for b in user["content"]]
    assert kinds == ["input_text", "input_video"]
    assert user["content"][0]["text"] == "is this the relay?"
    assert user["content"][1]["video_url"].startswith("data:video/mp4;base64,")
    assert base64.b64decode(user["content"][1]["video_url"].split(",", 1)[1]) == clip
    # history recorded both sides
    assert [m.role for m in session.history] == ["user", "assistant"]
    assert session.history[0].had_clip is True


async def test_turn_history_is_replayed_as_text(fake_client, skill):
    session = SessionStore().create(skill)
    await guide.turn(fake_client, skill, session, "first question")
    await guide.turn(fake_client, skill, session, "second question")
    call = fake_client.calls[-1]
    roles = [item.get("role") for item in call["input"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert call["input"][1]["content"] == "first question"


async def test_turn_uploads_large_clip_to_files_api(fake_client, skill, monkeypatch):
    from app import media

    monkeypatch.setattr(media.settings, "inline_media_limit_bytes", 10)
    session = SessionStore().create(skill)
    await guide.turn(fake_client, skill, session, "", clip_mp4=b"x" * 50)
    assert fake_client.uploads and fake_client.uploads[0]["purpose"] == "user_data"
    user = fake_client.calls[-1]["input"][-1]
    assert user["content"][1] == {"type": "input_video", "file_id": "file-fake-123"}
    assert user["content"][0]["text"].startswith("(no words")


async def test_verify_uses_structured_output(fake_client, skill, jpeg_bytes):
    session = SessionStore().create(skill)
    verdict = await guide.verify(fake_client, skill, session, jpeg_bytes)
    assert isinstance(verdict, StepVerdict)
    assert verdict.status == "pass"
    call = fake_client.calls[-1]
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["strict"] is True
    assert call["text"]["format"]["name"] == "StepVerdict"
    assert "SAFETY GATE" in call["input"][0]["content"]
    blocks = call["input"][1]["content"]
    assert [b["type"] for b in blocks] == ["input_text", "input_image"]
    assert "no reference photo" in blocks[0]["text"]


async def test_verify_demotes_hesitant_pass_on_safety_gate(fake_client, skill, jpeg_bytes):
    fake_client.set_verdict(status="pass", confidence=0.55)
    session = SessionStore().create(skill)  # step 1 is the gate
    verdict = await guide.verify(fake_client, skill, session, jpeg_bytes)
    assert verdict.status == "unclear"
    assert verdict.photo_request.needed is True


async def test_verify_keeps_hesitant_pass_on_normal_step(fake_client, skill, jpeg_bytes):
    fake_client.set_verdict(status="pass", confidence=0.55)
    session = SessionStore().create(skill)
    session.current_step = 2
    verdict = await guide.verify(fake_client, skill, session, jpeg_bytes)
    assert verdict.status == "pass"
