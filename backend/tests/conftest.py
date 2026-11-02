"""A fake Meta client. Records every request and returns canned responses.

Tests never touch the network. The fake mirrors the SDK surfaces the backend uses:
client.responses.create(...) (streamed and not), client.files.create(...) and client.files.delete(...).
"""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


class FakeStream:
    def __init__(self, deltas: list[str], ending: object | None = None):
        self._deltas = deltas
        self._ending = ending  # an event to end with instead of response.completed

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        # Same lane fields the real SDK events carry; the SAM parser checks them.
        lane = {"item_id": "msg_fake", "output_index": 0, "content_index": 0}
        yield SimpleNamespace(type="response.created", response=SimpleNamespace(status="in_progress"))
        yield SimpleNamespace(type="response.output_item.added", output_index=0, item=SimpleNamespace(id="msg_fake", type="message"))
        for d in self._deltas:
            yield SimpleNamespace(type="response.output_text.delta", delta=d, **lane)
        if self._ending is not None:
            yield self._ending
            return
        yield SimpleNamespace(type="response.output_text.done", text="".join(self._deltas), **lane)
        yield SimpleNamespace(type="response.completed", response=SimpleNamespace(status="completed"))


SAM_WIRE = (Path(__file__).parent / "fixtures" / "sam_black_box.txt").read_text()


class FakeResponses:
    def __init__(self, parent: "FakeClient"):
        self.parent = parent

    async def create(self, **kwargs):
        self.parent.calls.append(kwargs)
        model = kwargs.get("model", "")
        delay = self.parent.sam_delay if model.startswith("sam") else self.parent.delay
        if delay:
            await asyncio.sleep(delay)
        if self.parent.fail_with is not None:
            raise self.parent.fail_with
        if model.startswith("sam"):
            concept = kwargs["input"][0]["content"][0]["text"]
            self.parent.sam_calls += 1
            missed_on_purpose = self.parent.sam_calls <= self.parent.sam_miss_first
            hit = not missed_on_purpose and (self.parent.sam_hits is None or concept in self.parent.sam_hits)
            return FakeStream([SAM_WIRE] if hit else [])
        fmt = (kwargs.get("text") or {}).get("format") or {}
        if fmt.get("name") == "CompiledSkill":
            return SimpleNamespace(output_text=self.parent.next_compiled)
        if fmt.get("name") == "DigestChunk":
            outputs = self.parent.next_chunk_outputs
            text = outputs(kwargs) if callable(outputs) else outputs.pop(0)
            return SimpleNamespace(output_text=text)
        if fmt.get("name") == "DigestedSkill":
            digested = self.parent.next_digested
            return SimpleNamespace(output_text=digested(kwargs) if callable(digested) else digested)
        if kwargs.get("stream"):
            return FakeStream(self.parent.next_deltas, self.parent.next_ending)
        return SimpleNamespace(output_text=self.parent.next_output_text)


class FakeFiles:
    def __init__(self, parent: "FakeClient"):
        self.parent = parent

    async def create(self, **kwargs):
        if self.parent.upload_fail_after is not None and len(self.parent.uploads) >= self.parent.upload_fail_after:
            raise self.parent.upload_fail_with or RuntimeError("upload failed")
        name, content, mime = kwargs["file"]
        # The digester passes an open file that is closed after the call, so read it now.
        data = content.read() if hasattr(content, "read") else content
        file_id = f"file-fake-{123 + len(self.parent.uploads)}"
        self.parent.uploads.append({**kwargs, "id": file_id, "name": name, "mime": mime, "size": len(data), "head": data[:8]})
        return SimpleNamespace(id=file_id)

    async def delete(self, file_id: str):
        self.parent.deleted.append(file_id)
        return SimpleNamespace(id=file_id, deleted=True)


