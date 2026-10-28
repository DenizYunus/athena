"""Athena backend. FastAPI app with the marketplace, author, and session endpoints."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse

from . import asr, guide, sam
from .compiler import Capture, compile_skill
from .meta_client import get_client
from .schemas import (
    Highlight,
    Session,
    Skill,
    StartSessionRequest,
    TranscriptResponse,
    TurnResponse,
    VerdictRecord,
    VerifyResponse,
)
from .store import SessionStore, SkillStore

app = FastAPI(title="Athena", version="0.2.0")

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

    return {
        "ok": True,
        "skills": len(skills.list()),
        "server_has_key": bool(settings.api_key),
        "ffmpeg": asr.ffmpeg_available(),
    }


# ---------------------------------------------------------------- marketplace


@app.get("/skills", response_model=list[Skill])
def list_skills() -> list[Skill]:
    return skills.list()


@app.get("/skills/{skill_id}", response_model=Skill)
def get_skill(skill_id: str) -> Skill:
    return _skill_or_404(skill_id)


@app.get("/skills/{skill_id}/media/{name}")
def skill_media(skill_id: str, name: str) -> FileResponse:
    _skill_or_404(skill_id)
    path = skills.media_path(skill_id, name)
    if path is None:
        raise HTTPException(404, "no such media")
    return FileResponse(path, media_type="image/jpeg")


@app.delete("/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: str) -> None:
    skill = _skill_or_404(skill_id)
    if not skill.compiled:
        raise HTTPException(403, "seed skills cannot be deleted")
    skills.delete(skill_id)


# ---------------------------------------------------------------- author


async def _read_optional(upload: UploadFile | None) -> bytes | None:
    if upload is None:
        return None
    data = await upload.read()
    return data or None


@app.post("/author/transcribe", response_model=TranscriptResponse)
async def author_transcribe(
    clip: UploadFile = File(...),
    keywords: str = Form(""),
    client: Any = Depends(meta_client),
) -> TranscriptResponse:
    """Transcribe a dictated note (any audio or video file ffmpeg can read)."""
    data = await clip.read()
    if not data:
        raise HTTPException(422, "empty clip")
    words = [k.strip() for k in keywords.split(",") if k.strip()]
    try:
        text = await asr.transcribe_clip(client.api_key, data, words)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    return TranscriptResponse(transcript=text)


@app.post("/skills/compile", response_model=Skill, status_code=201)
async def compile_endpoint(
    author: str = Form("Anonymous"),
    title_hint: str = Form(""),
    notes: list[str] = Form(default=[]),
    photos: list[UploadFile] = File(default=[]),
    client: Any = Depends(meta_client),
) -> Skill:
    """Photos (in order) + one note per photo -> a published skill.

    notes[i] belongs to photos[i]; extra notes without a photo are passed as free text.
    """
    captures: list[Capture] = []
    photo_bytes = [await p.read() for p in photos]
    for i in range(max(len(photo_bytes), len(notes))):
        photo = photo_bytes[i] if i < len(photo_bytes) and photo_bytes[i] else None
        note = notes[i] if i < len(notes) else ""
        if photo is None and not note.strip():
            continue
        captures.append(Capture(photo=photo, note=note))
    if not captures:
        raise HTTPException(422, "send at least one photo or note")
    try:
        skill, media = await compile_skill(client, captures, author=author, title_hint=title_hint)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(502, str(exc)) from exc
    skills.save(skill, media)
    return skills.get(skill.id) or skill


# ---------------------------------------------------------------- sessions


@app.post("/sessions", response_model=Session, status_code=201)
def start_session(req: StartSessionRequest) -> Session:
    skill = _skill_or_404(req.skill_id)
    return sessions.create(skill, mode=req.mode)


@app.get("/sessions/{session_id}", response_model=Session)
def get_session(session_id: str) -> Session:
    return _session_or_404(session_id)


@app.post("/sessions/{session_id}/turn")
async def session_turn(
    session_id: str,
    transcript: str = Form(""),
    clip: UploadFile | None = File(default=None),
    photo: UploadFile | None = File(default=None),
    stream: bool = False,
    client: Any = Depends(meta_client),
):
    """Push-to-talk turn: what the user said plus what they showed.

    With a clip and no transcript, the clip's audio track is transcribed first
    (Voice Transcribe, biased to the skill vocabulary) so the guide gets the words
    as text and the app can show them in the user's bubble. An audio-only clip
    (m4a/wav, the glasses path) is transcribed and not forwarded as video.
    """
    session = _session_or_404(session_id)
    if session.completed:
        raise HTTPException(409, "session is complete")
    skill = _skill_or_404(session.skill_id)
    clip_bytes = await _read_optional(clip)
    photo_bytes = await _read_optional(photo)
    if not transcript.strip() and not clip_bytes and not photo_bytes:
        raise HTTPException(422, "send a transcript, a clip, or a photo")

    transcript = transcript.strip()
    # A glasses turn sends the question as an audio file (no video track) plus a photo from the
    # glasses camera: the audio is transcribed and the guide gets text and the photo, never an
    # audio-only file dressed up as a video.
    audio_only = bool(clip_bytes) and asr.is_audio_only(clip.content_type if clip else None, clip_bytes or b"")
    guide_clip = None if audio_only else clip_bytes

    async def words() -> str:
        """The transcript to use: as sent, else transcribed from the clip's audio track."""
        if transcript or not clip_bytes:
            return transcript
        try:
            return await asr.transcribe_clip(client.api_key, clip_bytes, skill.vocabulary)
        except RuntimeError:
            return ""  # the guide still gets the clip; the model can hear the audio track

    if stream:
        # Events, in order: transcribing (only when the clip's audio is being transcribed),
        # transcript, delta..., done. An error event replaces done if the guide call fails.
        async def sse() -> AsyncIterator[bytes]:
            def event(payload: dict) -> bytes:
                return f"data: {json.dumps(payload)}\n\n".encode()

            try:
                if not transcript and clip_bytes:
                    yield event({"transcribing": True})
                heard = await words()
                yield event({"transcript": heard})
                async for delta in guide.turn_stream(client, skill, session, heard, guide_clip, photo_bytes):
                    yield event({"delta": delta})
                yield event({"done": True, "current_step": session.current_step})
            except Exception as exc:  # the phone shows this in a bubble
                yield event({"error": str(exc)[:300]})

        return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    heard = await words()
    reply = await guide.turn(client, skill, session, heard, guide_clip, photo_bytes)
    return TurnResponse(reply=reply, transcript=heard, current_step=session.current_step, session_id=session.id)


