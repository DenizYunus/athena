# Athena

A marketplace of camera-verified skills. An expert photographs a repair and talks through it; Muse Spark compiles that into a skill with checkable steps and safety gates. Someone else installs the skill and a guide walks them through it from their phone camera or Ray-Ban Meta glasses: it answers questions from a short video clip, checks each step from a photo against the expert's reference, asks for a better angle when it cannot tell, and refuses to move past a safety gate without evidence.

Built for the Meta Global AI Developer Hackathon, Oct 27 to Nov 4, 2026. Track: Indie Developer. Problem statement: Build for Your World.

## What we built during the hackathon

Everything in this repository was created during the event. Commit history starts Oct 27, 2026.

| Date | What |
|---|---|
| Oct 27 | Research brief (`RESEARCH.md`), UI design (`design/`, 18 artboards), backend skeleton: skill schema, seed skill, push-to-talk turn (video clip + transcript → Muse Spark), snapshot verification (reference photo + user photo → structured verdict), session API, tests. |
| Oct 27 | Android app (`mobile/`, Expo): marketplace, skill detail, settings, and the push-to-talk session over the live camera: record a clip with voice, send, hear the reply; check button runs the snapshot verification. First APK on the Releases page. |
| Oct 28 | First live calls against the Meta Model API (text, image, video, structured output, SAM, realtime and batch speech). Skill compiler: photos + dictated notes → Muse Spark structured output → published skill with reference photos, gates, SAM concepts and a voice vocabulary. SAM 3.1 part highlighting on every verification, drawn over the learner's photo. Voice Transcribe: live transcript on screen while recording (WebSocket relay), and clip transcription on the server as fallback. Author screens in the app. APK v0.2.0. |

## Meta models used

| Model | Where |
|---|---|
| `muse-spark-1.3` | `backend/app/guide.py`: the push-to-talk turn (mp4 clip + transcript in, streamed spoken reply out) and the step verification call (reference photo + learner photo in, strict JSON verdict out via structured output). `backend/app/compiler.py`: the skill compiler (author photos interleaved with dictated notes in, a strict-schema skill out). |
| `sam-3.1` | `backend/app/sam.py`: runs in parallel with every verification on the learner's photo, trying the step's `sam_concepts` at once; the first hit's mask and box are drawn over the photo in the app (`mobile/src/HighlightOverlay.tsx`). Also `POST /sessions/{id}/highlight` for "where is it?". |
| `muse-voice-transcribe-1.0` | `backend/app/asr.py`: realtime WebSocket relay (`/asr`) that the phone streams 16 kHz PCM into while recording, with the skill vocabulary as keyword bias, so the words appear on screen as you talk; and the batch endpoint for the clip's audio track and for dictated author notes. |
| `muse-image-1.0` | Generated the seed skill's reference photo of an exposed compressor (`backend/skills/media/`), since no real fridge was at hand on day one. |

## What the compiler does

The expert takes photos in order and holds the mic to say what they did for each one. One Muse Spark call gets every photo interleaved with its note and returns a strict-schema skill: ordered steps, what a photo must show for each, which photo is the reference, safety gates first, common mistakes, "watch me" flags, a voice vocabulary for the recognizer, and two to four plain visual phrases per step for SAM (it finds "small black box" where "start relay" returns nothing). The skill is published at once with its photos; the author can try it, keep it, or discard it.

## Interaction model

Push to talk is the default. Tap once: the phone records a video clip and the mic. Tap again: the clip and the transcript go to the guide in one request, the reply comes back as text and is spoken by the phone. A separate check button takes one full-resolution photo and runs the strict verification against the expert's reference. Safety gates use the same verification but only a pass unlocks the next step. See `design/` for the screens and `RESEARCH.md` for the API facts behind these choices.

## Try it on a phone in three steps

1. **Backend on your Mac** (same Wi-Fi as the phone):

   ```bash
   backend/run.sh
   ```

   It prints the URL to enter in the app. The Meta API key can live in `backend/.env` (`MODEL_API_KEY=...`) or be typed into the app's Settings; the app sends it as the `X-Model-API-Key` header and the server uses it for that request.