COMPILED_EXAMPLE = {
    "title": "Replace a bike brake pad",
    "domain": "bike",
    "difficulty": 2,
    "est_minutes": 15,
    "tools": ["5 mm hex key"],
    "parts": ["brake pads"],
    "vocabulary": ["caliper", "brake pad", "rotor"],
    "steps": [
        {
            "instruction": "Flip the bike and remove the wheel.",
            "expected_state": "The wheel is off and the caliper is empty.",
            "reference_photo": 1,
            "sam_concepts": ["metal clamp", "silver bracket"],
            "verify_hints": "Wheel must be out of the frame.",
            "common_mistakes": ["Squeezing the brake lever with the wheel out"],
            "safety_gate": False,
            "gate_requirement": "",
            "watch_me": False,
            "est_minutes": 3,
        },
        {
            "instruction": "Slide the new pads into the caliper until they click.",
            "expected_state": "Both pads seated in the caliper, retaining pin in place.",
            "reference_photo": 2,
            "sam_concepts": ["small metal plate", "black pad"],
            "verify_hints": "Reject if the pin is missing.",
            "common_mistakes": [],
            "safety_gate": False,
            "gate_requirement": "",
            "watch_me": True,
            "est_minutes": 5,
        },
    ],
}


DIGESTED_EXAMPLE = {
    "title": "Replace a bike brake pad",
    "domain": "bike",
    "difficulty": 2,
    "est_minutes": 15,
    "tools": ["5 mm hex key"],
    "parts": ["brake pads"],
    "vocabulary": ["caliper", "brake pad", "rotor"],
    "conflicts": [
        {
            "topic": "Hex key size",
            "video_says": "The expert uses a 4 mm key.",
            "document_says": "Use a 5 mm key.",
            "resolution": "The skill uses the 5 mm key from the document.",
        }
    ],
    "steps": [
        {
            "instruction": "Squeeze the brake lever and check it is free before you start.",
            "expected_state": "The bike is on its side with the brake lever visible.",
            "sam_concepts": ["black lever"],
            "verify_hints": "",
            "common_mistakes": [],
            "safety_gate": True,
            "gate_requirement": "The bike is on its side and the wheel does not spin.",
            "watch_me": False,
            "est_minutes": 1,
            "start_s": -1,
            "end_s": -1,
            "frame_s": -1,
            "source": "added",
            "document_note": "",
        },
        {
            "instruction": "Remove the wheel.",
            "expected_state": "The wheel is off and the caliper is empty.",
            "sam_concepts": ["metal clamp", "silver bracket"],
            "verify_hints": "Wheel must be out of the frame.",
            "common_mistakes": ["Squeezing the brake lever with the wheel out"],
            "safety_gate": False,
            "gate_requirement": "",
            "watch_me": False,
            "est_minutes": 3,
            "start_s": 1,
            "end_s": 3,
            "frame_s": 2,
            "source": "both",
            "document_note": "Open the quick release fully before pulling the wheel.",
        },
        {
            "instruction": "Slide the new pads into the caliper until they click.",
            "expected_state": "Both pads seated in the caliper, retaining pin in place.",
            "sam_concepts": ["small metal plate", "black pad"],
            "verify_hints": "Reject if the pin is missing.",
            "common_mistakes": [],
            "safety_gate": False,
            "gate_requirement": "",
            "watch_me": True,
            "est_minutes": 5,
            "start_s": 3,
            "end_s": 6,
            "frame_s": 5,
            "source": "video",
            "document_note": "",
        },
        {
            "instruction": "Tighten the pad retaining bolt.",
            "expected_state": "The retaining bolt is in and flush with the caliper.",
            "sam_concepts": ["small bolt"],
            "verify_hints": "",
            "common_mistakes": [],
            "safety_gate": False,
            "gate_requirement": "",
            "watch_me": False,
            "est_minutes": 1,
            "start_s": -1,
            "end_s": -1,
            "frame_s": -1,
            "source": "document",
            "document_note": "Tighten to 6 Nm.",
        },
    ],
}


