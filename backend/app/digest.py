"""The video digester: an expert's how-to video (and written procedure, if any) -> a published Skill.

The same Skill the photo compiler writes, plus where in the video each step happens, a reference
photo per step taken from the frame that best shows it done, a short clip of the expert doing the
step, the document's specs per step, and the points where video and document disagree.

Stages, as the app's progress line shows them:
  preparing  the upload becomes a small H.264 MP4, cut into parts of at most DIGEST_CHUNK_S seconds.
             Muse Spark samples a fixed frame budget (about 1 fps up to roughly 5 minutes); a longer
             video is subsampled and its timestamps come back compressed, so no call sees more.
  uploading  every part and every PDF go to the Files API. All are deleted when the reading ends.
  reading    one part: one call watches the video, reads the PDFs and writes the skill.
             Several parts: one call per part lists the steps seen in it (video only), then
  merging    one text call turns those steps (with absolute times) and the PDFs into the skill.
  frames     per step: the sharpest frame around the model's best second, grounded with SAM 3.1,
             becomes the reference photo; a clip of the step is cut from the video.
  saving     published like a compiled skill.

ffmpeg and ffprobe run as asyncio subprocesses. The Meta client is a parameter so tests pass a fake.
"""

from __future__ import annotations

import asyncio
import copy
import io
import json
import logging
import math
import secrets
import shutil
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from PIL import Image, ImageFilter, ImageStat

from . import sam
from .compiler import COMPILED_SKILL_JSON_SCHEMA, slugify
from .config import settings
from .media import prepare_image
from .schemas import Conflict, DigestInfo, DigestJob, DigestStage, Skill, Step

log = logging.getLogger("athena")

T = TypeVar("T")

MAX_STEPS = 30
SOURCES = ("video", "document", "both", "added")
# ffmpeg returns no frame for a seek at or right after the last one; frames are taken this far before the end.
FRAME_END_MARGIN_S = 0.25
# Candidate frames around the model's best second, and the later points of the step tried when SAM misses.
FRAME_NUDGE_S = 0.7
FALLBACK_FRACTIONS = (0.6, 0.85)
MIN_CLIP_S = 2.0
# Steps whose frames and clips are made at once: each runs a few ffmpeg processes and SAM calls.
PARALLEL_STEPS = 4

# Long side at most 1280 px, never upscaled, both sides even (H.264 in yuv420p needs that). The
# scale filter sees the frame after ffmpeg's autorotate, so a portrait phone video stays portrait.
NORMALIZE_SCALE = (
    "scale=w='if(gte(iw,ih),trunc(min(1280,iw)/2)*2,-2)'"
    ":h='if(gte(iw,ih),-2,trunc(min(1280,ih)/2)*2)'"
)
# Short side at most 480 px: the clips play in a small overlay on the phone.
CLIP_SCALE = (
    "scale=w='if(gte(iw,ih),-2,trunc(min(480,iw)/2)*2)'"
    ":h='if(gte(iw,ih),trunc(min(480,ih)/2)*2,-2)'"
)
# HDR video (HLG, an iPhone's default since the iPhone 12; PQ, Android's HDR10) has to be tone-mapped to
# SDR BT.709, or its code values are read as SDR and every reference photo and clip comes out dim and
# grey. ffmpeg 8's own scale filter does it; it runs after the downscale, where it costs least.
HDR_TRANSFERS = ("arib-std-b67", "smpte2084")
TONE_MAP = "scale=out_transfer=bt709:out_primaries=bt709:out_color_matrix=bt709:out_range=tv"
# ffprobe reads only the container's headers; past this something is wrong with the Mac, not the file.
PROBE_TIMEOUT_S = 60.0


# ---------------------------------------------------------------- prompts and schemas


DIGEST_SYSTEM_PROMPT = """You are the Athena skill compiler. Turn a hands-on how-to video, and a written procedure when one is given, into a skill a novice follows with a phone camera. Every step must be checkable from one photo.

Rules:
- Follow the order the expert works in and keep their wording where it is clear. What the expert says aloud counts as much as what they do. Fill gaps a novice would hit (which screw, which direction, what to hold).
- Each step must be checkable from one photo: write expected_state as what must be visible, concrete and physical.
- Give the second in the video where each step starts (start_s) and ends (end_s), and the second whose frame best shows it done (frame_s): the result in plain view, hands out of the way where possible. Steps only in the document get start_s, end_s and frame_s of -1.
- Mark safety gates: power locked out or unplugged, gas or water valves closed, pressure or fluid drained, vehicle secured. Mark a step as a safety gate only when it removes a hazard. Gates come first. gate_requirement states the photo evidence, e.g. "the plug is out of the outlet and both are in the same frame".
- source is video (only the video shows it), document (only the document has it), both, or added. Use added only for a hazard-removal gate that neither the video nor the document covers but the task clearly needs (power, gas, pressure, hot fluid, a raised vehicle).
- document_note carries the document's specs and cautions for the step (torque, sizes, thread direction, limits) in one or two sentences, empty when none.
- When the video and the document disagree, list the conflict in conflicts (topic, what the video says, what the document says, and the resolution: what the skill uses) and prefer the document for specs and safety. conflicts is empty when they agree or when there is no document.
- sam_concepts are for a segmentation model that only understands plain visual language. Write what a stranger would call the thing: "black plastic box", "copper pipe", "white wire", "power cord", "screwdriver". Give two to four alternatives per step, most specific first. Never a bare technical name like "start relay" or "capacitor" on its own.
- vocabulary lists the technical names and brand terms a learner might say aloud (parts, tools, model names), for speech recognition.
- common_mistakes: real ones, one short phrase each. Leave empty if none.
- Difficulty 1 is "anyone with hands", 5 is "trained technician".
- At most 30 steps.
- Write for the ear: instructions are read aloud by the phone. No markdown."""


