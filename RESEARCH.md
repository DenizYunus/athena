# Athena — Research Brief

Meta Global AI Developer Hackathon · building period Oct 27 – Nov 4, 2026 · submit by Wed Nov 4, 9:00 PM ET.
Researched Oct 27, 2026 against dev.meta.ai docs, the meta-model-cookbook repo, and the Wearables Device Access Toolkit docs.

## 1. The idea in one paragraph

Athena is a marketplace of **procedural skills**. An expert (a refrigerator mechanic) photographs a repair and writes rough instructions. Muse Spark compiles that into a structured skill: ordered steps, the visual state that proves each step is done, safety gates, tools needed. A non-expert installs the skill and is walked through it by a guide agent that sees what they see (phone camera, or Ray-Ban Meta glasses), tells them what to do, checks each step from a photo, asks for a better angle when it cannot tell, and refuses to advance past a safety gate until the evidence is there. The expert gets paid per completion.

## 2. Fit with the hackathon

**Track:** Indie Developer (unless the team is an incorporated startup).
**Problem statement:** Build for Your World. The "domain you know" is skilled trades and field repair. It also brushes Push the Frontier (giving a machine a capability: verifying physical-world state from images), but Build for Your World is the safer, cleaner claim.

**Bonus prizes we can plausibly target without extra scope:**

| Prize | Why we qualify |
|---|---|
| Best Agentic Use-Case ($5K) | The guide is a multi-step agent with tools (request_photo, verify_step, flag_safety, escalate_to_pro) that decides on its own when a step is done. |
| Best Build on Meta Open Weight Models ($10K) | Optional: run Muse Glimmer (Apache 2.0, 30B, runs on a Mac) as the offline guide for basements with no signal. Only do this if days 6–7 are free. |
| Most Viral / Community Vote | Glasses demo footage is the hook. Post the demo on X. |

**Anti-project check.** The banned list includes "Simple Image Analyzers" and "Basic RAG Applications". Athena is at risk of looking like one if the demo is "send photo, get description". The demo must foreground three things: (1) an expert's photos become a structured, verifiable skill, (2) the agent rejects a step and asks for a second angle, (3) the agent refuses to advance past a safety gate. Avoid any health, medical, or nutrition framing even as a joke in the video.

**Rules we must satisfy.** Public repo from day one. Commit history starting Oct 27 is our proof of "new work only". Three-minute video. Everyone on the submission. Each team member claims their own $150 credits (form in the participant guide) and needs a card on file for the preauthorization.

## 3. Meta Model API facts that shape the product

Base URL `https://api.meta.ai/v1`, bearer auth with `MODEL_API_KEY`, OpenAI-compatible. Use the `openai` SDK (Python or TypeScript). The cookbook says the old official Meta client package is stale; do not use it. Prefer the Responses endpoint (`/v1/responses`); Chat Completions also works.

### Models we will use