@app.post("/sessions/{session_id}/verify", response_model=VerifyResponse)
async def session_verify(
    session_id: str,
    photo: UploadFile = File(...),
    highlight: bool = True,
    client: Any = Depends(meta_client),
) -> VerifyResponse:
    """Snapshot check. Advances on pass; safety gates only ever advance on pass.

    SAM 3.1 runs on the same photo in parallel so a fail or unclear verdict can
    point at the part in question.
    """
    session = _session_or_404(session_id)
    if session.completed:
        raise HTTPException(409, "session is complete")
    skill = _skill_or_404(session.skill_id)
    step = skill.step(session.current_step)
    photo_bytes = await photo.read()
    if not photo_bytes:
        raise HTTPException(422, "empty photo")

    async def _highlight() -> Highlight | None:
        if not highlight or not step.sam_concepts:
            return None
        try:
            return await sam.highlight(client, photo_bytes, step.sam_concepts)
        except Exception:
            return None

    verdict, found = await asyncio.gather(
        guide.verify(client, skill, session, photo_bytes, media_dir=skills.directory), _highlight()
    )
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
        highlight=found,
    )


@app.post("/sessions/{session_id}/highlight", response_model=Highlight | None)
async def session_highlight(
    session_id: str,
    photo: UploadFile = File(...),
    concept: str = Form(""),
    client: Any = Depends(meta_client),
) -> Highlight | None:
    """Point at a part: the current step's concepts, or one the user names."""
    session = _session_or_404(session_id)
    skill = _skill_or_404(session.skill_id)
    step = skill.step(session.current_step)
    photo_bytes = await photo.read()
    if not photo_bytes:
        raise HTTPException(422, "empty photo")
    concepts = [concept] if concept.strip() else step.sam_concepts
    return await sam.highlight(client, photo_bytes, concepts)


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


# ---------------------------------------------------------------- realtime voice


@app.websocket("/asr")
async def asr_socket(ws: WebSocket) -> None:
    """Live transcription relay.

    Client protocol: first a text frame {"type":"start","skill_id":..,"api_key":..?,"sample_rate":16000},
    then binary frames of 16-bit mono PCM, then {"type":"endStream"}. The server replies
    {"type":"ready"} and then forwards Meta's {"type":"transcript","transcript":..,"final":..} events.
    """
    await ws.accept()
    try:
        first = await asyncio.wait_for(ws.receive_text(), 10)
        start = json.loads(first)
    except (asyncio.TimeoutError, json.JSONDecodeError, WebSocketDisconnect):
        await ws.close(code=1008)
        return
    if start.get("type") != "start":
        await ws.send_text(json.dumps({"type": "error", "message": "first message must be type=start"}))
        await ws.close(code=1008)
        return
    try:
        client = get_client(start.get("api_key") or ws.headers.get("x-model-api-key"))
    except RuntimeError as exc:
        await ws.send_text(json.dumps({"type": "error", "message": str(exc)}))
        await ws.close(code=1008)
        return
    skill = skills.get(start.get("skill_id") or "")
    keywords = list(skill.vocabulary) if skill else []
    sample_rate = int(start.get("sample_rate") or 16000)
    try:
        await asr.relay(ws, client.api_key, keywords, sample_rate)
    except WebSocketDisconnect:
        return
    except Exception as exc:  # upstream failure: tell the phone, then close
        try:
            await ws.send_text(json.dumps({"type": "error", "message": f"ASR relay failed: {exc}"}))
        except Exception:
            pass
    try:
        await ws.close()
    except Exception:
        pass
