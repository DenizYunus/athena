"""Data shapes: skills, steps, verdicts, sessions.

The Skill JSON is what the compiler will produce from an expert's photos and notes,
and what the guide reads at session time. Keep it boring and explicit: every field
here is something the guide prompt or the verifier prompt actually uses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


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

    def step(self, n: int) -> Step:
        for s in self.steps:
            if s.n == n:
                return s
        raise KeyError(n)


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
    current_step: int
    session_id: str


class VerifyResponse(BaseModel):
    verdict: StepVerdict
    advanced: bool
    current_step: int
    completed: bool
    session_id: str
