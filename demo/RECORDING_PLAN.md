# Athena demo video · recording plan

Companion to `STORYBOARD.md`. Budget: about two and a half hours to capture, two hours to cut. Record everything on Nov 3 at the latest; Nov 4 is for the upload, the README link and the submission form.

## Output

- One MP4, H.264, 1920×1080, 30 fps, stereo AAC, 2:55 long. The limit is 3:00.
- Phone recordings are portrait (the phone's native resolution). In the edit they sit at full height on the dark background; never stretch them.

## Tools

| Need | Tool | Notes |
|---|---|---|
| Phone screen + phone audio to the Mac | `scrcpy` | `brew install scrcpy`. Needs `adb` on the PATH: `export PATH="/opt/homebrew/share/android-commandlinetools/platform-tools:$PATH"`. Audio forwarding needs Android 11 or newer; on an older phone use the built-in recorder instead. |
| Fallback recorder | Android's built-in Screen record (Quick Settings tile) | Set *Record audio: device audio and microphone*. Files land in `Movies/` or `Pictures/Screen recordings/`; pull them with `adb pull`. |
| Over-the-shoulder shots | A second phone, or the Mac's camera in QuickTime (File → New Movie Recording) | Landscape, 1080p, steady. |
| Narration | QuickTime (File → New Audio Recording) or Voice Memos | Quiet room, phone on silent, mouth a hand's width from the mic. |
| Edit | DaVinci Resolve (free) or iMovie | iMovie is enough: three tracks (phone clips, narration, title cards). |
| Title cards and lower thirds | Keynote or Figma exported as PNG with transparent background | Font Inter, model ids in a monospace font, amber `#F5B82E` on `#0F1115`. |

Record each clip with its own command so takes stay separate:

```bash
export PATH="/opt/homebrew/share/android-commandlinetools/platform-tools:$PATH"
adb devices                      # the phone must show as "device", not "unauthorized"
mkdir -p demo/clips
scrcpy --record=demo/clips/C1_gate_refused_take1.mp4 --max-size=1920 --video-bit-rate=16M --max-fps=30
```

Close the scrcpy window or press Ctrl+C to stop. If the phone cannot forward audio, add `--no-audio` and use the built-in recorder for the scenes where the guide's voice matters (C3).

`demo/clips/` is gitignored. Keep every take; name them `<clip id>_<what>_take<n>.mp4`.

## Pre-flight, the evening before

1. Backend: `backend/run.sh` in a terminal that stays open. Note the URL it prints. `ffmpeg` must be installed (`brew install ffmpeg`); the app's Settings → Test connection says if it is missing.
2. Phone: install `athena-0.3.0.apk` from the Releases page fresh (uninstall the old build first so no test skills or stale settings remain). Settings → backend URL → Test connection → Save. Same Wi-Fi as the Mac.
3. Walk through the whole sink skill once, off camera, with the real cabinet. This warms the prompt cache, grants the camera and microphone permissions so no permission dialogs appear on camera, and tells you how the verifier reacts to your cabinet.
4. Delete any skills compiled during testing so the marketplace shows only the two seed skills plus the one you compile on camera:

   ```bash
   backend/.venv/bin/python demo/reset_skills.py http://<mac-ip>:8000
   ```

5. Phone settings for a clean recording: Do Not Disturb on, notifications off, auto-rotate off, brightness 100 %, media volume max (the guide's voice is media audio), battery above 50 %, system font size default, no dark/light auto switch. Clear recent apps.
6. Text-to-speech: Settings → System → Languages → Text-to-speech, English (US) voice, speech rate normal. Play a test from any screen.
7. Props ready (next section). Clean the cabinet floor; the camera sees everything.

## Props

**Sink skill (scenes C1–C4):** an orange or any bright bucket, a towel, an old toothbrush, slip-joint pliers in frame but unused, the cabinet emptied except for what the gate needs. Decide in advance which slip nut you will leave tight for the fail scene.

**Fridge skill (scenes D1–D2):** reach to the fridge's plug and outlet. You do not need to open the back panel; only step 1 is filmed. If the plug cannot be reached, drop the scene (see the storyboard's fallbacks).

**Teach a skill (scenes A1–A2):** a three-to-five photo job that is not the sink. Pick whatever is at hand: descale the kettle, swap the vacuum cleaner filter, reset the router, change a bike tube, replace a shower head. Take the photos in order of the work. Dictate one sentence per photo, naming the parts the way a tradesperson would; that is what becomes the voice vocabulary. Type a title hint (the *Aa* button) such as "replace vacuum filter" so the compiled title is predictable. Run one compile off camera first; if the result is odd, re-shoot the photos closer and compile again.

## Clip list

Record in this order. Minimum takes: two per clip. Each row says what must be visible in the frame for the storyboard to work.

| Clip | Scene | Do this | Must be visible | Target length |
|---|---|---|---|---|
| F1 | Cold open | Second camera: open the sink cabinet, water standing in the basin. | Hands, cabinet, standing water. | 6 s |
| F2 | Cold open | Second camera: the fridge pulled a little from the wall, or just its plug and outlet. | Fridge and cord. | 5 s |
| A1 | Teach a skill | scrcpy. Home → Teach a skill → three or four photos → hold the mic on the selected photo and speak one sentence → release. Repeat for each photo. | Thumbnails filling the strip; the live transcript appearing while you speak; the green badge when a note is attached. | 25 s |
| A2 | Compile and review | Continue A1: tap Compile, wait (hint reads "Muse Spark is writing…"), then scroll the review screen slowly top to bottom, pause two seconds on the gate card and on a step with a thumbnail. Tap Keep. | Review screen with title, chips, steps, reference thumbnails. | 30 s |
| B1 | Marketplace | scrcpy. Home with the new skill marked *yours*, scroll, open the sink skill, scroll the detail page, tap Install & start. | *yours* chip; gate card; Install & start. | 15 s |
| C1 | Gate refused | Session on step 1 with no bucket. Let the guide finish speaking. Point at the empty cabinet, tap ✓. Wait for the verdict. Open Steps, show the sheet, close it. | Verdict bubble with the photo request or fix; Steps sheet without a Skip button. | 25 s |
| C2 | Gate passed | Put the bucket under the trap. Tap ✓. Wait for the verdict and the overlay. Let the overlay stay its full seven seconds. | Teal *Verified* bubble; the overlay with the bucket outlined. | 20 s |
| C3 | Push to talk | Tap the amber button, point at the trap, ask one clear question (see the storyboard), tap send. Do not touch anything until the voice finishes. | Hint text changing through its three stages; your words in the user bubble; the reply streaming; the phone's voice audible. | 30 s |
| C4 | Fail, fix, pass | Step 2: loosen one nut only, tap ✓, wait for *Not yet* and the overlay; loosen the other, tap ✓, wait for *Verified* and the progress bar. | Coral bubble with the fix; the overlay on the tight nut; the teal bubble after. | 30 s |
| D1 | Fridge gate refused | Fridge skill, step 1, plug still in the outlet, tap ✓. | Plug in the outlet; the refusal bubble. | 12 s |
| D2 | Fridge gate passed | Pull the plug, hold it so plug and outlet share the frame, tap ✓. | Plug and empty outlet together; *Verified*. | 12 s |
| E1 | Under the hood | Mac screen: README models table (QuickTime screen recording or a screenshot). | The four model rows. | 5 s |
| E2 | Under the hood | Terminal: `cd backend && .venv/bin/python -m pytest -q` → *41 passed*. | The green line. | 4 s |
| E3 | Under the hood | Terminal: `git log --format='%ad %s' --date=short` scrolled. | Dates Oct 27 and 28, 2026. | 4 s |
| E4 | Under the hood | Browser: github.com/DenizYunus/athena/releases. | The APK asset. | 4 s |
| G1 | Close | Browser or QuickTime: `design/GlassesStep.dc.html` and `design/GlassesUnclear.dc.html` opened, slow pan. | The 600×600 glasses cards. | 8 s |

Recording tips that save retakes:

- Hold the phone in portrait the whole time; the app is portrait-only.
- Keep the part you are checking in the middle third of the frame and fill at least a quarter of it. SAM and the verifier both prefer that.
- After every ✓, do not move the phone until the bubble has appeared; the overlay opens on top of the camera.
- Speak the push-to-talk question slowly and name the parts ("slip nut", "trap"); the transcription is biased to those words.
- Each verdict takes five to eight seconds. Those seconds are usable footage: the hint text tells the viewer what is happening.
- Use the backend terminal as a monitor: every request is logged there. If something fails, the error is in the coral bubble and in that log.

## Narration recording

1. Print the script from the storyboard. Read it once aloud for timing: aim for two minutes total.
2. Record scene by scene as separate files (`demo/narration/S01.m4a` …). A calm pace, slight pause at each full stop.
3. In the edit, place each file at its scene's start time. If a scene's narration runs long, trim words, not the pace.
4. Leave the guide's own voice audible in C3 (and optionally C1) with the narration pausing for it. That the phone talks is part of the product.

## Edit

1. New 1920×1080 30 fps project. Background: a solid `#0F1115` frame for the full length.
2. Lay the phone clips on the timeline per the storyboard times. Scale to fit the height; position left of center so the lower third has room on the right.
3. Trim dead time inside clips (waiting for a verdict) with a jump cut after about three seconds of the hint text; keep the moment the verdict appears.
4. Add the title card at 0:12, the lower thirds per the storyboard, the two captions, and the end card at 2:45.
5. Narration track per scene. Phone audio stays on its own track: duck it to −12 dB under narration, full volume when the guide speaks and the narration is silent.
6. Music: optional, instrumental, −24 dB, nothing with lyrics. Skipping music is fine.
7. Watch it once at full screen with the sound on and a stopwatch: under 3:00, every lower third legible for at least three seconds, no notification or permission dialog in frame.
8. Export: H.264, 1920×1080, 30 fps, high quality, AAC audio. Name it `athena-demo.mp4`.

## Publish

1. Upload to YouTube as **Public** or **Unlisted** (check the submission form: if it asks for a public link, choose Public). Title: "Athena · camera-verified skills · Meta Global AI Developer Hackathon". Description: the two-sentence summary from the README plus the repo link.
2. Put the link in `README.md` under **Submission** (the `TODO` line), commit, push.
3. Optionally attach `athena-demo.mp4` to the v0.3.0 release so the video lives with the code.
4. Fill the submission form from `demo/SUBMISSION_FORM.md`. Submit before Wed Nov 4, 9:00 PM ET, with every team member listed.
