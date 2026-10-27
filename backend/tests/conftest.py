"""A fake Meta client. Records every request and returns canned responses.

Tests never touch the network. The fake mirrors the two SDK surfaces guide.py uses:
client.responses.create(...) (streamed and not) and client.files.create(...).
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image


class FakeStream:
    def __init__(self, deltas: list[str]):
        self._deltas = deltas

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        yield SimpleNamespace(type="response.created")
        for d in self._deltas:
            yield SimpleNamespace(type="response.output_text.delta", delta=d)
        yield SimpleNamespace(type="response.completed")


class FakeResponses:
    def __init__(self, parent: "FakeClient"):
        self.parent = parent

    async def create(self, **kwargs):
        self.parent.calls.append(kwargs)
        if kwargs.get("stream"):
            return FakeStream(self.parent.next_deltas)
        return SimpleNamespace(output_text=self.parent.next_output_text)


class FakeFiles:
    def __init__(self, parent: "FakeClient"):
        self.parent = parent

    async def create(self, **kwargs):
        self.parent.uploads.append(kwargs)
        return SimpleNamespace(id="file-fake-123")


class FakeClient:
    def __init__(self):
        self.calls: list[dict] = []
        self.uploads: list[dict] = []
        self.next_deltas = ["Yes, ", "that's the relay. ", "Grip the body."]
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


@pytest.fixture
def jpeg_bytes() -> bytes:
    img = Image.new("RGB", (2000, 1500), (40, 44, 52))
    out = io.BytesIO()
    img.save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture
def app_client(fake_client):
    from fastapi.testclient import TestClient

    from app.main import app, meta_client, sessions

    app.dependency_overrides[meta_client] = lambda: fake_client
    sessions._sessions.clear()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