CHUNK_SYSTEM_PROMPT = """You are watching one part of an expert's hands-on how-to video. The parts are read separately and combined into a skill afterwards, so report only what this part shows and what the expert says in it.

List every step the expert does in this part, in order:
- action: what they do, in one or two sentences, with the specifics a novice needs (which screw, which direction, what to hold).
- visible_result: what can be seen once the step is done.
- spoken_details: facts the expert says aloud about the step (sizes, torque, warnings). Empty when none.
- safety: hazards the step handles or mentions (power, gas, water, pressure, hot fluid, a raised vehicle). Empty when none.
- start_s and end_s: when the step starts and ends. frame_s: the second whose frame best shows it done. All three are whole seconds from the start of THIS PART, not of the whole video.
- tools and parts: the ones seen or named in the step.
A step already under way when the part begins, or still going when it ends, is listed too.
summary: one or two sentences on what this part covers. No markdown."""


def _digested_skill_schema() -> dict:
    """The photo compiler's schema with video times, sources and document notes per step, and conflicts."""
    schema = copy.deepcopy(COMPILED_SKILL_JSON_SCHEMA)
    step = schema["properties"]["steps"]["items"]
    del step["properties"]["reference_photo"]
    step["properties"].update(
        {
            "start_s": {"type": "integer", "description": "Second in the video where the step starts; -1 when the video does not show it."},
            "end_s": {"type": "integer", "description": "Second in the video where the step ends; -1 when the video does not show it."},
            "frame_s": {"type": "integer", "description": "Second whose frame best shows the step done; -1 when the video does not show it."},
            "source": {"type": "string", "enum": list(SOURCES)},
            "document_note": {"type": "string", "description": "The document's specs and cautions for this step; empty when none."},
        }
    )
    step["required"] = [r for r in step["required"] if r != "reference_photo"] + [
        "start_s", "end_s", "frame_s", "source", "document_note",
    ]
    schema["properties"]["conflicts"] = {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "topic": {"type": "string"},
                "video_says": {"type": "string"},
                "document_says": {"type": "string"},
                "resolution": {"type": "string", "description": "What the skill uses, and why."},
            },
            "required": ["topic", "video_says", "document_says", "resolution"],
        },
    }
    schema["required"] = [*schema["required"], "conflicts"]
    return schema


DIGESTED_SKILL_SCHEMA: dict = _digested_skill_schema()

CHUNK_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {"type": "string"},
                    "visible_result": {"type": "string"},
                    "spoken_details": {"type": "string"},
                    "safety": {"type": "string"},
                    "start_s": {"type": "integer", "description": "Seconds from the start of this part."},
                    "end_s": {"type": "integer", "description": "Seconds from the start of this part."},
                    "frame_s": {"type": "integer", "description": "Seconds from the start of this part."},
                    "tools": {"type": "array", "items": {"type": "string"}},
                    "parts": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "action", "visible_result", "spoken_details", "safety", "start_s", "end_s", "frame_s", "tools", "parts",
                ],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["steps", "summary"],
}


# ---------------------------------------------------------------- inputs and progress


class VideoError(ValueError):
    """The upload is not a video the digester can use. The message is the sentence the app shows."""


class UnknownLength(VideoError):
    """The container does not say how long the video is (a stream-written WebM or Matroska file)."""


class BackendTimeout(RuntimeError):
    """ffmpeg or ffprobe ran past its time limit. The message is the sentence the app shows.

    Not a TimeoutError: the API layer reads that as the Meta API being slow, and this step ran on the Mac."""


@dataclass
class Document:
    name: str  # as uploaded: shown to Muse Spark and on the skill
    path: Path


@dataclass
class DigestInput:
    workdir: Path  # owned by the pipeline from here on, removed when it ends
    video: Path
    documents: list[Document] = field(default_factory=list)
    author: str = "Anonymous"
    title_hint: str = ""
    hdr: bool = False  # the upload is HLG or PQ; normalize tone-maps it


