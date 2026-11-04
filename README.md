# Athena

A marketplace of camera-verified skills. An expert films a repair while talking through it and, if there is one, adds the manual; Muse Spark watches the video, reads the manual and turns both into a skill with checkable steps, safety gates, a reference photo per step taken from the video, and a short clip of the expert doing each step. Someone else installs the skill and a guide walks them through it from their phone camera or Ray-Ban Meta glasses: it answers questions from a short video clip, checks each step from a photo against what the expert said done looks like (and the expert's reference photo where the step has one), asks for a better angle when it cannot tell, and refuses to move past a safety gate without evidence.

Built for the Meta Global AI Developer Hackathon, Oct 27 to Nov 4, 2026. Track: Indie Developer. Problem statement: Build for Your World.

## Submission

| | |
|---|---|
| Hackathon | Meta Global AI Developer Hackathon, Oct 27 – Nov 4, 2026 |
| Track · problem statement | Indie Developer · Build for Your World (skilled trades and home repair) |
| Team | Deniz Yunus Gogus ([@DenizYunus](https://github.com/DenizYunus)), Emre Can Dikmen |
| Demo video (3 min) | TODO: link after upload (storyboard and recording plan in `demo/`) |
| Code | this repository; APK on the [Releases](https://github.com/DenizYunus/athena/releases) page |
| Meta models | `muse-spark-1.3`, `sam-3.1`, `muse-voice-transcribe-1.0`, `muse-image-1.0` (table below) |
| New work | everything here was written during the event; the commit history starts Oct 27, 2026 |

**What it does, in three sentences.** An expert uploads a video of the job, narrated as they work, plus the manual if they have one; Muse Spark watches the video and reads the manual and writes a skill with ordered steps, the visual state that proves each step, safety gates (the manual's lockout and drain steps become gates even when the video skips them), the manual's specs per step, the points where video and manual disagree, and a voice vocabulary, and SAM 3.1 picks a reference frame per step from the video. A learner installs the skill and is guided from the live camera: questions are a short clip plus voice, answered in a spoken sentence; each step is proven with a photo that the verifier judges against the expert's description of the finished step, next to the expert's reference photo when the step has one, and answers with a strict JSON verdict, pass, fail with the fix, or unclear with the exact photo to take next. SAM 3.1 outlines the part on the learner's own photo, and a safety gate never opens without evidence.

**Why this is a system and not an image analyzer.** The organizers' list of discouraged projects includes "simple image analyzers". Athena's unit of work is a verification loop, not a description: the verifier must commit to one of three outcomes with a confidence and a photo request; the app refuses a hesitant pass on a safety gate; a fail carries the concrete correction and a SAM mask on the offending part; the digester turns an expert's video, narration and manual into the checkable spec that the loop runs against; and the conversation turn carries video and transcript together so the guide answers about what it sees.

**Honest notes for judges.** The Model API has no realtime video session, so verification is by photo and short clip, not a live video call. The two seed skills were written by us, and their reference photos were generated with `muse-image-1.0` (a digested skill uses frames from the expert's own video). In the seed skills 4 of the 13 steps have a reference photo; the others are checked against the written expected state. Muse Spark reads a video through a fixed budget of frames, about one per second up to roughly five minutes; we measured that a longer video is subsampled and its timestamps come back compressed, so the digester cuts anything over 4.5 minutes into parts, reads the parts in parallel and merges their steps in one more call. Muse Spark hears the narration but places it loosely in time: on a real 4:41 classroom video of a pump overhaul its step starts were off by up to 18 s, and one reference frame in five showed the neighbouring step. So the digester also asks Voice Transcribe, in its endpointing mode, for every spoken sentence with its start and end, and gives each reader of the video those lines; judged blind over seven digests of that video, step starts within 3 s went from 68% to 95%, and reference frames that show the right step from 77% to 100%. A step's reference photo is still the sharpest frame near the model's best second that SAM can find the step's part in. The digester was verified live on a narrated test video we generated, with step times we knew, and a one-page procedure, and on that classroom video with a manufacturer's 28-page pump manual; YouTube links are not accepted (the Model API takes MP4 files, and the author uploads a video they filmed or may use). The verifier, the digester and the compiler use structured output; the guide's spoken reply is plain text, and there is no function calling. Device testing: TODO after the phone run (the APK was built and every request shape was verified live against the API before any phone test).

## What we built during the hackathon

Everything in this repository was created during the event. Commit history starts Oct 27, 2026.

| Date | What |
|---|---|
| Oct 27 | Research brief (`RESEARCH.md`), UI design (`design/`, 18 artboards), backend skeleton: skill schema, seed skill, push-to-talk turn (video clip + transcript → Muse Spark), snapshot verification (reference photo + user photo → structured verdict), session API, tests. |
| Oct 27 | Android app (`mobile/`, Expo): marketplace, skill detail, settings, and the push-to-talk session over the live camera: record a clip with voice, send, hear the reply; check button runs the snapshot verification. First APK on the Releases page. |
| Oct 28 | First live calls against the Meta Model API (text, image, video, structured output, SAM, realtime and batch speech). Skill compiler: photos + dictated notes → Muse Spark structured output → published skill with reference photos, gates, SAM concepts and a voice vocabulary. SAM 3.1 part highlighting on every verification, drawn over the learner's photo. Voice Transcribe: the clip's audio track is transcribed on the server before the guide sees the clip; the transcript and the streamed reply arrive over server-sent events. Live transcription over a WebSocket relay for the author's dictated notes. Author screens in the app. Second seed skill in another domain (plumbing: cleaning a sink P-trap) with generated reference photos, verified live with pass and fail photos. APK v0.3.0. |
| Oct 29 | Ray-Ban Meta glasses source (`mobile/modules/glasses-bridge/`, Wearables Device Access Toolkit 1.0.1): glasses stream in the session, glasses photos for checks, audio push-to-talk with a glasses photo, lens cards for Meta Ray-Ban Display; verified on Meta's Mock Device Kit in an emulator, not on hardware. Found and fixed that Expo 58's `fetch` rejects file uploads on a device: all uploads now go through `XMLHttpRequest`. 51 tests. APK v0.4.0. Demo kit in `demo/`. |
| Nov 1 | Fixes from a full code review. Phone photos are turned upright from their EXIF tag before the verifier and SAM see them, so the highlight lands on the part. Tapping the mic or ✓ while the guide is talking no longer drops the recording or the check, and "Step N verified" is heard before the next step. A failed Meta call comes back as a sentence (rejected key, rate limit, timeout) instead of a bare 500. Muse Spark and SAM calls wait at most 50 s per read and retry once (the SDK default was 600 s and two retries), and a check is cut off at 100 s, so it answers before the app gives up at 120 s. Each check carries the step on screen and only that step can advance, so neither a double tap nor a late retake can carry a session past a safety gate. The guide no longer offers a spoken "done" command, which does not exist. 74 tests. |
| Nov 2 | Authoring from a video. The expert uploads a how-to video and, optionally, up to three PDFs; a background job normalizes the video with ffmpeg, uploads it and the PDFs to the Files API, has Muse Spark write the skill with where each step happens in the video, the manual's specs per step, safety gates from either source and the conflicts between them, then takes a reference frame per step (the sharpest frame SAM 3.1 can ground the part in) and a clip of the expert doing it. Live probes first: a 281 s video kept accurate timestamps, 300 s and longer did not, so videos over 270 s are read in parts and merged. On a narrated test pump video with a procedure PDF the skill came back with the PDF's lockout, valve and drain steps as gates and a planted 17 mm versus 19 mm socket conflict flagged. App: a video and PDF picker with upload and job progress, a review screen with conflicts, sources and step times, and "Watch the expert" in the session. 131 tests. APK v0.5.0. Later the same day, step timing: Voice Transcribe's endpointing mode gives each sentence of the narration its real start and end, and every reader of the video gets those lines. On a real classroom video, step starts within 3 s went from 68% to 95% (seven digests, judged blind); on the test video every step landed in the right scene in 5 of 5 runs, against 63% of steps without. 150 tests. |

## Meta models used

| Model | Where |
|---|---|
| `muse-spark-1.3` | `backend/app/guide.py`: the push-to-talk turn (mp4 clip + transcript in, streamed spoken reply out) and the step verification call (learner photo, plus the expert's reference photo when the step has one, in; strict JSON verdict out via structured output). `backend/app/digest.py`: the video digester (the expert's video, or each part of a long one, plus the PDFs through the Files API in; a strict-schema skill with step times, sources, document notes and conflicts out). `backend/app/compiler.py`: the photo compiler, kept as a second authoring path (author photos interleaved with dictated notes in, a strict-schema skill out). |
| `sam-3.1` | `backend/app/sam.py`: runs in parallel with every verification on the learner's photo, trying the step's `sam_concepts` at once; the first hit's mask and box are drawn over the photo in the app (`mobile/src/HighlightOverlay.tsx`). Also `POST /sessions/{id}/highlight` for "where is it?". When a video is digested, SAM checks each step's candidate reference frames and the one where it finds the part becomes the reference photo. |
| `muse-voice-transcribe-1.0` | `backend/app/asr.py`: in a session, the clip's audio track (pulled out with ffmpeg) goes to the batch endpoint with the skill vocabulary as keyword bias, and the transcript is sent to the guide together with the clip. For the author's dictated notes, the realtime endpoint over a WebSocket relay (`/asr`), so the words appear as they talk. For the video digester (`backend/app/digest.py`, `narration()`), the batch endpoint in its `ENDPOINTING` mode returns the narration as sentences with their start and end, in requests of at most 9 minutes of sound that overlap by 20 s. |
| `muse-image-1.0` | Generated the seed skills' reference photos (`backend/skills/media/`): the exposed compressor for the fridge skill, and the bucket, the removed trap and the dry reinstalled trap for the sink skill. The generator also drew a leaking joint by accident; that image is the fail test photo. |

## What the digester does

The expert picks a video of the job (filmed while talking through it) and, optionally, the manual as PDF, and taps Digest. The backend answers at once with a job and the app shows its progress:

1. **Preparing.** ffmpeg makes a small H.264 copy (long side 1280 px, at most 30 fps, HDR tone-mapped to SDR) and, for a video over 270 s, cuts it into equal parts.
2. **Uploading.** The parts and the PDFs go to the Files API; every uploaded file is deleted when the reading ends. Meanwhile Voice Transcribe splits the narration into sentences, each with its start and end; a silent video has none, and a failed request only means the readers go without.
3. **Reading.** A video of one part goes to Muse Spark with the PDFs in one call. Several parts are read in parallel, each reporting its steps with times inside the part, which the backend turns into times in the whole video; one more call merges them with the PDFs. Every reader of the video also gets the sentences said in what it watches, with their times counted the same way, so it places each step on the moment the expert announces it rather than guessing from the frames. Either way the answer is a strict-schema skill: ordered steps, what a photo must show for each, start, end and best second in the video, safety gates first, the source of each step (video, manual, both, or a gate neither covers but the task needs), the manual's specs and cautions per step, and the conflicts between video and manual with what the skill uses.
4. **Frames.** For each step the sharpest of three frames around the best second is grounded with SAM; if SAM finds none of the step's visual phrases, later frames in the step are tried. The chosen frame is the reference photo, and a clip of up to 30 s from the step's start is cut for the learner.
5. **Publishing.** The skill appears in the marketplace; the review screen shows the conflicts, each step's source and time, and plays its clip.

The photo compiler is still there as "Photograph the steps instead". The expert takes photos in order and holds the mic to say what they did for each one. One Muse Spark call gets every photo interleaved with its note and returns a strict-schema skill: ordered steps, what a photo must show for each, which photo is the reference, safety gates first, common mistakes, "watch me" flags, a voice vocabulary for the recognizer, and two to four plain visual phrases per step for SAM (it finds "small black box" where "start relay" returns nothing). The skill is published at once with its photos; the author can try it, keep it, or discard it.

## Interaction model

Push to talk is the default. Tap once: the phone records a video clip and the mic. Tap again: the clip goes to the backend, which transcribes the audio track first and only then asks the guide, so the words and the video travel together. The phone reads the response as a stream: your transcript lands in your bubble, then the reply streams into the guide's bubble and is spoken. Nothing else touches the microphone while the camera records. A separate check button takes one full-resolution photo and runs the strict verification against the step's expected state and, when the step has one, the expert's reference photo. Safety gates use the same verification but only a pass unlocks the next step. In a skill learned from a video, the steps sheet has "Watch the expert", which plays the expert's clip of the current step, and shows the manual's note for it. See `design/` for the screens and `RESEARCH.md` for the API facts behind these choices.

## Try it on a phone in three steps

1. **Backend on your Mac** (same Wi-Fi as the phone):

   ```bash
   backend/run.sh
   ```

   It prints the URL to enter in the app. The Meta API key can live in `backend/.env` (`MODEL_API_KEY=...`) or be typed into the app's Settings; the app sends it as the `X-Model-API-Key` header and the server uses it for that request.

   For a phone on another network, run `backend/share.sh` instead (`brew install cloudflared` first). It starts the backend behind a Cloudflare quick tunnel and prints an https URL that works from anywhere while the Mac is awake and online, with no account or port forwarding. The URL changes every time it starts. Streamed replies, checks and the live transcript all work through it. A video upload has to finish within a few minutes: 42 MB went through and 95 MB did not, so keep videos short.

2. **Install the APK** from the Releases page on an Android phone (allow installs from unknown sources).

3. **In the app**: Settings → Backend URL → Test connection → Save. Then open a skill (the fridge relay, or the sink P-trap, which any home can try without parts), Install & start, and talk to the guide: tap the big button, show and ask, tap again to send. Your words appear once transcribed, then the reply streams in. Tap ✓ to verify a step from a photo; on a fail or unclear verdict the part is outlined on your photo. "Teach a skill" on the home screen opens the video upload: pick a video, optionally a PDF manual, and tap Digest.

The backend needs `ffmpeg` on the Mac (`brew install ffmpeg`) to digest videos and to transcribe clips and dictated notes; Settings → Test connection says if it is missing.

## Mobile app

Expo SDK 58, React Native, TypeScript, no navigation library (seven screens, one switch). `expo-document-picker` picks the expert's video and manuals, `expo-video` plays the expert's step clips, `expo-camera` records the clip with audio, `expo-audio` streams raw PCM from the mic to the transcription relay while the author dictates a note (and writes the WAV as fallback), `react-native-svg` draws the SAM masks, `expo-speech` reads replies aloud, `expo-secure-store` keeps the backend URL, the key and the glasses source. Multipart uploads go through `XMLHttpRequest`: the `fetch` that Expo SDK 58 installs globally rejects React Native's `{ uri, name, type }` file parts ("Unsupported FormDataPart implementation"), so photo and clip uploads through `fetch` fail on a device.

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

## Glasses

The session can take its frames from Ray-Ban Meta glasses instead of the phone camera, through the [Meta Wearables Device Access Toolkit](https://wearables.developer.meta.com/docs/getting-started-toolkit) 1.0.1 (Android only). The bridge is an Expo local module in `mobile/modules/glasses-bridge/` (Kotlin, Expo Modules API): it registers the app with Meta AI, opens a device session, streams the glasses camera at 720x1280 (the toolkit's own quality ladder may step down to 504x896 or 360x640 over Bluetooth), takes photos with `capturePhoto()`, and pushes 600x600 cards to the lens display when the glasses have one. Its config plugin adds the toolkit's manifest entries (`APPLICATION_ID` / `CLIENT_TOKEN`, both `0` in Developer Mode, and the `athena://` callback scheme), so `npx expo prebuild --platform android --no-install --clean` produces a building project.

In a session, tap **Glasses** next to the Live pill. The preview behind the chat bubbles switches to the glasses stream, the check button sends a glasses photo to the verifier (the SAM highlight is drawn on that photo), and push-to-talk records your question as an audio file with `expo-audio` and sends it with a glasses photo. The backend transcribes an audio-only clip and does not forward it to the guide as video (a live probe showed Muse Spark accepts an audio-only `input_video` but ignores the sound, answering that it "hears complete silence"). Because the audio never reaches the guide, a glasses turn whose audio cannot be transcribed, or where nothing was heard and no photo came with it, fails with a message instead of reaching the guide empty. With the toggle off nothing changes: the phone camera records the clip and takes the photo as before.

**What works on the Mock Device Kit** (Settings → Glasses → Mock Device Kit, the default; no hardware, no Meta AI app): registration and permissions are simulated, the phone's back camera stands in for the glasses camera and streams through the toolkit's real encode/decode path, `capturePhoto()` returns the mock's configured image or, failing that, the latest stream frame, and the push-to-talk and check flows run end to end against the backend. Verified on an Android 16 (API 36) emulator whose virtual-scene camera fed the mock: the pill reads "Glasses ✓" within about a second, the preview shows the decoded I420 stream (480x640 there, the emulator camera's own size; the mock streams the phone camera at its native resolution, while real glasses negotiate the 720x1280 the module asks for), the check button's glasses photo came back with an "unclear" verdict for the virtual living room, and a push-to-talk turn sent the audio file plus a glasses photo and got a spoken reply about the photo. Each time the toolkit reads its configuration it logs `Failed to read metadata from manifest: …APPLICATION_ID, the app might be misconfigured` and the same line for `CLIENT_TOKEN` (two pairs per connect on the emulator): that is its reaction to the Developer Mode value `0` and is expected without a published integration. The default mock pairs Ray-Ban Meta glasses, which have no display, so no cards are sent. Passing `mockModel: "meta_rayban_display"` to the bridge pairs the mock as Meta Ray-Ban Display glasses instead: in a test build with that option the mock display acknowledged every card (the step card on connect, "Checking…" and the verdict during a check, "Listening…" during a question, and the step card again after the reply). The cards are not drawn anywhere on the phone; only their delivery was checked.

**What needs hardware**: real Ray-Ban Meta glasses (Gen 1/2, Oakley Meta HSTN/Vanguard, Meta Ray-Ban Display) paired to the Meta AI app **V292 or newer** with glasses firmware **V128 or newer**, Developer Mode on in Meta AI (Settings → App Info, tap App Version five times), Bluetooth permission on the phone, and Android 10+ (the toolkit libraries declare minSdk 29, so the app's minSdk is 29). With Settings switched to real glasses, the emulator (no Meta AI app) shows the registration step failing at once with a message to install Meta AI, so the real code path runs up to the hand-off to Meta AI. Untested here for lack of glasses: the registration round trip through Meta AI, the camera permission prompt, the decoded frame layout on hardware HEVC decoders (the module assumes the planar I420 the toolkit asks its decoder for; `yuv: "nv12"` is a one-line switch), the glasses microphone (the question is recorded with the phone mic; routing the glasses mic needs Bluetooth SCO), and the lens cards. Full toolkit access is limited to the countries where the glasses are sold.

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
| POST | `/sessions/{id}/turn` | Push-to-talk turn. Multipart: `transcript` (text), `clip` (mp4, or an audio-only m4a/wav from a glasses turn, optional), `photo` (jpeg, optional). With a clip and no transcript the audio track is transcribed first; an audio-only clip is transcribed and not sent to the guide as video (a failed transcription returns 502, and no words with no photo returns 422; as SSE `error` events when streaming). `?stream=true` returns server-sent events: `transcribing`, `transcript`, `delta`…, `done` (or `error`) |
| POST | `/sessions/{id}/verify` | Snapshot check. Multipart: `photo`, optional `step` (the step on the app's screen; 409 if the session has already left it). Returns the verdict (advances on pass, and only for the step that was judged) and the SAM highlight for the step's part; `?highlight=false` skips SAM. A failed Meta call returns 502 (504 when it runs past 100 s) with a sentence the app shows; a photo that is not an image returns 422 |
| POST | `/sessions/{id}/highlight` | SAM only. Multipart: `photo`, optional `concept` |
| POST | `/sessions/{id}/skip` | Skip a non-gate step |
| WS | `/asr` | Live transcription relay. First frame `{"type":"start","skill_id":..,"api_key":..?}`, then binary 16 kHz PCM, then `{"type":"endStream"}`; events come back as Meta sends them |
| POST | `/author/transcribe` | Transcribe a dictated note. Multipart: `clip` (wav or mp4), `keywords` |
| POST | `/skills/digest` | Multipart: `video` (MP4, MOV, WebM and other files ffmpeg reads; up to 1 GB and 60 minutes), `documents` (up to three PDFs, 50 MB each, optional), `author`, `title_hint` → 202 with a digest job. Bad input answers 413 or 422 with a sentence |
| GET | `/skills/digest/{job_id}` | The job: `status` (running, done, failed), `stage`, `message`, `progress`, parts read, and the published skill when done or the reason when failed |
| POST | `/skills/compile` | Multipart: `photos` (ordered), `notes` (one per photo, extras allowed), `author`, `title_hint` → the published skill |
| GET | `/skills/{id}/media/{name}` | A skill's reference photo (JPEG) or step clip (MP4, with Range requests) |
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

Findings from the first live day, for anyone pointing these models at a repair: SAM 3.1 answers to plain visual phrases ("black box", "wire") and not to part names ("compressor", "start relay"); the compiler prompt is written around that. Muse Spark reads the video's frames and audio and reliably says what it cannot see. Voice Transcribe with the skill's vocabulary as keywords spelled "start relay" and "compressor" right on the first try. The verifier, given the sink skill, passed the bucket-under-trap and dry-joints photos, failed a photo of a leaking joint with the exact nut to tighten, and answered a fridge photo on a sink step with "not under a bathroom sink".

## Repository layout

```
RESEARCH.md        hackathon rules, Meta API facts, build plan
demo/              storyboard, recording plan, submission form text
design/            UI artboards (open design/canvas.json with the Design canvas, or read the .dc.html files)
backend/
  app/             FastAPI app, guide agent, verifier, SAM highlight, ASR relay, video digester, photo compiler, schemas
  skills/          seed skills as JSON (fridge-start-relay, sink-p-trap) with media/; compiled skills land in skills/compiled/ (gitignored)
  scripts/         smoke scripts against the live API
  tests/           pytest, no network
  run.sh           start on 0.0.0.0 and print the LAN URL
  share.sh         start behind a Cloudflare quick tunnel and print a public https URL
  Dockerfile       the backend as one container, with a static ffmpeg 8, for a host that runs images
  requirements.lock  the exact package versions the tests pass with, used by the image
mobile/
  App.tsx          screen switch
  src/api.ts       backend client
  src/live.ts      WebSocket client for the live transcript (PCM in, transcript events out)
  src/glasses.ts   the Glasses toggle: a hook over the native bridge (connect, frames, capturePhoto, lens cards)
  modules/glasses-bridge/  Expo local module (Kotlin) wrapping the Wearables Device Access Toolkit, plus its config plugin
  src/HighlightOverlay.tsx  the learner's photo with the SAM mask and box drawn over it
  src/ClipPlayer.tsx        the expert's step clip in a modal player
  src/screens/     Marketplace, SkillDetail, Session (push to talk), AuthorImport (video and manual), AuthorCapture (photos), AuthorReview, Settings
```

## License

MIT.
