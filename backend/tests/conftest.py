"""A fake Meta client. Records every request and returns canned responses.

Tests never touch the network. The fake mirrors the two SDK surfaces guide.py uses:
client.responses.create(...) (streamed and not) and client.files.create(...).
"""

from __future__ import annotations

import asyncio
import io
import json
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
            hit = self.parent.sam_hits is None or concept in self.parent.sam_hits
            return FakeStream([SAM_WIRE] if hit else [])
        fmt = (kwargs.get("text") or {}).get("format") or {}
        if fmt.get("name") == "CompiledSkill":
            return SimpleNamespace(output_text=self.parent.next_compiled)
        if kwargs.get("stream"):
            return FakeStream(self.parent.next_deltas, self.parent.next_ending)
        return SimpleNamespace(output_text=self.parent.next_output_text)


class FakeFiles:
    def __init__(self, parent: "FakeClient"):
        self.parent = parent

    async def create(self, **kwargs):
        self.parent.uploads.append(kwargs)
        return SimpleNamespace(id="file-fake-123")


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


class FakeClient:
    api_key = "LLM|0|fake-key"

    def __init__(self):
        self.calls: list[dict] = []
        self.uploads: list[dict] = []
        self.sam_hits: set[str] | None = None  # None: every concept hits
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
    main.app.dependency_overrides[main.meta_client] = lambda: fake_client
    main.sessions._sessions.clear()
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()
    for sid in [s.id for s in main.skills.list() if s.compiled]:
        main.skills._skills.pop(sid, None)