@dataclass
class VideoInfo:
    duration: float  # the container's length, the longest stream: narration after the last frame counts
    has_audio: bool
    # Where the picture stops, at most `duration`. Earlier when the sound runs on past the last cut;
    # frames and clips are only taken before it.
    picture_end: float
    hdr: bool = False


def _stage(job: DigestJob, stage: DigestStage, progress: float, message: str) -> None:
    job.stage = stage
    job.progress = round(min(1.0, max(job.progress, progress)), 3)
    job.message = message


def _clock(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------- ffmpeg


async def _run(args: list[str], timeout: float, what: str) -> bytes:
    """Run ffmpeg or ffprobe; stdout on success. ffmpeg's own error text goes to the log only."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"The backend cannot {what} because {Path(args[0]).name} is not installed. Install ffmpeg on the Mac.") from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException as exc:
        # A timeout or a cancelled job must not leave ffmpeg running.
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        if isinstance(exc, TimeoutError):
            raise BackendTimeout(f"The backend took too long to {what}. Try again, or use a shorter video.") from exc
        raise
    if proc.returncode != 0:
        log.warning("%s failed (%s): %s", Path(args[0]).name, proc.returncode, err.decode(errors="replace")[-600:])
        raise RuntimeError(f"The backend could not {what}. Try another video file.")
    return out


def _seconds(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):  # ffprobe writes "N/A" when it does not know
        return None
    return number if math.isfinite(number) else None


def _not_the_files_fault(exc: Exception) -> bool:
    """ffmpeg missing on the Mac, or a step that ran too long: the app must not blame the file."""
    return isinstance(exc, BackendTimeout) or "not installed" in str(exc)


async def probe(path: Path) -> VideoInfo:
    """Length, picture and audio of a video. Raises VideoError when it is not a usable video."""
    try:
        out = await _run(
            [
                settings.ffprobe_bin, "-v", "error",
                "-show_entries",
                "format=duration:stream=codec_type,duration,color_transfer:stream_disposition=attached_pic",
                "-of", "json", str(path),
            ],
            timeout=PROBE_TIMEOUT_S,
            what="read the video",
        )
        info = json.loads(out or b"{}")
    except (RuntimeError, json.JSONDecodeError) as exc:
        if _not_the_files_fault(exc):
            raise
        raise VideoError("The file could not be read as a video. Choose an MP4, MOV or WebM file.") from exc
    streams = info.get("streams") or []
    # Cover art in an audio file shows up as a video stream marked attached_pic.
    video = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    if not video:
        raise VideoError("The file has no picture. Choose a video of the job.")
    duration = _seconds((info.get("format") or {}).get("duration"))
    if duration is None:
        durations = [d for s in streams if (d := _seconds(s.get("duration"))) is not None]
        duration = max(durations, default=None)
    if duration is None:
        raise UnknownLength("The video's length could not be read. Save it as an MP4 and try again.")
    if duration <= 0:
        raise VideoError("The video has no length. Choose another video.")
    picture = video[0]  # the stream normalize maps
    picture_end = _seconds(picture.get("duration"))
    return VideoInfo(
        duration=duration,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        picture_end=min(picture_end, duration) if picture_end and picture_end > 0 else duration,
        hdr=picture.get("color_transfer") in HDR_TRANSFERS,
    )


async def probe_upload(path: Path) -> tuple[Path, VideoInfo]:
    """probe() for the author's upload, and the file to digest from here on.

    A WebM or Matroska file written as a stream (a browser's MediaRecorder, a live recording) has no
    length in its header. One remux into Matroska, which copies every codec without re-encoding,
    writes it; the file is refused only if that does not help."""
    try:
        return path, await probe(path)
    except UnknownLength as unknown:
        remuxed = path.with_name("remuxed.mkv")
        try:
            await _run(
                [
                    settings.ffmpeg_bin, "-y", "-loglevel", "error", "-i", str(path),
                    "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", str(remuxed),
                ],
                timeout=300,
                what="read the video",
            )
        except RuntimeError as exc:
            if _not_the_files_fault(exc):
                raise
            raise unknown from exc
        info = await probe(remuxed)
    # The upload can be up to 1 GiB; the remux replaces it.
    path.unlink(missing_ok=True)
    return remuxed, info


def _encode_args(vf: str | None, crf: int) -> list[str]:
    # The audio map is optional, so a silent video works; Muse Spark hears narration when there is some.
    args = ["-map", "0:v:0", "-map", "0:a:0?"]
    if vf:
        args += ["-vf", vf]
    return args + [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ac", "1", "-b:a", "64k",
        "-movflags", "+faststart",
    ]


async def normalize(src: Path, dst: Path, duration: float, hdr: bool = False) -> None:
    """H.264 MP4, long side at most 1280 px, at most 30 fps, HDR tone-mapped to SDR. ffmpeg's autorotate
    applies the phone's rotation."""

    async def encode(vf: str) -> None:
        await _run(
            [settings.ffmpeg_bin, "-y", "-loglevel", "error", "-i", str(src), *_encode_args(vf, 28), "-fpsmax", "30", str(dst)],
            timeout=max(300.0, duration * 3),
            what="prepare the video",
        )

    if hdr:
        try:
            await encode(f"{NORMALIZE_SCALE},{TONE_MAP}")
            return
        except RuntimeError as exc:
            if "not installed" in str(exc):
                raise
            # An ffmpeg older than 8 cannot tone-map in scale, and tone-mapping is several times slower
            # than the plain encode, so it is the likelier step to run out of time. Dim reference
            # photos beat a failed upload.
            log.warning("tone-mapping the HDR video failed, preparing it without: %s", exc)
    await encode(NORMALIZE_SCALE)


async def cut(src: Path, dst: Path, start: float, length: float, vf: str | None = None, crf: int = 28) -> None:
    """Re-encode a span. -ss before -i seeks to the exact frame when re-encoding, and the output starts at 0."""
    await _run(
        [
            settings.ffmpeg_bin, "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(src),
            "-t", f"{length:.3f}", *_encode_args(vf, crf), str(dst),
        ],
        timeout=max(120.0, length * 3),
        what="cut the video",
    )


async def frame_png(src: Path, at: float) -> bytes | None:
    """One frame as PNG (lossless, so the reference photo is encoded once). None when there is no frame there."""
    out = await _run(
        [settings.ffmpeg_bin, "-loglevel", "error", "-ss", f"{at:.3f}", "-i", str(src), "-frames:v", "1", "-c:v", "png", "-f", "image2pipe", "pipe:1"],
        timeout=60,
        what="take a frame from the video",
    )
    return out or None


def plan_chunks(duration: float, chunk_s: float) -> list[tuple[float, float]]:
    """n equal contiguous parts, none longer than chunk_s (no short leftover at the end)."""
    n = max(1, math.ceil(duration / chunk_s - 1e-9))
    return [(duration * i / n, duration * (i + 1) / n) for i in range(n)]


def sharpness(image: bytes) -> float:
    """Variance of the edge image of a grayscale thumbnail: higher is sharper (less motion blur)."""
    with Image.open(io.BytesIO(image)) as img:
        gray = img.convert("L")
        gray.thumbnail((320, 320))
        return float(ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).var[0])


# ---------------------------------------------------------------- Meta calls


async def _all_or_nothing(coros: list[Coroutine[Any, Any, T]]) -> list[T]:
    """Run together; on the first failure cancel the rest and raise that failure (not an ExceptionGroup,
    so the app gets the same sentence as for any other failed call)."""
    tasks = [asyncio.create_task(c) for c in coros]
    if not tasks:
        return []
    try:
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    failed = next((t for t in tasks if t.done() and not t.cancelled() and t.exception() is not None), None)
    if failed is not None:
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        raise failed.exception()  # type: ignore[misc]
    return [t.result() for t in tasks]


async def _upload(client: Any, path: Path, name: str, mime: str, uploaded: list[str]) -> str:
    with path.open("rb") as f:
        obj = await client.files.create(
            file=(name, f, mime),
            purpose="user_data",
            expires_after={"anchor": "created_at", "seconds": settings.digest_file_ttl_s},
            timeout=settings.digest_call_timeout_s,
        )
    uploaded.append(obj.id)
    return obj.id


async def _delete_all(client: Any, file_ids: list[str]) -> None:
    async def delete(file_id: str) -> None:
        try:
            await client.files.delete(file_id)
        except Exception as exc:  # it expires on its own (digest_file_ttl_s)
            log.warning("could not delete uploaded file %s: %s", file_id, exc)

    await asyncio.gather(*(delete(fid) for fid in file_ids))


async def _structured(client: Any, name: str, schema: dict, system: str, content: list[dict[str, Any]]) -> dict:
    response = await client.responses.create(
        model=settings.compile_model,
        input=[
            {"role": "system", "content": system},
            {"type": "message", "role": "user", "content": content},
        ],
        text={"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
        timeout=settings.digest_call_timeout_s,
    )
    raw = getattr(response, "output_text", "") or ""
    status = getattr(response, "status", None)
    # A refusal leaves no text and a cut-off answer is half a JSON object; the raw text goes to the log only.
    if status not in (None, "completed") or not raw.strip():
        log.warning("digest call %s unusable: status=%s output=%r", name, status, raw[:300])
        raise RuntimeError("Muse Spark could not finish reading the video. Try again.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("digest call %s is not JSON: %r", name, raw[:300])
        raise RuntimeError("Muse Spark's answer could not be read. Try again.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Muse Spark's answer did not have the expected shape. Try again.")
    return data


def _file(file_id: str) -> dict[str, Any]:
    return {"type": "input_file", "file_id": file_id}


def _document_blocks(documents: list[Document], doc_ids: list[str]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for doc, file_id in zip(documents, doc_ids):
        blocks.append({"type": "input_text", "text": f"The expert's written procedure ({doc.name}):"})
        blocks.append(_file(file_id))
    return blocks


def _closing(title_hint: str) -> dict[str, Any]:
    hint = title_hint.strip()
    text = f"The expert calls this task: {hint}. Compile the skill." if hint else "Compile the skill."
    return {"type": "input_text", "text": text}


@dataclass
class Observed:
    """A step one part's reader saw, with times already made absolute (seconds into the whole video)."""

    part: int
    start: float
    end: float
    frame: float
    action: str
    visible_result: str
    spoken_details: str
    safety: str
    tools: list[str]
    parts: list[str]


def _absolute(value: Any, offset: float, length: float) -> float:
    """A time the part's reader gave (seconds into the part), clamped into the part, plus its offset."""
    return offset + min(max(float(value), 0.0), length)


async def _read_part(
    client: Any, job: DigestJob, index: int, span: tuple[float, float], file_id: str, title_hint: str
) -> tuple[list[Observed], str]:
    a, b = span
    n = job.chunks_total
    length = b - a
    intro = (
        f"This is part {index + 1} of {n} of the expert's video. It covers {_clock(a)} to {_clock(b)} of the whole "
        f"video (second {a:.0f} to second {b:.0f}). Give every time in seconds from the start of this part: 0 is "
        f"the first frame of this part, which is {length:.0f} seconds long."
    )
    content: list[dict[str, Any]] = [{"type": "input_text", "text": intro}, _file(file_id)]
    if title_hint.strip():
        content.append({"type": "input_text", "text": f"The expert calls this task: {title_hint.strip()}."})
    data = await _structured(client, "DigestChunk", CHUNK_SCHEMA, CHUNK_SYSTEM_PROMPT, content)
    try:
        observed = [
            Observed(
                part=index + 1,
                start=_absolute(s["start_s"], a, length),
                end=_absolute(s["end_s"], a, length),
                frame=_absolute(s["frame_s"], a, length),
                action=str(s["action"]).strip(),
                visible_result=str(s["visible_result"]).strip(),
                spoken_details=str(s["spoken_details"]).strip(),
                safety=str(s["safety"]).strip(),
                tools=[str(t) for t in s["tools"] if str(t).strip()],
                parts=[str(p) for p in s["parts"] if str(p).strip()],
            )
            for s in data["steps"]
        ]
        summary = str(data["summary"]).strip()
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("digest part %s has an unexpected shape: %s", index + 1, exc)
        raise RuntimeError("Muse Spark's answer did not have the expected shape. Try again.") from exc
    job.chunks_done += 1
    job.progress = round(max(job.progress, 0.25 + 0.5 * job.chunks_done / n), 3)
    job.message = f"Muse Spark is watching the video: {job.chunks_done} of {n} parts done."
    return observed, summary


def observed_lines(parts: list[tuple[tuple[float, float], list[Observed], str]]) -> str:
    """What every part's reader saw, as text lines with absolute times, for the merge call."""
    lines: list[str] = []
    for i, ((a, b), steps, summary) in enumerate(parts, start=1):
        lines.append(f"Part {i} of {len(parts)} (second {a:.0f} to second {b:.0f}). {summary}".rstrip())
        if not steps:
            lines.append("- No steps seen in this part.")
        for s in steps:
            line = f"- Second {s.start:.0f} to second {s.end:.0f}, best frame at second {s.frame:.0f}. {s.action}"
            for label, value in (
                ("Visible when done", s.visible_result),
                ("The expert says", s.spoken_details),
                ("Safety", s.safety),
                ("Tools", ", ".join(s.tools)),
                ("Parts", ", ".join(s.parts)),
            ):
                if value:
                    line += f" {label}: {value.rstrip('.')}."
            lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------- the model's skill, checked


@dataclass
class Draft:
    """One step as the model wrote it, with its times checked against the video."""

    data: dict[str, Any]
    source: str
    times: tuple[float, float, float] | None  # start, end, best frame; None when the video does not show it
    concepts: list[str]


def check_times(raw: dict[str, Any], source: str, duration: float) -> tuple[float, float, float] | None:
    """Times inside the video, end not before start, the best frame inside the step (else its midpoint).
    Document-only and added steps, and steps marked -1, have no times."""
    start, end, frame = (float(raw[k]) for k in ("start_s", "end_s", "frame_s"))
    if source in ("document", "added") or start < 0:
        return None
    start = min(max(start, 0.0), duration)
    end = min(max(end, 0.0), duration) if end >= 0 else start
    if end < start:
        end = start
    if not start <= frame <= end:
        frame = (start + end) / 2
    return round(start, 2), round(end, 2), round(frame, 2)


def check_skill(data: dict[str, Any], duration: float) -> list[Draft]:
    try:
        drafts: list[Draft] = []
        for raw in data["steps"][:MAX_STEPS]:
            source = raw["source"] if raw["source"] in SOURCES else "video"
            drafts.append(
                Draft(
                    data=raw,
                    source=source,
                    times=check_times(raw, source, duration),
                    concepts=[str(c).strip() for c in raw["sam_concepts"] if str(c).strip()][:4],
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("digested skill has an unexpected shape: %s", exc)
        raise RuntimeError("Muse Spark's answer did not have the expected shape. Try again.") from exc
    if not drafts:
        raise RuntimeError("Muse Spark found no steps in the video. Try a video that shows the work clearly.")
    return drafts


# ---------------------------------------------------------------- reference frames and clips


@dataclass
class StepMedia:
    reference: bytes | None = None  # JPEG
    concepts: list[str] = field(default_factory=list)
    clip: Path | None = None


async def _grounded(client: Any, jpeg: bytes, concepts: list[str]) -> tuple[str | None, bool]:
    """(the concept SAM found, whether SAM answered). A timeout or error stops trying more frames."""
    try:
        found = await asyncio.wait_for(sam.highlight(client, jpeg, concepts), settings.highlight_timeout_s)
    except Exception as exc:
        log.warning("SAM grounding of a reference frame failed: %s", exc)
        return None, False
    return (found.concept if found is not None else None), True


async def reference_frame(
    client: Any, video: Path, picture_end: float, times: tuple[float, float, float], concepts: list[str]
) -> tuple[bytes | None, list[str]]:
    """The reference photo for a step, and its concepts with the one SAM found moved to the front.

    The sharpest of three frames around the model's best second; if SAM finds none of the step's
    concepts in it, up to two later frames in the step, keeping the first SAM finds something in."""
    start, end, best = times
    last = max(0.0, picture_end - FRAME_END_MARGIN_S)

    def inside(t: float) -> float:
        return round(min(max(t, start), end, last), 3)

    tried: list[float] = []
    candidates: list[tuple[float, bytes]] = []
    for t in (inside(best), inside(best - FRAME_NUDGE_S), inside(best + FRAME_NUDGE_S)):
        if t in tried:
            continue
        tried.append(t)
        png = await frame_png(video, t)
        if png:
            candidates.append((t, png))
    if not candidates:
        # ffmpeg only returns a frame at or after the time asked for, so all three can fall after the
        # last frame: a video under about 4 fps, or a best second at the very end. Walk back through the
        # step, then to a second before the picture's end (never later than the step's start).
        walk_back = [inside(start + f * (end - start)) for f in sorted(FALLBACK_FRACTIONS, reverse=True)]
        for t in (*walk_back, inside(start), round(max(0.0, min(start, last - 1.0)), 3)):
            if t in tried:
                continue
            tried.append(t)
            png = await frame_png(video, t)
            if png:
                candidates.append((t, png))
                break
    if not candidates:
        return None, concepts
    scores = [await asyncio.to_thread(sharpness, png) for _, png in candidates]
    # max() keeps the first of equals, so the model's own second wins a tie.
    _, sharpest = candidates[max(range(len(candidates)), key=lambda i: scores[i])]
    first = await asyncio.to_thread(prepare_image, sharpest)
    if not concepts:
        return first, concepts

    hit, answered = await _grounded(client, first, concepts)
    chosen = first
    for fraction in FALLBACK_FRACTIONS:
        if hit is not None or not answered:
            break
        t = inside(start + fraction * (end - start))
        if any(abs(t - u) < 0.3 for u in tried):
            continue
        tried.append(t)
        png = await frame_png(video, t)
        if not png:
            continue
        jpeg = await asyncio.to_thread(prepare_image, png)
        hit, answered = await _grounded(client, jpeg, concepts)
        if hit is not None:
            chosen = jpeg
    if hit is None:
        return first, concepts
    return chosen, [hit, *(c for c in concepts if c != hit)]


def clip_span(times: tuple[float, float, float], picture_end: float) -> tuple[float, float]:
    """From the step's start, at most digest_clip_max_s, at least MIN_CLIP_S, where the video has a
    picture. A step wholly after the picture ends (the sound runs on) gets the last seconds of picture."""
    start, end, _ = times
    a = start
    b = min(end, start + settings.digest_clip_max_s, picture_end)
    if b - a < MIN_CLIP_S:
        b = min(picture_end, a + MIN_CLIP_S)
        a = max(0.0, b - MIN_CLIP_S)
    return a, b


async def step_clip(video: Path, target: Path, times: tuple[float, float, float], picture_end: float) -> Path | None:
    a, b = clip_span(times, picture_end)
    await cut(video, target, a, b - a, vf=CLIP_SCALE, crf=30)
    if not (target.is_file() and target.stat().st_size > 0):
        return None
    # Sound with no picture would play as a black box under "Watch the expert".
    try:
        await probe(target)
    except VideoError as exc:
        log.warning("clip %s left out: %s", target.name, exc)
        return None
    return target


async def _step_media(
    client: Any, video: Path, workdir: Path, picture_end: float, n: int, draft: Draft
) -> StepMedia:
    """A failed frame or clip leaves that field empty; it does not fail the job."""
    media = StepMedia(concepts=draft.concepts)
    if draft.times is None:
        return media

    async def frame() -> None:
        try:
            media.reference, media.concepts = await reference_frame(client, video, picture_end, draft.times, draft.concepts)
        except Exception:
            log.exception("reference frame for step %s failed", n)

    async def clip() -> None:
        try:
            media.clip = await step_clip(video, workdir / f"clip_{n}.mp4", draft.times, picture_end)
        except Exception:
            log.exception("clip for step %s failed", n)

    await asyncio.gather(frame(), clip())
    return media


# ---------------------------------------------------------------- the pipeline


async def digest(client: Any, inp: DigestInput, job: DigestJob) -> tuple[Skill, dict[str, bytes | Path]]:
    """The skill and its media (keyed by path relative to the skills dir). Updates the job as it goes."""
    _stage(job, "preparing", 0.05, "Preparing the video.")
    normalized = inp.workdir / "video.mp4"
    await normalize(inp.video, normalized, job.video_seconds or settings.digest_max_video_s, hdr=inp.hdr)
    # The upload can be up to 1 GiB and is not needed again.
    inp.video.unlink(missing_ok=True)
    info = await probe(normalized)
    duration = info.duration
    job.video_seconds = round(duration, 2)
    spans = plan_chunks(duration, settings.digest_chunk_s)
    n = len(spans)
    job.chunks_total = n
    job.progress = max(job.progress, 0.1)
    if n == 1:
        parts = [normalized]
    else:
        parts = []
        for i, (a, b) in enumerate(spans):
            part = inp.workdir / f"part_{i + 1}.mp4"
            await cut(normalized, part, a, b - a)
            parts.append(part)
            job.progress = round(max(job.progress, 0.1 + 0.05 * (i + 1) / n), 3)

    docs = inp.documents
    uploaded: list[str] = []
    try:
        _stage(
            job, "uploading", 0.15,
            "Uploading the video and the document to Meta." if docs else "Uploading the video to Meta.",
        )
        sem = asyncio.Semaphore(max(1, settings.digest_parallel_calls))
        total_files = len(parts) + len(docs)
        sent = 0

        async def upload(path: Path, name: str, mime: str) -> str:
            nonlocal sent
            async with sem:
                file_id = await _upload(client, path, name, mime, uploaded)
            sent += 1
            job.progress = round(max(job.progress, 0.15 + 0.1 * sent / total_files), 3)
            return file_id

        ids = await _all_or_nothing(
            [upload(p, f"part_{i + 1}.mp4" if n > 1 else "video.mp4", "video/mp4") for i, p in enumerate(parts)]
            + [upload(d.path, f"document_{i + 1}.pdf", "application/pdf") for i, d in enumerate(docs)]
        )
        video_ids, doc_ids = ids[: len(parts)], ids[len(parts):]

        if n == 1:
            _stage(
                job, "reading", 0.25,
                "Muse Spark is watching the video and reading the document." if docs else "Muse Spark is watching the video.",
            )
            content = [
                {"type": "input_text", "text": "The expert's video:"},
                _file(video_ids[0]),
                *_document_blocks(docs, doc_ids),
                _closing(inp.title_hint),
            ]
            data = await _structured(client, "DigestedSkill", DIGESTED_SKILL_SCHEMA, DIGEST_SYSTEM_PROMPT, content)
        else:
            _stage(job, "reading", 0.25, f"Muse Spark is watching the video: 0 of {n} parts done.")

            async def read(i: int) -> tuple[list[Observed], str]:
                async with sem:
                    return await _read_part(client, job, i, spans[i], video_ids[i], inp.title_hint)

            results = await _all_or_nothing([read(i) for i in range(n)])
            _stage(
                job, "merging", 0.75,
                "Combining the video with the document." if docs else "Combining the parts of the video.",
            )
            intro = (
                f"The expert's video is {duration:.0f} seconds long ({_clock(duration)}). It was too long to watch in "
                f"one go, so it was read in {n} parts, one after another, by separate readers. Below is what each part "
                "showed, with every time in seconds from the start of the whole video. A step that crosses the boundary "
                "between two parts may appear twice, at the end of one part and the start of the next: merge it into "
                "one step. Copy start_s, end_s and frame_s from the observed steps: the first start, the last end, and "
                "the best frame among them. Use -1 for steps only in the document and for added gates."
            )
            content = [
                {"type": "input_text", "text": intro},
                {"type": "input_text", "text": observed_lines([(spans[i], *results[i]) for i in range(n)])},
                *_document_blocks(docs, doc_ids),
                _closing(inp.title_hint),
            ]
            data = await _structured(client, "DigestedSkill", DIGESTED_SKILL_SCHEMA, DIGEST_SYSTEM_PROMPT, content)
    finally:
        await _delete_all(client, uploaded)

    drafts = check_skill(data, duration)
    title = str(data.get("title") or "").strip() or inp.title_hint.strip() or "Untitled skill"
    skill_id = f"{slugify(title)}-{secrets.token_hex(2)}"
    media_dir = f"compiled/media/{skill_id}"

    _stage(job, "frames", 0.85, "Picking a reference frame for each step.")
    step_sem = asyncio.Semaphore(PARALLEL_STEPS)
    finished = 0

    async def make(n_step: int, draft: Draft) -> StepMedia:
        nonlocal finished
        async with step_sem:
            result = await _step_media(client, normalized, inp.workdir, info.picture_end, n_step, draft)
        finished += 1
        job.progress = round(max(job.progress, 0.85 + 0.12 * finished / len(drafts)), 3)
        return result

    made = await asyncio.gather(*(make(i, d) for i, d in enumerate(drafts, start=1)))

    media: dict[str, bytes | Path] = {}
    steps: list[Step] = []
    try:
        for n_step, (draft, sm) in enumerate(zip(drafts, made), start=1):
            s = draft.data
            reference = clip = None
            if sm.reference is not None:
                reference = f"{media_dir}/step_{n_step}.jpg"
                media[reference] = sm.reference
            if sm.clip is not None:
                clip = f"{media_dir}/step_{n_step}.mp4"
                media[clip] = sm.clip
            gate = bool(s["safety_gate"])
            steps.append(
                Step(
                    n=n_step,
                    instruction=s["instruction"],
                    expected_state=s["expected_state"],
                    reference_image=reference,
                    sam_concepts=sm.concepts,
                    verify_hints=s["verify_hints"],
                    common_mistakes=[m for m in s["common_mistakes"] if str(m).strip()],
                    safety_gate=gate,
                    gate_requirement=(s["gate_requirement"].strip() or None) if gate else None,
                    watch_me=bool(s["watch_me"]),
                    est_minutes=max(1, int(s["est_minutes"])),
                    source=draft.source,  # type: ignore[arg-type]
                    video_start_s=draft.times[0] if draft.times else None,
                    video_end_s=draft.times[1] if draft.times else None,
                    clip=clip,
                    document_note=str(s["document_note"]).strip(),
                )
            )
        skill = Skill(
            id=skill_id,
            title=title,
            domain=data["domain"],
            author=inp.author.strip() or "Anonymous",
            difficulty=min(5, max(1, int(data["difficulty"]))),
            est_minutes=max(1, int(data["est_minutes"])),
            price_usd=0,
            tools=[t for t in data["tools"] if str(t).strip()],
            parts=[p for p in data["parts"] if str(p).strip()],
            vocabulary=[v for v in data["vocabulary"] if str(v).strip()],
            steps=steps,
            compiled=True,
            conflicts=[Conflict(**c) for c in data["conflicts"] if str(c.get("topic", "")).strip()],
            digest=DigestInfo(video_seconds=round(duration, 2), chunks=n, documents=[d.name for d in docs]),
        )
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        # pydantic's ValidationError is a ValueError too; either way the app gets one sentence.
        log.warning("digested skill has an unexpected shape: %s", exc)
        raise RuntimeError("Muse Spark's answer did not have the expected shape. Try again.") from exc
    return skill, media


async def run_job(
    job: DigestJob,
    client: Any,
    inp: DigestInput,
    save: Callable[[Skill, dict[str, bytes | Path]], Skill],
    describe_failure: Callable[[Exception], str],
) -> None:
    """The background task behind POST /skills/digest. Never raises: a failure ends up on the job.

    `save` publishes the skill and returns it as stored; `describe_failure` is the API layer's
    mapping from an exception to the sentence the app shows."""
    try:
        try:
            skill, media = await digest(client, inp, job)
            _stage(job, "saving", 0.97, "Publishing the skill.")
            saved = save(skill, media)
        finally:
            # Before the job says done or failed, so nothing is left on disk once it does.
            shutil.rmtree(inp.workdir, ignore_errors=True)
        job.skill = saved
        job.status = "done"
        _stage(job, "done", 1.0, "The skill is published.")
    except asyncio.CancelledError:
        _fail(job, "The backend stopped before the video was digested. Try again.")
        raise
    except Exception as exc:
        log.exception("digest job %s failed", job.id)
        _fail(job, describe_failure(exc))


def _fail(job: DigestJob, sentence: str) -> None:
    job.status = "failed"
    job.stage = "failed"
    job.error = sentence
    job.message = sentence
