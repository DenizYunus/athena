from __future__ import annotations

import json


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
