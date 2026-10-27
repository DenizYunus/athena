"""The two Muse Spark calls that make Athena work.

turn():   push-to-talk. transcript + optional mp4 clip + optional photo -> spoken reply (streamed).
verify(): snapshot check. reference photo + learner photo -> strict JSON StepVerdict.

Both take the client as a parameter so tests can pass a fake; the API layer passes
the real AsyncOpenAI from meta_client.get_client().
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from .config import settings
from .media import image_block, image_block_from_source, prepare_image, video_block
from .prompts import guide_system_prompt, verify_system_prompt, verify_user_blocks
from .schemas import Message, Session, Skill, StepVerdict

MAX_HISTORY_MESSAGES = 12


def _history_items(session: Session) -> list[dict[str, Any]]:
    """Recent text-only history. Media from earlier turns is not resent."""
    items: list[dict[str, Any]] = []
    for m in session.history[-MAX_HISTORY_MESSAGES:]:
        prefix = ""
        if m.role == "user" and (m.had_clip or m.had_photo):
            prefix = "[sent with a clip] " if m.had_clip else "[sent with a photo] "
        items.append({"role": m.role, "content": prefix + m.text})
    return items


async def turn_stream(
    client: Any,
    skill: Skill,
    session: Session,
    transcript: str,
    clip_mp4: bytes | None = None,
    photo: bytes | None = None,
) -> AsyncIterator[str]:
    """Yield reply text deltas. Appends the exchange to session.history when done."""
    step = skill.step(session.current_step)

    content: list[dict[str, Any]] = []
    text = transcript.strip() or "(no words, just showing you this)"
    content.append({"type": "input_text", "text": text})
    if clip_mp4:
        content.append(await video_block(clip_mp4, client))
    if photo:
        content.append(image_block(prepare_image(photo)))

    input_items: list[dict[str, Any]] = [
        {"role": "system", "content": guide_system_prompt(skill, step)},
        *_history_items(session),
        {"type": "message", "role": "user", "content": content},
    ]

    stream = await client.responses.create(
        model=settings.guide_model,
        input=input_items,
        stream=True,
    )

    parts: list[str] = []
    async for event in stream:
        etype = getattr(event, "type", None)
        if etype == "response.output_text.delta":
            delta = getattr(event, "delta", "") or ""
            parts.append(delta)
            yield delta
        elif etype == "response.failed":
            raise RuntimeError(f"Meta API stream failed: {getattr(event, 'response', None)}")

    reply = "".join(parts).strip()
    session.history.append(
        Message(role="user", text=text, step=step.n, had_clip=bool(clip_mp4), had_photo=bool(photo))
    )
    session.history.append(Message(role="assistant", text=reply, step=step.n))


async def turn(
    client: Any,
    skill: Skill,
    session: Session,
    transcript: str,
    clip_mp4: bytes | None = None,
    photo: bytes | None = None,
) -> str:
    parts = [d async for d in turn_stream(client, skill, session, transcript, clip_mp4, photo)]
    return "".join(parts).strip()


async def verify(client: Any, skill: Skill, session: Session, photo: bytes) -> StepVerdict:
    """One structured-output call. Does not mutate the session; the API layer decides what to do."""
    step = skill.step(session.current_step)
    reference = image_block_from_source(step.reference_image) if step.reference_image else None
    user_blocks = verify_user_blocks(reference, image_block(prepare_image(photo)))

    response = await client.responses.create(
        model=settings.verify_model,
        input=[
            {"role": "system", "content": verify_system_prompt(skill, step)},
            {"type": "message", "role": "user", "content": user_blocks},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "StepVerdict",
                "strict": True,
                "schema": json.loads(json.dumps(_verdict_schema())),
            }
        },
    )
    raw = response.output_text
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Verifier returned non-JSON: {raw[:200]}") from exc
    verdict = StepVerdict.model_validate(data)
    if step.safety_gate and verdict.status == "pass" and verdict.confidence < 0.8:
        # Belt and braces: a hesitant pass on a gate is treated as unclear.
        verdict = verdict.model_copy(update={"status": "unclear"})
        if not verdict.photo_request.needed:
            verdict.photo_request.needed = True
            verdict.photo_request.what = step.gate_requirement or step.expected_state
    return verdict


def _verdict_schema() -> dict:
    from .schemas import VERDICT_JSON_SCHEMA

    return VERDICT_JSON_SCHEMA