| Model id | Role in Athena | What matters |
|---|---|---|
| `muse-spark-1.3` | Skill compiler + guide agent | Text, image, video, PDF in. Up to 50 images per request, so it can compare the expert's reference photo against the learner's photo in one turn. Tool calling with parallel calls, structured output via JSON schema (constrained decoding), streaming, 1M context, reasoning effort knob. $1.25 / $4.25 per M tokens. A 1280 px image costs ~1,300–1,500 input tokens. |
| `muse-spark-1.3-contributor` | Dev and testing only | Same model, $0.10 / $0.20 per M tokens, but Meta may train on the data. Fine for development, switch to standard for the demo and anything with real users' photos. 150 RPM limit. |
| `sam-3.1` | Part highlighting | Text concept prompt ("compressor relay") on an image or mp4 → boxes and masks for every match, streamed in a token format decoded by `meta_sam_parser` (Python) or `@meta-sam/parser` (TS). Use short noun phrases, no articles, one concept per call. Tracks up to 16 objects per frame in video. $2.50 per 1k images. |
| `muse-voice-transcribe-1.0` | Hands-free voice | Realtime WebSocket `wss://api.meta.ai/v1/asr/realtime`, 16 or 24 kHz mono PCM, modes PUSH_TO_TALK / ENDPOINTING / DIARIZATION, `keywords` list for vocabulary biasing (feed it the skill's part names). Speech-to-text only. $0.18 per hour. |
| `muse-image-1.0` | Optional: illustrate a step | Generates or edits images from text plus reference images via Responses. $0.01 per image. Could render an "expected result" picture when the expert skipped a photo. Nice-to-have, not core. |
| `muse-glimmer` (open weights) | Optional: offline guide | 30B dense, text + image, Apache 2.0, runs via Ollama / llama.cpp on a Mac. Not served on the Model API. Only for the open-weight bonus prize. |

Not used: `muse-wonder-1.0` is a coding/agent model at 3× the price with no capability we need. `muse-spark-open` (816B) needs an 8-GPU node.

### The "video chat" question

There is **no realtime video session** on the Model API. Video is offline: upload an mp4 (inline up to 50 MB, Files API up to 1 GiB) and the model reads frames plus the audio track. Audio input is only realtime for transcription.

So the product should not promise a live video call with the AI. The honest and still impressive design is **snapshot verification**: the camera is always on, the app captures a frame when the user says "done" or taps, or every few seconds during a "watch me" step, and sends one or a few frames to Muse Spark. For tricky steps, record a 5–10 s clip and send the mp4. This matches the cookbook's own VisionClaw glasses recipe, which sends one high-res JPEG per spoken question.

### Request shapes we will rely on

Multi-image compare, Responses endpoint:

```json
{"model": "muse-spark-1.3",
 "input": [
   {"role": "system", "content": "<guide system prompt + compiled skill JSON>"},
   {"type": "message", "role": "user", "content": [
     {"type": "input_text", "text": "Reference photo of step 4 done correctly:"},
     {"type": "input_image", "image_url": "<expert reference, data URL or file_id>"},
     {"type": "input_text", "text": "User's current photo:"},
     {"type": "input_image", "image_url": "<learner photo>"}
   ]}
 ],
 "tools": [ {"type": "function", "name": "verify_step", ...},
            {"type": "function", "name": "request_photo", ...},
            {"type": "function", "name": "flag_safety", ...} ],
 "text": {"format": {"type": "json_schema", "name": "StepVerdict", "strict": true, "schema": {...}}},
 "stream": true}
```

Notes: `tool_choice` only accepts `"auto"`. `strict: true` requires `additionalProperties: false` and every property in `required`. Recursive schemas are rejected. Images are allowed only in user messages. Coordinates from Muse Spark grounding come back on a 0–1000 normalized grid when you ask for them in the prompt.

SAM 3.1 segmentation:

```json
{"model": "sam-3.1", "stream": true,
 "input": [{"type": "message", "role": "user", "content": [
   {"type": "input_text", "text": "compressor relay"},
   {"type": "input_image", "image_url": "<data URL>"}]}],
 "metadata": {"include_confidence": "true", "mask_encoding": "one_bit"}}
```

Decode with `parse_responses_stream(events, image_segmentation_format()).final_result()` and `decode_mask_to_svg_path(record.mask)` to draw the overlay.

### Rate limits and budget

Muse Spark 1.3: 3,000 RPM / 4M TPM per team. SAM: 3,000 RPM. A verification turn with the skill context cached plus two 1280 px images is roughly 5k input tokens and 300 output tokens, so about $0.008. The $150 credit covers tens of thousands of turns. Resize images to 1280 px longest side, JPEG quality 85 (the perception-grounding recipe does exactly this).

## 4. Ray-Ban Meta glasses

**Wearables Device Access Toolkit 1.0** (iOS 17.2+ / Android 10+; needs Meta AI app V292 and glasses firmware V128). Supports Ray-Ban Meta Gen 1/2, Oakley Meta HSTN and Vanguard, and Meta Ray-Ban Display.

What the phone app gets:
- Camera stream at 360×640, 504×896, or 720×1280 at 2–30 fps over Bluetooth Classic, with an automatic quality ladder. Plus `capturePhoto()` during a stream for a full-res JPEG.
- Mic and open-ear audio through normal Bluetooth profiles. So Voice Transcribe input and spoken guidance output work with no special API.
- Developer Mode in the Meta AI app (tap App Version five times) lets unpublished apps register. App Store publishing is not yet supported; distribution is an invite-only beta channel. Fine for a hackathon.
- **Mock Device Kit** simulates registration, permissions, camera stream (from the phone camera or an h265 file), photo capture, and touchpad taps, so the glasses path can be built without hardware. It does not simulate the display.

**Meta Ray-Ban Display** (the glasses with the in-lens screen):
- 600×600 px private display in the right lens.
- Native path: the phone app builds a layout from FlexBox, Text, Image (URL), Button, Icon, and Video components, serializes it, and sends it over Bluetooth. Each send replaces the whole view. The glasses hold no state.
- Input: touchpad and Meta Neural Band EMG gestures, delivered as events. No custom "Hey Meta" voice commands.
- Alternative Web Apps path: plain HTML/CSS/JS running on the glasses, with a browser simulator.

Design consequence: the glasses UI is a sequence of **600×600 cards** driven from the phone. One card per step: a short instruction line, a step counter, an optional reference image or SAM-highlighted crop, and a one-word status. Voice carries everything else.

Caveat: full toolkit access is limited to "AI glasses supported countries". Check before promising the glasses demo live; the mobile app must stand on its own.

## 5. Architecture for 8 days

```
Mobile app (Expo / React Native, iOS first)
  ├─ Marketplace, skill detail, install
  ├─ Author flow: capture photos, dictate notes → POST /skills/compile
  ├─ Session: camera, voice (WebSocket to ASR), step cards, verdict overlays
  └─ Glasses bridge (native module, DAT): frame source + display cards

Backend (FastAPI, Python)
  ├─ /skills/compile   → muse-spark-1.3 structured output → Skill JSON
  ├─ /skills/*/annotate → sam-3.1 masks on author photos, stored as SVG paths
  ├─ /sessions/*/turn  → guide agent loop (tools: verify_step, request_photo, flag_safety, escalate)
  ├─ /asr proxy        → muse-voice-transcribe realtime, keywords = skill vocabulary
  └─ Postgres + S3-compatible storage (skills, images, session logs)
```

Skill JSON (what the compiler produces):

```json
{"id": "fridge-start-relay-replace", "title": "Replace a refrigerator start relay",
 "domain": "appliance", "difficulty": 2, "est_minutes": 25,
 "tools": ["#2 Phillips", "multimeter"], "parts": ["start relay (match model)"],
 "safety_gates": [{"before_step": 1, "requirement": "Fridge unplugged", "evidence": "photo of plug out of outlet"}],
 "steps": [
   {"n": 1, "instruction": "Pull the fridge out and unplug it.",
    "expected_state": "Power cord visibly disconnected from wall outlet",
    "reference_image": "img_01", "sam_concepts": ["power plug"],
    "verify_hints": "Reject if the cord is still in the outlet or the plug is out of frame.",
    "common_mistakes": ["Switching off at the thermostat instead of unplugging"]},
   ...
 ]}
```

Guide agent loop per turn: user photo (+ optional clip or voice text) → Muse Spark with the skill JSON cached in the system prompt and the step's reference image → structured verdict `{status: pass | fail | unclear, confidence, reason, next_action, photo_request?: {what, angle}}` → UI renders pass (advance), fail (show what's wrong, overlay SAM mask on the part), or unclear (ask for a specific second photo). Safety gates use the same loop but `pass` is the only way forward.

