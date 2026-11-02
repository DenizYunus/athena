"""Athena backend. FastAPI app with the marketplace, author, and session endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import openai
import pydantic
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from PIL import UnidentifiedImageError

from . import asr, guide, sam
from .compiler import Capture, compile_skill
from .config import settings
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

log = logging.getLogger("athena")

app = FastAPI(title="Athena", version="0.2.0")

skills = SkillStore()
sessions = SessionStore()


def meta_client(x_model_api_key: str | None = Header(default=None)) -> Any:
    """Dependency so tests can override it with a fake. The app may send its own key."""
    try:
        return get_client(x_model_api_key)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def _failure(exc: Exception) -> tuple[int, str]:
    """The status and the sentence the app shows when a Meta call or an upload fails.

    The sentence never carries an upstream status code: app builds up to 0.4.0 read any "503" in an
    error as "no API key". The full exception goes to the log, which the recording plan uses as a monitor.
    """
    log.warning("request failed: %s: %s", type(exc).__name__, exc)
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return 502, "The Meta API rejected the API key. Check it in Settings."
    if isinstance(exc, openai.RateLimitError):
        return 502, "The Meta API is rate limiting requests. Wait a few seconds and try again."
    if isinstance(exc, (openai.APITimeoutError, httpx.TimeoutException, TimeoutError)):
        return 504, "The Meta API took too long to answer. Try again."
    if isinstance(exc, (openai.APIConnectionError, httpx.TransportError)):
        return 502, "The backend could not reach the Meta API. Check the Mac's internet connection."
    if isinstance(exc, openai.BadRequestError):
        return 502, "The Meta API refused this request. Try again with a new photo or clip."
    if isinstance(exc, openai.OpenAIError):
        return 502, "The Meta API had a problem answering. Try again in a moment."
    if isinstance(exc, UnidentifiedImageError):
        return 422, "The photo could not be read as an image. Take it again."
    if isinstance(exc, pydantic.ValidationError):
        return 502, "The answer from the Meta API did not have the expected shape. Try again."
    if isinstance(exc, (RuntimeError, ValueError)):
        return 502, str(exc)[:300]
    return 500, f"The backend failed: {type(exc).__name__}. The backend terminal has the details."


def _http_failure(exc: Exception) -> HTTPException:
    status, message = _failure(exc)
    return HTTPException(status, message)


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
    except Exception as exc:
        raise _http_failure(exc) from exc
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
        """The transcript to use: as sent, else transcribed from the clip's audio track.

        A video clip survives a failed transcription (the guide still gets the clip). An audio-only
        clip does not reach the guide at all, so a failed transcription fails the turn instead of
        silently dropping the question."""
        if transcript or not clip_bytes:
            return transcript
        try:
            return await asr.transcribe_clip(client.api_key, clip_bytes, skill.vocabulary)
        except RuntimeError as exc:
            if audio_only:
                log.warning("transcription of an audio-only clip failed: %s", exc)
                # Say what to do, without the upstream status code (the app reads a bare 503 as "no API key").
                reason = str(exc)
                if isinstance(exc, asr.TranscribeError):
                    if exc.status in (401, 403):
                        reason = "the speech service rejected the API key; check it in Settings"
                    elif exc.status in (400, 413, 415, 422):
                        reason = "the recording could not be used"
                    else:
                        reason = "the speech service did not answer"
                raise RuntimeError(f"Could not transcribe your question ({reason}). Try again.") from exc
            return ""

    def nothing_to_answer(heard: str) -> bool:
        """An audio-only clip with no words and no photo leaves the guide nothing at all."""
        return audio_only and not heard and not photo_bytes

    NOTHING = "Didn't catch your question: no words were heard and no photo was sent. Try again."

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
                if nothing_to_answer(heard):
                    raise RuntimeError(NOTHING)
                yield event({"transcript": heard})
                async for delta in guide.turn_stream(client, skill, session, heard, guide_clip, photo_bytes):
                    yield event({"delta": delta})
                yield event({"done": True, "current_step": session.current_step})
            except Exception as exc:  # the phone shows this in a bubble
                yield event({"error": _failure(exc)[1]})

        return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    try:
        heard = await words()
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    if nothing_to_answer(heard):
        raise HTTPException(422, NOTHING)
    try:
        reply = await guide.turn(client, skill, session, heard, guide_clip, photo_bytes)
    except Exception as exc:
        raise _http_failure(exc) from exc
    return TurnResponse(reply=reply, transcript=heard, current_step=session.current_step, session_id=session.id)


@app.post("/sessions/{session_id}/verify", response_model=VerifyResponse)
async def session_verify(
    session_id: str,
    photo: UploadFile = File(...),
    step_n: int | None = Form(default=None, alias="step"),
    highlight: bool = True,
    client: Any = Depends(meta_client),
) -> VerifyResponse:
    """Snapshot check. Advances on pass; safety gates only ever advance on pass.

    SAM 3.1 runs on the same photo in parallel so a fail or unclear verdict can
    point at the part in question. The app sends the step on its screen as `step`
    (builds up to 0.4.0 do not; then the session's current step is checked).
    """
    session = _session_or_404(session_id)
    if session.completed:
        raise HTTPException(409, "session is complete")
    skill = _skill_or_404(session.skill_id)
    if step_n is not None and step_n != session.current_step:
        # The photo was taken for a step the session has already left (a check that finished after
        # the app stopped waiting). Judging it against the current step would credit the wrong step.
        raise HTTPException(409, f"This photo was for step {step_n}, but the session is on step {session.current_step}.")
    step = skill.step(session.current_step)
    photo_bytes = await photo.read()
    if not photo_bytes:
        raise HTTPException(422, "empty photo")

    async def _highlight() -> Highlight | None:
        if not highlight or not step.sam_concepts:
            return None
        try:
            return await asyncio.wait_for(sam.highlight(client, photo_bytes, step.sam_concepts), settings.highlight_timeout_s)
        except Exception:
            return None

    try:
        verdict, found = await asyncio.wait_for(
            asyncio.gather(guide.verify(client, skill, session, photo_bytes, media_dir=skills.directory, step=step), _highlight()),
            settings.check_deadline_s,
        )
    except Exception as exc:
        raise _http_failure(exc) from exc
    # The verdict belongs to the step that was judged. Another check on this session (a double tap,
    # or a retry after the app stopped waiting) may have moved it on during the model call; only a
    # pass for the step the session is still on advances it, so a gate is never passed by a verdict
    # about the step before it.
    session.verdicts.append(VerdictRecord(step=step.n, verdict=verdict))

    advanced = False
    if verdict.status == "pass" and not session.completed and session.current_step == step.n:
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
    try:
        return await asyncio.wait_for(sam.highlight(client, photo_bytes, concepts), settings.check_deadline_s)
    except Exception as exc:
        raise _http_failure(exc) from exc


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
