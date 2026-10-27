# Athena

A marketplace of camera-verified skills. An expert photographs a repair and talks through it; Muse Spark compiles that into a skill with checkable steps and safety gates. Someone else installs the skill and a guide walks them through it from their phone camera or Ray-Ban Meta glasses: it answers questions from a short video clip, checks each step from a photo against the expert's reference, asks for a better angle when it cannot tell, and refuses to move past a safety gate without evidence.

Built for the Meta Global AI Developer Hackathon, Oct 27 to Nov 4, 2026. Track: Indie Developer. Problem statement: Build for Your World.

## What we built during the hackathon

Everything in this repository was created during the event. Commit history starts Oct 27, 2026.

| Date | What |
|---|---|
| Oct 27 | Research brief (`RESEARCH.md`), UI design (`design/`, 18 artboards), backend skeleton: skill schema, seed skill, push-to-talk turn (video clip + transcript → Muse Spark), snapshot verification (reference photo + user photo → structured verdict), session API, tests. |

## Meta models used

| Model | Where |
|---|---|
| `muse-spark-1.3` | `backend/app/guide.py`: the push-to-talk turn (mp4 clip + transcript in, streamed spoken reply out) and the step verification call (two images in, strict JSON verdict out via structured output). Later: the skill compiler. |
| `sam-3.1` | Planned: part masks on fail verdicts (`sam_concepts` on each step are already in the schema). |
| `muse-voice-transcribe-1.0` | Planned: realtime transcription on the phone while the user records, so the transcript is ready when they tap send. |

## Interaction model

Push to talk is the default. Tap once: the phone records a video clip and the mic. Tap again: the clip and the transcript go to the guide in one request, the reply comes back as text and is spoken by the phone. A separate check button takes one full-resolution photo and runs the strict verification against the expert's reference. Safety gates use the same verification but only a pass unlocks the next step. See `design/` for the screens and `RESEARCH.md` for the API facts behind these choices.

## Backend

Python 3.11+, FastAPI, the `openai` SDK pointed at `https://api.meta.ai/v1`.

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # put your MODEL_API_KEY in it
uvicorn app.main:app --reload
```

Endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/skills` | List skills in the marketplace |
| GET | `/skills/{id}` | One skill with all steps |
| POST | `/sessions` | Start a session `{skill_id, mode}` (mode defaults to `ptt`) |
| GET | `/sessions/{id}` | Session state, current step, transcript, verdicts |
| POST | `/sessions/{id}/turn` | Push-to-talk turn. Multipart: `transcript` (text), `clip` (mp4, optional), `photo` (jpeg, optional). `?stream=true` returns server-sent text deltas |
| POST | `/sessions/{id}/verify` | Snapshot check. Multipart: `photo`. Returns the verdict and advances on pass |
| POST | `/sessions/{id}/skip` | Skip a non-gate step |

Tests run without an API key (the Meta client is faked):

```bash
cd backend && pytest
```

Smoke scripts hit the real API and need `MODEL_API_KEY`:

```bash
cd backend
python scripts/smoke_verify.py --skill fridge-start-relay --step 1 --photo path/to/photo.jpg
python scripts/smoke_turn.py --skill fridge-start-relay --step 4 --transcript "is this the relay?" --clip path/to/clip.mp4
```

## Repository layout

```
RESEARCH.md        hackathon rules, Meta API facts, build plan
design/            UI artboards (open design/canvas.json with the Design canvas, or read the .dc.html files)
backend/
  app/             FastAPI app, guide agent, schemas, media helpers
  skills/          seed skills as JSON
  scripts/         smoke scripts against the live API
  tests/           pytest, no network
```

## License

MIT.