class FakeClient:
    api_key = "LLM|0|fake-key"

    def __init__(self):
        self.calls: list[dict] = []
        self.uploads: list[dict] = []
        self.deleted: list[str] = []  # file ids passed to files.delete
        self.upload_fail_after: int | None = None  # files.create fails once this many uploads went through
        self.upload_fail_with: Exception | None = None
        self.sam_hits: set[str] | None = None  # None: every concept hits
        self.sam_calls = 0
        self.sam_miss_first = 0  # the first N SAM calls (one per concept) find nothing, whatever the concept
        # Digest calls, routed by the structured output name: a list consumed in order, or a callable(kwargs) -> str.
        self.next_chunk_outputs: list[str] | Callable[[dict], str] = []
        self.next_digested: str | Callable[[dict], str] = json.dumps(DIGESTED_EXAMPLE)
        self.fail_with: Exception | None = None  # raised by every responses.create call when set
        self.delay = 0.0  # seconds each non-SAM responses.create call waits, to overlap requests
        self.sam_delay = 0.0  # seconds each SAM call waits
        self.next_compiled = json.dumps(COMPILED_EXAMPLE)
        self.next_deltas = ["Yes, ", "that's the relay. ", "Grip the body."]
        self.next_ending: object | None = None  # e.g. an "error" event to end the guide stream with
        self.next_output_text = json.dumps(
            {
                "status": "pass",
                "confidence": 0.93,
                "reason": "The plug is out of the outlet and both are in frame.",
                "what_to_fix": "",
                "photo_request": {"needed": False, "what": "", "angle": ""},
                "safety_concern": False,
            }
        )
        self.responses = FakeResponses(self)
        self.files = FakeFiles(self)

    def set_verdict(self, **overrides):
        base = json.loads(self.next_output_text)
        base.update(overrides)
        self.next_output_text = json.dumps(base)


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()


# The real function, for the one test that checks its request; every other test gets the stand-in below.
from app import asr as _asr  # noqa: E402

REAL_TRANSCRIBE_TURNS = _asr.transcribe_turns


@pytest.fixture(autouse=True)
def speech_turns(monkeypatch) -> list[bytes]:
    """The digester asks the speech service when each sentence is said; no test reaches the network.
    Each request's WAV is recorded; a test that wants sentences patches asr.transcribe_turns itself."""
    from app import asr

    sent: list[bytes] = []

    async def no_turns(api_key, wav, timeout=180.0):
        sent.append(wav)
        return []

    monkeypatch.setattr(asr, "transcribe_turns", no_turns)
    return sent


def api_status_error(cls, status: int):
    """An openai SDK status error as the real client raises it."""
    import httpx

    response = httpx.Response(status, request=httpx.Request("POST", "https://api.meta.ai/v1/responses"))
    return cls(f"Error code: {status} - upstream detail", response=response, body=None)


@pytest.fixture
def jpeg_bytes() -> bytes:
    img = Image.new("RGB", (2000, 1500), (40, 44, 52))
    out = io.BytesIO()
    img.save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture
def app_client(fake_client, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app import main

    async def no_transcription(api_key, media, keywords=None):
        return ""

    monkeypatch.setattr(main.asr, "transcribe_clip", no_transcription)
    # Compiled skills written during tests go to a temp dir, not the repo.
    monkeypatch.setattr(main.skills, "directory", tmp_path)
    monkeypatch.setattr(main.skills, "compiled_dir", tmp_path / "compiled")
    # The store was loaded at import from the repo's skills dir, whose compiled/ may hold skills
    # authored on this Mac. Each test sees the seed skills only, and what it publishes goes away
    # with this dict, so a test passes the same alone or after others.
    monkeypatch.setattr(main.skills, "_skills", {k: v for k, v in main.skills._skills.items() if not v.compiled})
    main.app.dependency_overrides[main.meta_client] = lambda: fake_client
    main.sessions._sessions.clear()
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()
