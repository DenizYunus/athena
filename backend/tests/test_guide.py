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


def test_prepare_image_turns_photo_upright_from_exif():
    """A phone photo taken with skipProcessing keeps sensor-oriented pixels plus an Orientation tag."""
    from PIL import Image
    import io

    img = Image.new("RGB", (400, 300), (200, 30, 30))
    img.paste((30, 30, 200), (0, 0, 400, 30))  # a blue band along the top of the sensor frame
    exif = Image.Exif()
    exif[0x0112] = 6  # stored rotated: display it turned 90 degrees clockwise
    raw = io.BytesIO()
    img.save(raw, format="JPEG", exif=exif.tobytes())

    out = prepare_image(raw.getvalue())
    with Image.open(io.BytesIO(out)) as upright:
        assert upright.size == (300, 400)  # portrait, as the phone shows it
        assert upright.getexif().get(0x0112) is None  # no tag left to apply twice
        r, g, b = upright.getpixel((290, 200))  # the top band now runs down the right edge
        assert b > 150 and r < 100


def test_prepare_image_survives_a_malformed_exif_block():
    """Pillow's exif_transpose rewrites the whole EXIF block and raises on a tag stored with the wrong type."""
    from PIL import Image
    import io
    import struct

    entries = [
        struct.pack("<HHI4s", 0x0112, 3, 1, struct.pack("<HH", 6, 0)),  # Orientation 6, as a SHORT
        struct.pack("<HHI4s", 0x0117, 2, 4, b"Cam\x00"),  # StripByteCounts stored as ASCII
    ]
    tiff = b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", len(entries)) + b"".join(entries) + struct.pack("<I", 0)
    raw = io.BytesIO()
    Image.new("RGB", (400, 300), (10, 120, 10)).save(raw, format="JPEG", exif=b"Exif\x00\x00" + tiff)

    out = prepare_image(raw.getvalue())
    with Image.open(io.BytesIO(out)) as upright:
        assert upright.size == (300, 400)


def test_client_has_bounded_timeout_and_one_retry(monkeypatch):
    from app import meta_client

    monkeypatch.setattr(meta_client.settings, "model_timeout_s", 50.0)
    monkeypatch.setattr(meta_client.settings, "model_max_retries", 1)
    meta_client._client_for.cache_clear()
    c = meta_client.get_client("LLM|9|timeout-check")
    assert c.max_retries == 1
    assert c.timeout.read == 50.0 and c.timeout.connect == 10.0
    meta_client._client_for.cache_clear()


@pytest.mark.parametrize(
    "ending",
    [
        {"type": "error", "code": "server_error", "message": "upstream 503"},
        {"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"}}},
        {"type": "response.failed", "response": {"error": {"message": "boom"}}},
    ],
)
async def test_turn_stream_fails_on_broken_answer_and_keeps_history_clean(fake_client, skill, ending):
    from types import SimpleNamespace

    fake_client.next_ending = SimpleNamespace(**ending)
    session = SessionStore().create(skill)
    with pytest.raises(RuntimeError) as err:
        async for _ in guide.turn_stream(fake_client, skill, session, "is this the relay?"):
            pass
    assert "503" not in str(err.value)
    assert session.history == []


async def test_turn_stream_fails_on_empty_answer(fake_client, skill):
    fake_client.next_deltas = []
    session = SessionStore().create(skill)
    with pytest.raises(RuntimeError, match="empty answer"):
        await guide.turn(fake_client, skill, session, "hello")
    assert session.history == []


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


@pytest.mark.parametrize("output", ["", '{"status": "pass", "confid', "I can't help with that."])
async def test_verify_turns_an_unusable_answer_into_a_sentence(fake_client, skill, jpeg_bytes, output):
    fake_client.next_output_text = output
    session = SessionStore().create(skill)
    with pytest.raises(RuntimeError) as err:
        await guide.verify(fake_client, skill, session, jpeg_bytes)
    message = str(err.value)
    assert message.startswith("The checker") and "status" not in message and "help" not in message


async def test_verify_judges_the_step_it_is_given(fake_client, skill, jpeg_bytes):
    session = SessionStore().create(skill)  # the session is on step 1, the safety gate
    await guide.verify(fake_client, skill, session, jpeg_bytes, step=skill.step(2))
    call = fake_client.calls[-1]
    assert "SAFETY GATE" not in call["input"][0]["content"]
    assert "Step 2:" in call["input"][0]["content"]


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


def test_client_key_resolution(monkeypatch):
    from app import meta_client

    monkeypatch.setattr(meta_client.settings, "api_key", "")
    with pytest.raises(RuntimeError):
        meta_client.get_client(None)
    c = meta_client.get_client("LLM|1|phone-key")
    assert c.api_key == "LLM|1|phone-key"
    monkeypatch.setattr(meta_client.settings, "api_key", "LLM|2|server-key")
    assert meta_client.get_client(None).api_key == "LLM|2|server-key"
    assert meta_client.get_client("  ").api_key == "LLM|2|server-key"