2. **Install the APK** from the Releases page on an Android phone (allow installs from unknown sources).

3. **In the app**: Settings → Backend URL → Test connection → Save. Then open the fridge skill, Install & start, and talk to the guide: tap the big button, show and ask (your words appear as you speak), tap again to send. Tap ✓ to verify a step from a photo; on a fail or unclear verdict the part is outlined on your photo. "Teach a skill" on the home screen opens the author flow.

The backend needs `ffmpeg` on the Mac (`brew install ffmpeg`) to transcribe clips and dictated notes; Settings → Test connection says if it is missing.

## Mobile app

Expo SDK 58, React Native, TypeScript, no navigation library (six screens, one switch). `expo-camera` records the clip with audio, `expo-audio` streams raw PCM from the mic to the transcription relay (and writes the WAV for dictated notes), `react-native-svg` draws the SAM masks, `expo-speech` reads replies aloud, `expo-secure-store` keeps the backend URL and key.

```bash
cd mobile && npm install
npx expo start              # dev server; press a for an emulator or scan with Expo Go
```

Build the APK locally (needs JDK 17 and the Android SDK; `brew install openjdk@17 && brew install --cask android-commandlinetools`):

```bash
cd mobile
npx expo prebuild --platform android --no-install
echo "sdk.dir=/opt/homebrew/share/android-commandlinetools" > android/local.properties
cd android && JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home ./gradlew assembleRelease
# → android/app/build/outputs/apk/release/app-release.apk
```

The release build is signed with the debug keystore, which is fine for sideloading and demos.

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
| POST | `/sessions/{id}/verify` | Snapshot check. Multipart: `photo`. Returns the verdict (advances on pass) and the SAM highlight for the step's part; `?highlight=false` skips SAM |
| POST | `/sessions/{id}/highlight` | SAM only. Multipart: `photo`, optional `concept` |
| POST | `/sessions/{id}/skip` | Skip a non-gate step |
| WS | `/asr` | Live transcription relay. First frame `{"type":"start","skill_id":..,"api_key":..?}`, then binary 16 kHz PCM, then `{"type":"endStream"}`; events come back as Meta sends them |
| POST | `/author/transcribe` | Transcribe a dictated note. Multipart: `clip` (wav or mp4), `keywords` |
| POST | `/skills/compile` | Multipart: `photos` (ordered), `notes` (one per photo, extras allowed), `author`, `title_hint` → the published skill |
| GET | `/skills/{id}/media/{name}` | A skill's reference photo |
| DELETE | `/skills/{id}` | Remove a compiled skill (seed skills are read-only) |

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

Findings from the first live day, for anyone pointing these models at a repair: SAM 3.1 answers to plain visual phrases ("black box", "wire") and not to part names ("compressor", "start relay"); the compiler prompt is written around that. Muse Spark reads the video's frames and audio and reliably says what it cannot see. Voice Transcribe with the skill's vocabulary as keywords spelled "start relay" and "compressor" right on the first try.

## Repository layout

```
RESEARCH.md        hackathon rules, Meta API facts, build plan
design/            UI artboards (open design/canvas.json with the Design canvas, or read the .dc.html files)
backend/
  app/             FastAPI app, guide agent, verifier, SAM highlight, ASR relay, compiler, schemas
  skills/          seed skills as JSON (+ media/); compiled skills land in skills/compiled/ (gitignored)
  scripts/         smoke scripts against the live API
  tests/           pytest, no network
  run.sh           start on 0.0.0.0 and print the LAN URL
mobile/
  App.tsx          screen switch
  src/api.ts       backend client
  src/live.ts      WebSocket client for the live transcript (PCM in, transcript events out)
  src/HighlightOverlay.tsx  the learner's photo with the SAM mask and box drawn over it
  src/screens/     Marketplace, SkillDetail, Session (push to talk), AuthorCapture, AuthorReview, Settings
```

## License

MIT.
