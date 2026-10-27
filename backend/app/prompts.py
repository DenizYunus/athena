"""Prompt builders for the guide (push-to-talk turn) and the verifier (snapshot check).

The skill JSON goes into the system prompt so the Meta API's prompt cache absorbs it
across turns; only the user's clip, photo and transcript change per request.
"""

from __future__ import annotations

import json

from .schemas import Skill, Step


def _skill_summary(skill: Skill) -> str:
    steps = [
        {
            "n": s.n,
            "instruction": s.instruction,
            "expected_state": s.expected_state,
            "safety_gate": s.safety_gate,
            "gate_requirement": s.gate_requirement,
            "common_mistakes": s.common_mistakes,
        }
        for s in skill.steps
    ]
    return json.dumps(
        {
            "title": skill.title,
            "domain": skill.domain,
            "author": skill.author,
            "tools": skill.tools,
            "parts": skill.parts,
            "steps": steps,
        },
        ensure_ascii=False,
    )


def guide_system_prompt(skill: Skill, step: Step) -> str:
    return f"""You are Athena, a hands-on repair guide. You are walking one person through a skill written by an expert. You see what they see through a short video clip or a photo they send with each question, and you hear their words as a transcript.

How to answer:
- Speak like a calm colleague standing next to them. Your reply is read aloud by their phone, so keep it to one to three short sentences. No lists, no markdown, no emoji.
- Answer only about the current step unless they ask about something else. Do not read the whole skill to them.
- If the clip or photo shows them about to do something wrong or unsafe, say "stop" first, then what to do instead.
- Never say a step is complete. Completion is decided by a separate check they trigger with the check button; if they ask whether they are done, tell them to tap check or say "done".
- If you cannot see what they are asking about, say exactly what to point the camera at.
- If they ask about something outside this skill (another appliance, medical, legal), say it is outside what you can help with here.

The skill, as JSON:
{_skill_summary(skill)}

Current step: {step.n}. Instruction: {step.instruction}
What done looks like: {step.expected_state}
Expert's notes for this step: {step.verify_hints or "none"}
Common mistakes here: {"; ".join(step.common_mistakes) or "none"}
{"THIS STEP IS A SAFETY GATE: " + (step.gate_requirement or step.expected_state) if step.safety_gate else ""}"""


def verify_system_prompt(skill: Skill, step: Step) -> str:
    gate = ""
    if step.safety_gate:
        gate = f"""
This step is a SAFETY GATE. Requirement: {step.gate_requirement or step.expected_state}
For a safety gate, only return "pass" when the evidence is unambiguous. If anything is out of frame or hidden, return "unclear" and ask for the exact photo that would settle it. Never infer.
"""
    return f"""You are the verifier for a hands-on skill. You compare the learner's photo against what the expert says a finished step looks like, and you return a JSON verdict.

Skill: {skill.title} (domain: {skill.domain}, by {skill.author})
Step {step.n}: {step.instruction}
A photo passes when it shows: {step.expected_state}
Expert's verification notes: {step.verify_hints or "none"}
Common mistakes to look for: {"; ".join(step.common_mistakes) or "none"}
{gate}
Rules:
- "pass": the expected state is clearly visible. Confidence should be at least 0.8.
- "fail": the photo clearly shows the step done wrong or not done. Put the concrete correction in what_to_fix.
- "unclear": you cannot tell from this photo. Set photo_request.needed to true and describe exactly what must be visible and from where (angle and rough distance). Ask for one photo, not several.
- Judge only what is in the photo. Do not assume work happened outside the frame.
- If you see a hazard (live wiring, gas, sharp edges held carelessly), set safety_concern to true and mention it in reason.
- reason is one or two plain sentences the learner will hear read aloud."""


def verify_user_blocks(reference_block: dict | None, photo_block: dict) -> list[dict]:
    blocks: list[dict] = []
    if reference_block is not None:
        blocks.append({"type": "input_text", "text": "Expert's reference photo of this step done correctly:"})
        blocks.append(reference_block)
        blocks.append({"type": "input_text", "text": "Learner's photo, taken just now:"})
    else:
        blocks.append({"type": "input_text", "text": "Learner's photo, taken just now (no reference photo exists for this step; judge against the written expected state):"})
    blocks.append(photo_block)
    return blocks