## 6. Build plan

| Day | Deliverable |
|---|---|
| Oct 27 (Tue) | Public repo, credits claimed, design locked, backend skeleton with one working Muse Spark multi-image verify call. |
| Oct 28 | Skill compiler (photos + notes → Skill JSON) and the guide loop with structured verdicts. Test on one real fridge skill. |
| Oct 29 | Mobile app: marketplace list, skill detail, session screen with camera and verdict UI. |
| Oct 30 | Voice: realtime transcription with keyword bias; "done" and questions hands-free. SAM overlays on fail/unclear. |
| Oct 31 | Author flow in the app. Second skill in a different domain (bike brake, router, espresso machine) to prove generality. |
| Nov 1 | Glasses: DAT frame source with Mock Device Kit; Display cards if hardware is available. |
| Nov 2 | Polish, error handling, safety gate UX, seed marketplace with 4–6 skills. |
| Nov 3 | Record the 3-minute video. Buffer day. |
| Nov 4 | README with "what we built" section, submit before 9 PM ET. |

## 7. Demo video storyboard (3:00)

0:00–0:20 The problem: an expert's know-how is locked in their head; a learner with a dead fridge and a phone.
0:20–0:50 Author: mechanic snaps six photos, dictates notes, taps Compile. Skill appears with steps, safety gate, SAM-highlighted parts.
0:50–1:00 Marketplace: learner installs it.
1:00–2:10 Session: safety gate refuses until the plug is visibly out. Step 3 verdict "unclear", agent asks for a photo from below. Step 4 fail: wrong relay orientation, SAM mask shows the tab. Fix, pass, next.
2:10–2:40 Glasses: same session hands-free, cards in the lens, voice "done".
2:40–3:00 Which Meta models did what, what was built this week, repo link.

## 8. Open risks

- **Verification accuracy** on real photos is the whole product. Spend day 2 on prompt and schema design, and keep a small eval set of pass/fail/unclear photos.
- **Glasses hardware** may be unavailable or region-locked. Mock Device Kit covers the camera path; the Display path needs real hardware.
- **Audio in Muse Spark 1.3** is "not fully supported" per the docs; do not send audio to Spark, use Voice Transcribe and send text.
- **Safety liability.** Gate electrical and gas steps, show a "call a pro" escalation, and keep the demo on low-risk repairs.

## Sources

- https://dev.meta.ai/docs/models · https://dev.meta.ai/docs/image-understanding · https://dev.meta.ai/docs/video-understanding
- https://dev.meta.ai/docs/structured-output · https://dev.meta.ai/docs/tool-calling · https://dev.meta.ai/docs/file-handling
- https://dev.meta.ai/docs/sam/segmenting · https://dev.meta.ai/docs/speech-to-text/ · https://dev.meta.ai/docs/pricing-rate-limits
- https://dev.meta.ai/models/muse-spark · https://dev.meta.ai/models/sam-3-1 · https://dev.meta.ai/models/muse-glimmer
- https://github.com/meta-models/meta-model-cookbook (03_visionclaw, 10_perception_grounding, 07_sam)
- https://wearables.developer.meta.com/docs/getting-started-toolkit · https://wearables.developer.meta.com/docs/develop/dat/display-overview/
- https://developers.meta.com/wearables/faq · https://developers.meta.com/blog/build-for-display-glasses/
