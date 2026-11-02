from __future__ import annotations

import asyncio
import io
import json

import httpx
import openai

from conftest import api_status_error


def _start(app_client, mode=None):
    body = {"skill_id": "fridge-start-relay"}
    if mode:
        body["mode"] = mode
    r = app_client.post("/sessions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_health_and_marketplace(app_client):
    assert app_client.get("/health").json()["ok"] is True
    skills = app_client.get("/skills").json()
    assert skills[0]["id"] == "fridge-start-relay"
    assert app_client.get("/skills/nope").status_code == 404


def test_session_defaults_to_push_to_talk(app_client):
    s = _start(app_client)
    assert s["mode"] == "ptt"
    assert s["current_step"] == 1


def test_turn_with_clip_returns_reply(app_client, fake_client):
    s = _start(app_client)
    r = app_client.post(
        f"/sessions/{s['id']}/turn",
        data={"transcript": "which one is the relay?"},
        files={"clip": ("clip.mp4", b"\x00" * 64, "video/mp4")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["reply"].startswith("Yes")
    assert r.json()["current_step"] == 1
    sent = fake_client.calls[-1]["input"][-1]["content"]
    assert [b["type"] for b in sent] == ["input_text", "input_video"]


def test_turn_streams_sse(app_client):
    s = _start(app_client)
    with app_client.stream(
        "POST", f"/sessions/{s['id']}/turn?stream=true", data={"transcript": "hi"}
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    deltas = "".join(e.get("delta", "") for e in events)
    assert deltas == "Yes, that's the relay. Grip the body."
    assert events[-1]["done"] is True


def test_turn_requires_some_input(app_client):
    s = _start(app_client)
    assert app_client.post(f"/sessions/{s['id']}/turn", data={"transcript": "  "}).status_code == 422


def test_verify_pass_advances(app_client, jpeg_bytes):
    s = _start(app_client)
    r = app_client.post(f"/sessions/{s['id']}/verify", files={"photo": ("p.jpg", jpeg_bytes, "image/jpeg")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"]["status"] == "pass"
    assert body["advanced"] is True
    assert body["current_step"] == 2
    assert app_client.get(f"/sessions/{s['id']}").json()["verdicts"][0]["step"] == 1


def test_verify_unclear_does_not_advance(app_client, fake_client, jpeg_bytes):
    fake_client.set_verdict(
        status="unclear",
        confidence=0.4,
        photo_request={"needed": True, "what": "plug and outlet together", "angle": "from the side, 30 cm"},
    )
    s = _start(app_client)
    body = app_client.post(
        f"/sessions/{s['id']}/verify", files={"photo": ("p.jpg", jpeg_bytes, "image/jpeg")}
    ).json()
    assert body["advanced"] is False
    assert body["current_step"] == 1
    assert body["verdict"]["photo_request"]["needed"] is True


def test_safety_gate_cannot_be_skipped(app_client):
    s = _start(app_client)
    assert app_client.post(f"/sessions/{s['id']}/skip").status_code == 403


def test_normal_step_can_be_skipped_and_session_completes(app_client, jpeg_bytes):
    s = _start(app_client)
    app_client.post(f"/sessions/{s['id']}/verify", files={"photo": ("p.jpg", jpeg_bytes, "image/jpeg")})
    for _ in range(5):
        r = app_client.post(f"/sessions/{s['id']}/skip")
        assert r.status_code == 200
    assert r.json()["current_step"] == 7
    last = app_client.post(f"/sessions/{s['id']}/verify", files={"photo": ("p.jpg", jpeg_bytes, "image/jpeg")}).json()
    assert last["completed"] is True
    assert app_client.post(f"/sessions/{s['id']}/skip").status_code == 409


def test_verify_reports_rejected_key_as_502_with_a_sentence(app_client, fake_client, jpeg_bytes):
    fake_client.fail_with = api_status_error(openai.AuthenticationError, 401)
    s = _start(app_client)
    r = app_client.post(f"/sessions/{s['id']}/verify?highlight=false", files={"photo": ("p.jpg", jpeg_bytes, "image/jpeg")})
    assert r.status_code == 502
    assert "API key" in r.json()["detail"]
    assert app_client.get(f"/sessions/{s['id']}").json()["current_step"] == 1


def test_verify_upstream_503_never_reads_as_missing_key(app_client, fake_client, jpeg_bytes):
    """App builds up to 0.4.0 show "No API key" for any error text containing 503."""
    fake_client.fail_with = api_status_error(openai.InternalServerError, 503)
    s = _start(app_client)
    r = app_client.post(f"/sessions/{s['id']}/verify?highlight=false", files={"photo": ("p.jpg", jpeg_bytes, "image/jpeg")})
    assert r.status_code == 502
    assert "503" not in r.json()["detail"]


def test_verify_timeout_is_504(app_client, fake_client, jpeg_bytes):
    fake_client.fail_with = openai.APITimeoutError(request=httpx.Request("POST", "https://api.meta.ai/v1/responses"))
    s = _start(app_client)
    r = app_client.post(f"/sessions/{s['id']}/verify?highlight=false", files={"photo": ("p.jpg", jpeg_bytes, "image/jpeg")})
    assert r.status_code == 504
    assert "too long" in r.json()["detail"]


def test_verify_rejects_a_file_that_is_not_an_image(app_client):
    s = _start(app_client)
    r = app_client.post(f"/sessions/{s['id']}/verify?highlight=false", files={"photo": ("p.jpg", b"not a jpeg", "image/jpeg")})
    assert r.status_code == 422
    assert "image" in r.json()["detail"]


def test_turn_stream_upstream_error_is_a_sentence(app_client, fake_client):
    fake_client.fail_with = api_status_error(openai.InternalServerError, 503)
    s = _start(app_client)
    r = app_client.post(f"/sessions/{s['id']}/turn?stream=true", data={"transcript": "hello"})
    events = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
    assert "error" in events[-1] and "503" not in events[-1]["error"]
    assert not any("done" in e for e in events)


def test_compile_reports_rejected_key_as_502(app_client, fake_client, jpeg_bytes):
    fake_client.fail_with = api_status_error(openai.AuthenticationError, 401)
    r = app_client.post(
        "/skills/compile",
        data={"notes": ["unplug it"], "author": "t"},
        files=[("photos", ("a.jpg", jpeg_bytes, "image/jpeg"))],
    )
    assert r.status_code == 502
    assert "API key" in r.json()["detail"]


async def test_overlapping_checks_on_the_gate_advance_once(app_client, fake_client, jpeg_bytes):
    """A double tap (or a retry while the first check still runs) must not carry the session past
    the step after the gate on the strength of two verdicts about the gate."""
    from app import main

    s = _start(app_client)  # fridge skill: step 1 is the safety gate
    fake_client.delay = 0.2
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

        async def check():
            return await client.post(
                f"/sessions/{s['id']}/verify",
                params={"highlight": "false"},
                files={"photo": ("p.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
            )

        first, second = await asyncio.gather(check(), check())
    bodies = [first.json(), second.json()]
    assert sorted(b["advanced"] for b in bodies) == [False, True]
    state = main.sessions.get(s["id"])
    assert state.current_step == 2
    assert [v.step for v in state.verdicts] == [1, 1]


def test_verify_refuses_a_photo_taken_for_a_step_the_session_left(app_client, fake_client, jpeg_bytes):
    """A check that finished after the app stopped waiting moved the session on; the retake for the
    old step must not be judged against the new one."""
    s = _start(app_client)
    photo = {"photo": ("p.jpg", jpeg_bytes, "image/jpeg")}
    assert app_client.post(f"/sessions/{s['id']}/verify?highlight=false", data={"step": "1"}, files=photo).json()["current_step"] == 2
    calls = len(fake_client.calls)
    r = app_client.post(f"/sessions/{s['id']}/verify?highlight=false", data={"step": "1"}, files=photo)
    assert r.status_code == 409
    assert "step 2" in r.json()["detail"]
    assert len(fake_client.calls) == calls  # nothing was sent to the verifier
    assert app_client.get(f"/sessions/{s['id']}").json()["current_step"] == 2


def test_verify_without_a_step_field_still_works(app_client, jpeg_bytes):
    """App builds up to 0.4.0 send only the photo."""
    s = _start(app_client)
    r = app_client.post(f"/sessions/{s['id']}/verify?highlight=false", files={"photo": ("p.jpg", jpeg_bytes, "image/jpeg")})
    assert r.status_code == 200 and r.json()["advanced"] is True


def test_verify_past_its_deadline_is_504_and_does_not_advance(app_client, fake_client, jpeg_bytes, monkeypatch):
    from app import main

    monkeypatch.setattr(main.settings, "check_deadline_s", 0.2)
    fake_client.delay = 1.0
    s = _start(app_client)
    r = app_client.post(f"/sessions/{s['id']}/verify?highlight=false", files={"photo": ("p.jpg", jpeg_bytes, "image/jpeg")})
    assert r.status_code == 504
    state = app_client.get(f"/sessions/{s['id']}").json()
    assert state["current_step"] == 1 and state["verdicts"] == []


def test_slow_highlight_does_not_hold_up_the_verdict(app_client, fake_client, jpeg_bytes, monkeypatch):
    from app import main

    monkeypatch.setattr(main.settings, "highlight_timeout_s", 0.1)
    fake_client.sam_delay = 2.0
    s = _start(app_client)
    r = app_client.post(f"/sessions/{s['id']}/verify", files={"photo": ("p.jpg", jpeg_bytes, "image/jpeg")})
    assert r.status_code == 200
    body = r.json()
    assert body["advanced"] is True and body["highlight"] is None


def test_verify_unusable_answer_is_a_sentence(app_client, fake_client, jpeg_bytes):
    fake_client.next_output_text = '{"status": "pass", "confid'
    s = _start(app_client)
    r = app_client.post(f"/sessions/{s['id']}/verify?highlight=false", files={"photo": ("p.jpg", jpeg_bytes, "image/jpeg")})
    assert r.status_code == 502
    assert r.json()["detail"].startswith("The checker") and "confid" not in r.json()["detail"]
