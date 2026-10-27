"""Athena backend. FastAPI app with the marketplace and session endpoints."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from . import guide
from .meta_client import get_client
from .schemas import (
    Session,
    Skill,
    StartSessionRequest,
    TurnResponse,
    VerdictRecord,
    VerifyResponse,
)
from .store import SessionStore, SkillStore

app = FastAPI(title="Athena", version="0.1.0")

skills = SkillStore()
sessions = SessionStore()


def meta_client(x_model_api_key: str | None = Header(default=None)) -> Any:
    """Dependency so tests can override it with a fake. The app may send its own key."""
    try:
        return get_client(x_model_api_key)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def _skill_or_404(skill_id: str) -> Skill:
    skill = skills.get(skill_id)
    if skill is None:
        raise HTTPException(404, f"unknown skill {skill_id}")
    return skill


def _session_or_404(session_id: str) -> Session:
    session = sessions.get(session_id)
    if session is None:
        raise HTTPException(404, f"unknown session {session_id}")
    return session


@app.get("/health")
def health() -> dict:
    from .config import settings

    return {"ok": True, "skills": len(skills.list()), "server_has_key": bool(settings.api_key)}


@app.get("/skills", response_model=list[Skill])
def list_skills() -> list[Skill]:
    return skills.list()


@app.get("/skills/{skill_id}", response_model=Skill)
def get_skill(skill_id: str) -> Skill:
    return _skill_or_404(skill_id)


@app.post("/sessions", response_model=Session, status_code=201)
def start_session(req: StartSessionRequest) -> Session:
    skill = _skill_or_404(req.skill_id)
    return sessions.create(skill, mode=req.mode)


@app.get("/sessions/{session_id}", response_model=Session)
def get_session(session_id: str) -> Session:
    return _session_or_404(session_id)


async def _read_optional(upload: UploadFile | None) -> bytes | None:
    if upload is None:
        return None
    data = await upload.read()
    return data or None


@app.post("/sessions/{session_id}/turn")
async def session_turn(
    session_id: str,
    transcript: str = Form(""),
    clip: UploadFile | None = File(default=None),
    photo: UploadFile | None = File(default=None),
    stream: bool = False,
    client: Any = Depends(meta_client),
):
    """Push-to-talk turn: what the user said plus what they showed."""
    session = _session_or_404(session_id)
    if session.completed:
        raise HTTPException(409, "session is complete")
    skill = _skill_or_404(session.skill_id)
    clip_bytes = await _read_optional(clip)
    photo_bytes = await _read_optional(photo)
    if not transcript.strip() and not clip_bytes and not photo_bytes:
        raise HTTPException(422, "send a transcript, a clip, or a photo")

    if stream:
        async def sse() -> AsyncIterator[bytes]:
            async for delta in guide.turn_stream(client, skill, session, transcript, clip_bytes, photo_bytes):
                yield f"data: {json.dumps({'delta': delta})}\n\n".encode()
            yield f"data: {json.dumps({'done': True, 'current_step': session.current_step})}\n\n".encode()

        return StreamingResponse(sse(), media_type="text/event-stream")

    reply = await guide.turn(client, skill, session, transcript, clip_bytes, photo_bytes)
    return TurnResponse(reply=reply, current_step=session.current_step, session_id=session.id)


@app.post("/sessions/{session_id}/verify", response_model=VerifyResponse)
async def session_verify(
    session_id: str,
    photo: UploadFile = File(...),
    client: Any = Depends(meta_client),
) -> VerifyResponse:
    """Snapshot check. Advances on pass; safety gates only ever advance on pass."""
    session = _session_or_404(session_id)
    if session.completed:
        raise HTTPException(409, "session is complete")
    skill = _skill_or_404(session.skill_id)
    photo_bytes = await photo.read()
    if not photo_bytes:
        raise HTTPException(422, "empty photo")

    verdict = await guide.verify(client, skill, session, photo_bytes)
    session.verdicts.append(VerdictRecord(step=session.current_step, verdict=verdict))

    advanced = False
    if verdict.status == "pass":
        advanced = True
        last = max(s.n for s in skill.steps)
        if session.current_step >= last:
            session.completed = True
        else:
            session.current_step += 1

    return VerifyResponse(
        verdict=verdict,
        advanced=advanced,
        current_step=session.current_step,
        completed=session.completed,
        session_id=session.id,
    )


@app.post("/sessions/{session_id}/skip", response_model=Session)
def session_skip(session_id: str) -> Session:
    """Move past a step without a verdict. Refused on safety gates."""
    session = _session_or_404(session_id)
    if session.completed:
        raise HTTPException(409, "session is complete")
    skill = _skill_or_404(session.skill_id)
    step = skill.step(session.current_step)
    if step.safety_gate:
        raise HTTPException(403, "safety gates cannot be skipped")
    last = max(s.n for s in skill.steps)
    if session.current_step >= last:
        session.completed = True
    else:
        session.current_step += 1
    return session
