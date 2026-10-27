"""The skill compiler: an expert's photos and notes -> a Skill with checkable steps.

One Muse Spark call with every photo interleaved with the author's note for it, and a
strict JSON schema for the output. The compiler decides the step order, which photo
is the reference for which step, which steps are safety gates, and the SAM concepts
(plain visual noun phrases, because that is what SAM 3.1 responds to).
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from typing import Any

from .config import settings
from .media import image_block, prepare_image
from .schemas import Skill, Step


@dataclass
class Capture:
    photo: bytes | None
    note: str = ""


COMPILED_SKILL_JSON_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "description": "Imperative, under 60 characters, e.g. 'Replace a refrigerator start relay'."},
        "domain": {"type": "string", "description": "One word: appliance, bike, plumbing, electronics, car, home, garden, other."},
        "difficulty": {"type": "integer", "minimum": 1, "maximum": 5},
        "est_minutes": {"type": "integer", "minimum": 1},
        "tools": {"type": "array", "items": {"type": "string"}},
        "parts": {"type": "array", "items": {"type": "string"}},
        "vocabulary": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Part and tool names a learner might say; used to bias speech recognition.",
        },
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "instruction": {"type": "string", "description": "One or two sentences, spoken aloud."},
                    "expected_state": {"type": "string", "description": "What a photo must show for this step to count as done."},
                    "reference_photo": {
                        "type": "integer",
                        "description": "1-based index of the author's photo that shows this step done right, or 0 if none fits.",
                    },
                    "sam_concepts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Two to four plain visual noun phrases a segmentation model can find: colour + shape + object, e.g. 'black plastic box', 'copper pipe', 'power cord'. No technical part names alone.",
                    },
                    "verify_hints": {"type": "string", "description": "What the verifier should look for and what to reject."},
                    "common_mistakes": {"type": "array", "items": {"type": "string"}},
                    "safety_gate": {"type": "boolean", "description": "True only for steps that remove a hazard (power off, gas off, pressure released, vehicle secured)."},
                    "gate_requirement": {"type": "string", "description": "When safety_gate: the evidence a photo must show. Empty otherwise."},
                    "watch_me": {"type": "boolean", "description": "True for delicate steps worth continuous watching."},
                    "est_minutes": {"type": "integer", "minimum": 1},
                },
                "required": [
                    "instruction", "expected_state", "reference_photo", "sam_concepts", "verify_hints",
                    "common_mistakes", "safety_gate", "gate_requirement", "watch_me", "est_minutes",
                ],
            },
        },
    },
    "required": ["title", "domain", "difficulty", "est_minutes", "tools", "parts", "vocabulary", "steps"],
}


COMPILER_SYSTEM_PROMPT = """You are the Athena skill compiler. An expert has photographed a hands-on task in order and dictated a short note for each photo. Turn that into a skill a novice can follow with a phone camera, where every step can be checked from a photo.

Rules:
- Keep the expert's order and wording where it is clear; fill gaps a novice would hit (which screw, which direction, what to hold).
- Each step must be checkable from one photo: write expected_state as what must be visible, concrete and physical.
- Mark a step as a safety gate only when it removes a hazard: unplugging, switching a breaker, shutting a valve, releasing pressure, chocking wheels. Gates come first. gate_requirement states the photo evidence, e.g. "the plug is out of the outlet and both are in the same frame".
- reference_photo is the 1-based index of the photo that best shows the step completed. Use 0 when no photo fits. A photo may be the reference for at most one step.
- sam_concepts are for a segmentation model that only understands plain visual language. Write what a stranger would call the thing: "black plastic box", "copper pipe", "white wire", "power cord", "screwdriver". Give two to four alternatives per step, most specific first. Never a bare technical name like "start relay" or "capacitor" on its own.
- vocabulary lists the technical names and brand terms a learner might say aloud (parts, tools, model names), for speech recognition.
- common_mistakes: real ones, one short phrase each. Leave empty if none.
- Difficulty 1 is "anyone with hands", 5 is "trained technician".
- Write for the ear: instructions are read aloud by the phone. No markdown."""


def _author_blocks(captures: list[Capture], title_hint: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    intro = "Here are the expert's photos in order, each with the note they dictated."
    if title_hint.strip():
        intro += f" The expert calls this task: {title_hint.strip()}."
    blocks.append({"type": "input_text", "text": intro})
    n = 0
    for cap in captures:
        if cap.photo is not None:
            n += 1
            note = cap.note.strip() or "(no note)"
            blocks.append({"type": "input_text", "text": f"Photo {n}. Expert's note: {note}"})
            blocks.append(image_block(prepare_image(cap.photo)))
        elif cap.note.strip():
            blocks.append({"type": "input_text", "text": f"Expert's note without a photo: {cap.note.strip()}"})
    blocks.append({"type": "input_text", "text": f"There are {n} photos. Compile the skill."})
    return blocks


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] or "skill"


async def compile_skill(client: Any, captures: list[Capture], author: str, title_hint: str = "") -> tuple[Skill, dict[str, bytes]]:
    """Returns the Skill and the media files to store, keyed by path relative to the skills dir."""
    photos = [c.photo for c in captures if c.photo is not None]
    if not photos and not any(c.note.strip() for c in captures):
        raise ValueError("nothing to compile: send photos and/or notes")

    response = await client.responses.create(
        model=settings.compile_model,
        input=[
            {"role": "system", "content": COMPILER_SYSTEM_PROMPT},
            {"type": "message", "role": "user", "content": _author_blocks(captures, title_hint)},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "CompiledSkill",
                "strict": True,
                "schema": COMPILED_SKILL_JSON_SCHEMA,
            }
        },
    )
    raw = response.output_text
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Compiler returned non-JSON: {raw[:200]}") from exc

    skill_id = f"{slugify(data['title'])}-{secrets.token_hex(2)}"
    media_dir = f"compiled/media/{skill_id}"
    media: dict[str, bytes] = {}
    for i, photo in enumerate(photos, start=1):
        media[f"{media_dir}/photo_{i}.jpg"] = prepare_image(photo)

    steps: list[Step] = []
    used: set[int] = set()
    for n, s in enumerate(data["steps"], start=1):
        ref = int(s.get("reference_photo") or 0)
        reference = None
        if 1 <= ref <= len(photos) and ref not in used:
            used.add(ref)
            reference = f"{media_dir}/photo_{ref}.jpg"
        gate = bool(s["safety_gate"])
        steps.append(
            Step(
                n=n,
                instruction=s["instruction"],
                expected_state=s["expected_state"],
                reference_image=reference,
                sam_concepts=[c for c in s["sam_concepts"] if c.strip()][:4],
                verify_hints=s["verify_hints"],
                common_mistakes=s["common_mistakes"],
                safety_gate=gate,
                gate_requirement=(s["gate_requirement"] or None) if gate else None,
                watch_me=bool(s["watch_me"]),
                est_minutes=max(1, int(s["est_minutes"])),
            )
        )
    if not steps:
        raise RuntimeError("Compiler produced no steps")

    skill = Skill(
        id=skill_id,
        title=data["title"],
        domain=data["domain"],
        author=author.strip() or "Anonymous",
        difficulty=min(5, max(1, int(data["difficulty"]))),
        est_minutes=max(1, int(data["est_minutes"])),
        price_usd=0,
        tools=data["tools"],
        parts=data["parts"],
        vocabulary=data["vocabulary"],
        steps=steps,
        compiled=True,
    )
    return skill, media
