"""Athena backend. FastAPI app with the marketplace, author, and session endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import openai
import pydantic
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from PIL import UnidentifiedImageError

from . import asr, digest, guide, sam
from .compiler import Capture, compile_skill
from .config import settings
from .meta_client import get_client
from .schemas import (
    DigestJob,
    Highlight,
    Session,
    Skill,
    StartSessionRequest,
    TranscriptResponse,
    TurnResponse,
    VerdictRecord,
    VerifyResponse,
)
from .store import DigestJobStore, SessionStore, SkillStore

log = logging.getLogger("athena")

app = FastAPI(title="Athena", version="0.2.0")

skills = SkillStore()
sessions = SessionStore()
digest_jobs = DigestJobStore()
# The event loop keeps only a weak reference to a task; a digest running in the background must not
# be garbage-collected halfway, so each one stays here until it finishes.
_digest_tasks: set[asyncio.Task] = set()


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
        return 502, "The backend could not reach the Meta API. Check the backend's internet connection."
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
    return 500, f"The backend failed: {type(exc).__name__}. The backend log has the details."


def _digest_failure(exc: Exception, with_documents: bool) -> str:
    """The sentence for a failed digest job. _failure's sentence for a refused request asks for a new
    photo or clip, which a digest never sends: a 400 here is about the video or the manuals."""
    if isinstance(exc, openai.BadRequestError):
        log.warning("digest refused: %s", exc)
        if with_documents:
            return "The Meta API refused the video or the manual. Try again without the manual, or with a shorter video."
        return "The Meta API refused the video. Try a shorter video or another recording."
    return _failure(exc)[1]


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


MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
}


@app.get("/skills/{skill_id}/media/{name}")
def skill_media(skill_id: str, name: str) -> FileResponse:
    """Reference photos and expert clips. FileResponse answers Range requests, which video players use."""
    _skill_or_404(skill_id)
    path = skills.media_path(skill_id, name)
    if path is None:
        raise HTTPException(404, "no such media")
    return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"))


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


VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".3gp", ".avi")
COPY_CHUNK_BYTES = 1024 * 1024
# PDF readers find the %PDF- header anywhere in a file's first 1024 bytes; some generators and mail
# gateways put a byte-order mark or a blank line before it.
PDF_HEADER_WINDOW = 1024


def _size_text(n_bytes: int) -> str:
    if n_bytes >= 1024**3 and n_bytes % 1024**3 == 0:
        return f"{n_bytes // 1024**3} GB"
    if n_bytes >= 1024**2:
        return f"{n_bytes / 1024**2:.0f} MB"
    return f"{max(1, round(n_bytes / 1024))} KB"


def _minutes_text(seconds: float) -> str:
    minutes = seconds / 60
    return f"{minutes:.0f} minutes" if minutes == int(minutes) else f"{minutes:.1f} minutes"


def _copy_upload(upload: UploadFile, target: Path, limit: int) -> int | None:
    """Copy an upload to disk in chunks; the bytes written, or None once it passes `limit`.

    Starlette has already spooled the body to a temporary file; reading it in chunks keeps a
    1 GiB video out of memory. Runs in a worker thread so the event loop keeps serving polls."""
    upload.file.seek(0)
    total = 0
    with target.open("wb") as out:
        while chunk := upload.file.read(COPY_CHUNK_BYTES):
            total += len(chunk)
            if total > limit:
                return None
            out.write(chunk)
    return total


def _is_video(upload: UploadFile) -> bool:
    name = (upload.filename or "").lower()
    return (upload.content_type or "").lower().startswith("video/") or name.endswith(VIDEO_SUFFIXES)


def _display_name(upload: UploadFile, fallback: str) -> str:
    """The file name as the author knows it, without any folder part a client may send.

    React Native percent-encodes multipart file names ("Brake%20pad%20manual.pdf"). Decoding comes
    first, so the cut never splits an escape and a decoded %2F loses its folder part too."""
    name = unquote(upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name[:120] or fallback


def _drop_head(path: Path, n_bytes: int) -> None:
    """Rewrite a file without its first n_bytes, streamed (a manual can be 50 MB)."""
    tmp = path.with_name(f".{path.name}.tmp")
    with path.open("rb") as src, tmp.open("wb") as out:
        src.seek(n_bytes)
        shutil.copyfileobj(src, out, COPY_CHUNK_BYTES)
    os.replace(tmp, path)


async def _save_documents(documents: list[UploadFile], workdir: Path) -> list[digest.Document]:
    if len(documents) > settings.digest_max_documents:
        raise HTTPException(422, f"Add at most {settings.digest_max_documents} documents.")
    saved: list[digest.Document] = []
    for i, upload in enumerate(documents, start=1):
        name = _display_name(upload, f"document {i}.pdf")
        is_pdf_type = (upload.content_type or "").lower() == "application/pdf" or name.lower().endswith(".pdf")
        if not is_pdf_type:
            raise HTTPException(422, f"{name} is not a PDF. Add the manual as a PDF file.")
        limit = settings.digest_max_document_bytes
        if upload.size is not None and upload.size > limit:
            raise HTTPException(422, f"{name} is larger than {_size_text(limit)}. Add a smaller PDF.")
        path = workdir / f"document_{i}.pdf"
        written = await asyncio.to_thread(_copy_upload, upload, path, limit)
        if written is None:
            raise HTTPException(422, f"{name} is larger than {_size_text(limit)}. Add a smaller PDF.")
        with path.open("rb") as f:
            header_at = f.read(PDF_HEADER_WINDOW).find(b"%PDF-")
        if header_at < 0:
            raise HTTPException(422, f"{name} is not a PDF. Add the manual as a PDF file.")
        if header_at > 0:
            # Meta gets a file that starts with the header; the PDF's own offsets count from there.
            await asyncio.to_thread(_drop_head, path, header_at)
        saved.append(digest.Document(name=name, path=path))
    return saved


async def _save_video(video: UploadFile, workdir: Path) -> tuple[Path, digest.VideoInfo]:
    """The video on disk and what ffprobe says about it, or the HTTP error that tells the author what to fix."""
    limit = settings.digest_max_upload_bytes
    too_large = HTTPException(413, f"The video is larger than {_size_text(limit)}. Trim it or record a shorter one.")
    if video.size is not None and video.size > limit:
        raise too_large
    suffix = Path(video.filename or "").suffix.lower()
    path = workdir / f"upload{suffix if suffix in VIDEO_SUFFIXES else '.bin'}"
    written = await asyncio.to_thread(_copy_upload, video, path, limit)
    if written is None:
        raise too_large
    if written == 0:
        raise HTTPException(422, "The video file is empty. Choose another video.")
    try:
        path, info = await digest.probe_upload(path)
    except digest.VideoError as exc:
        raise HTTPException(422, str(exc)) from exc
    except digest.BackendTimeout as exc:  # a busy Mac, not a bad file
        raise HTTPException(504, str(exc)) from exc
    except RuntimeError as exc:  # ffprobe is missing on this Mac
        raise HTTPException(500, str(exc)) from exc
    if info.duration > settings.digest_max_video_s:
        raise HTTPException(
            422,
            f"The video is longer than {_minutes_text(settings.digest_max_video_s)}. Trim it and try again.",
        )
    return path, info


def _room_for_a_digest() -> None:
    """Refuse a new digest while the backend runs as many as it allows, or its disk is nearly full.
    Neither status is 503, which app builds up to 0.4.0 read as "no API key"."""
    if digest_jobs.running() >= settings.digest_max_running:
        raise HTTPException(429, "The backend is already digesting as many videos as it can. Try again in a few minutes.")
    if shutil.disk_usage(tempfile.gettempdir()).free < settings.digest_min_free_bytes:
        raise HTTPException(507, "The backend's disk is nearly full. Free some space where it runs, then try again.")


@app.post("/skills/digest", response_model=DigestJob, status_code=202)
async def digest_endpoint(
    video: UploadFile | None = File(default=None),
    video_url: str = Form(""),
    documents: list[UploadFile] = File(default=[]),
    author: str = Form("Anonymous"),
    title_hint: str = Form(""),
    client: Any = Depends(meta_client),
) -> DigestJob:
    """A video of the job, uploaded or as a YouTube link (and up to three PDFs of the written
    procedure) -> a digest job.

    An upload is checked here, so a wrong file fails at once with a sentence. A link is only checked
    for its shape: the video behind it is downloaded and checked by the job, whose failure the app
    shows the same way. The digest itself runs in the background for minutes, and the app polls
    GET /skills/digest/{job_id}."""
    _room_for_a_digest()
    has_file = video is not None and bool(video.filename or video.size)
    link = video_url.strip()
    source: digest.YouTubeSource | None = None
    if has_file and link:
        raise HTTPException(422, "Choose a video file or paste a YouTube link, not both.")
    if link:
        url = digest.youtube_link(link)
        if url is None:
            raise HTTPException(422, "Paste a link to one YouTube video (youtube.com or youtu.be).")
        source = digest.YouTubeSource(url=url)
    elif not has_file:
        raise HTTPException(422, "Choose a video to upload, or paste a YouTube link.")
    elif not _is_video(video):
        raise HTTPException(422, "The file is not a video. Choose an MP4, MOV or WebM file.")
    workdir = Path(tempfile.mkdtemp(prefix="athena-digest-"))
    try:
        docs = await _save_documents(documents, workdir)
        path, info = (None, None) if source else await _save_video(video, workdir)
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise

    try:
        # Again: another digest may have started while this request saved its files.
        _room_for_a_digest()
    except HTTPException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    job = digest_jobs.create(video_seconds=round(info.duration, 2) if info else 0, documents=[d.name for d in docs])
    inp = digest.DigestInput(
        workdir=workdir,
        video=path,
        documents=docs,
        author=author,
        title_hint=title_hint,
        hdr=info.hdr if info else False,
        source=source,
    )

    def publish(skill: Skill, media: dict) -> Skill:
        skills.save(skill, media)
        return skills.get(skill.id) or skill

    task = asyncio.create_task(digest.run_job(job, client, inp, publish, lambda exc: _digest_failure(exc, bool(docs))))
    _digest_tasks.add(task)
    task.add_done_callback(_digest_tasks.discard)
    # A copy: the answer says "queued" even if the task gets to run before the response is written.
    return job.model_copy(deep=True)


@app.get("/skills/digest/{job_id}", response_model=DigestJob)
async def get_digest(job_id: str) -> DigestJob:
    """Async on purpose: it runs on the event loop with the digest task, so a poll never sees a job
    halfway through an update (a sync route would read it from a worker thread)."""
    job = digest_jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown digest job")
    return job


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
