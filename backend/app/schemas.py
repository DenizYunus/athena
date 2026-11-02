"""Data shapes: skills, steps, verdicts, sessions.

The Skill JSON is what the compiler will produce from an expert's photos and notes,
and what the guide reads at session time. Keep it boring and explicit: every field
here is something the guide prompt or the verifier prompt actually uses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


# Where a digested step came from. "added": a hazard-removal gate that neither the video nor the
# document covers but the task clearly needs; the review screen labels it as added by Athena.
StepSource = Literal["video", "document", "both", "added"]


class Step(BaseModel):
    n: int = Field(ge=1)
    instruction: str
    expected_state: str = Field(description="What a photo must show for this step to count as done.")
    reference_image: str | None = Field(
        default=None,
        description="Path (relative to the skills dir) or URL of the expert's photo of this step done right.",
    )
    sam_concepts: list[str] = Field(default_factory=list, description="Short noun phrases for SAM 3.1.")
    verify_hints: str = ""
    common_mistakes: list[str] = Field(default_factory=list)
    safety_gate: bool = False
    gate_requirement: str | None = None
    watch_me: bool = Field(default=False, description="Author flagged this step as worth continuous watching.")
    est_minutes: int = 2
    # The fields below are set only for skills digested from a video; seed skills and photo-compiled
    # skills load with the defaults.
    source: StepSource | None = None
    video_start_s: float | None = Field(default=None, description="Seconds into the expert's video where this step starts.")
    video_end_s: float | None = None
    clip: str | None = Field(
        default=None,
        description="Path (relative to the skills dir) of a short clip of the expert doing this step.",
    )
    document_note: str = Field(default="", description="Specs and cautions from the expert's document for this step.")


class Conflict(BaseModel):
    """A point where the expert's video and written procedure disagree, and what the skill uses."""

    topic: str
    video_says: str
    document_says: str
    resolution: str


class DigestInfo(BaseModel):
    video_seconds: float
    chunks: int
    documents: list[str] = Field(default_factory=list, description="File names as uploaded.")


class Skill(BaseModel):
    id: str
    title: str
    domain: str
    author: str
    difficulty: int = Field(ge=1, le=5)
    est_minutes: int
    price_usd: float = 0
    tools: list[str] = Field(default_factory=list)
    parts: list[str] = Field(default_factory=list)
    vocabulary: list[str] = Field(
        default_factory=list,
        description="Part names fed to Voice Transcribe as keyword bias.",
    )
    steps: list[Step]
    compiled: bool = Field(default=False, description="Produced by the compiler at runtime (deletable), not a seed.")
    conflicts: list[Conflict] = Field(default_factory=list)
    digest: DigestInfo | None = Field(default=None, description="Set when the skill was digested from a video.")

    def step(self, n: int) -> Step:
        for s in self.steps:
            if s.n == n:
                return s
        raise KeyError(n)


DigestStage = Literal["queued", "preparing", "uploading", "reading", "merging", "frames", "saving", "done", "failed"]


class DigestJob(BaseModel):
    """A video being digested into a skill. The app polls it until status is done or failed."""

    id: str
    status: Literal["running", "done", "failed"]
    stage: DigestStage
    message: str = Field(description="One sentence for the app's progress line.")
    progress: float = Field(ge=0, le=1)
    chunks_total: int = 0
    chunks_done: int = 0
    video_seconds: float = 0
    documents: list[str] = Field(default_factory=list)
    skill: Skill | None = None
    error: str | None = Field(default=None, description="A sentence the app shows; never an upstream status code.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PhotoRequest(BaseModel):
    needed: bool
    what: str = ""
    angle: str = ""


VerdictStatus = Literal["pass", "fail", "unclear"]


class StepVerdict(BaseModel):
    status: VerdictStatus
    confidence: float = Field(ge=0, le=1)
    reason: str
    what_to_fix: str = ""
    photo_request: PhotoRequest
    safety_concern: bool = False


# Hand-written so strict mode is satisfied: object root, additionalProperties false
# everywhere, every property listed in required, no anyOf/oneOf, no recursion.
VERDICT_JSON_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["pass", "fail", "unclear"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "description": "One or two sentences on what the photo shows."},
        "what_to_fix": {
            "type": "string",
            "description": "Only when status is fail: the concrete correction. Empty string otherwise.",
        },
        "photo_request": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "needed": {"type": "boolean"},
                "what": {"type": "string", "description": "What must be visible in the next photo."},
                "angle": {"type": "string", "description": "Where to shoot from and how far."},
            },
            "required": ["needed", "what", "angle"],
        },
        "safety_concern": {"type": "boolean"},
    },
    "required": ["status", "confidence", "reason", "what_to_fix", "photo_request", "safety_concern"],
}


Mode = Literal["ptt", "snapshot", "watch"]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    step: int
    had_clip: bool = False
    had_photo: bool = False
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VerdictRecord(BaseModel):
    step: int
    verdict: StepVerdict
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Session(BaseModel):
    id: str
    skill_id: str
    mode: Mode = "ptt"
    current_step: int = 1
    completed: bool = False
    history: list[Message] = Field(default_factory=list)
    verdicts: list[VerdictRecord] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StartSessionRequest(BaseModel):
    skill_id: str
    mode: Mode = "ptt"


class TurnResponse(BaseModel):
    reply: str
    transcript: str = Field(default="", description="What the user said, as sent or as transcribed from the clip.")
    current_step: int
    session_id: str


class Box(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class MaskShape(BaseModel):
    """An SVG path in the mask's own coordinate space; translate by (left, top) inside the frame."""

    left: float
    top: float
    width: float
    height: float
    path: str


class HighlightObject(BaseModel):
    id: int
    box: Box
    confidence: float
    mask: MaskShape | None = None


class Highlight(BaseModel):
    concept: str
    frame_width: int = Field(description="Pixel size of the frame the coordinates refer to (the resized photo).")
    frame_height: int
    objects: list[HighlightObject]


class VerifyResponse(BaseModel):
    verdict: StepVerdict
    advanced: bool
    current_step: int
    completed: bool
    session_id: str
    highlight: Highlight | None = Field(default=None, description="SAM 3.1 match for the step's part, when found.")


class TranscriptResponse(BaseModel):
    transcript: str
