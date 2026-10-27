# Athena demo video · storyboard (2:55 of a 3:00 limit)

One rule for every scene: show the loop, not the picture. The organizers' banned list includes "simple image analyzers", so the video never rests on "send a photo, get a description". Each scene shows a decision the system makes (compile, refuse, re-request, fail with a fix, pass) and names the Meta model doing it in a lower third.

Format: 1920×1080 landscape, 30 fps. The phone recording sits portrait at full height, centered or left; the right side carries the lower third and a one-line caption. Dark background `#0F1115`, amber `#F5B82E` accents, same as the app. No health, medical or nutrition framing anywhere, not even as a joke.

The sink skill carries the session scenes because any home can do it for real in ten minutes. The fridge skill carries the safety-gate scene because an unplugged fridge is the clearest "no evidence, no progress" moment.

## Timeline

| Time | Scene | On screen | Narration (spoken) | Lower third | Clip |
|---|---|---|---|---|---|
| 0:00–0:12 | Cold open | Over-the-shoulder: a hand opens the sink cabinet, water pooled in the basin. Cut to a fridge pulled out from the wall. Caption fades in: *The know-how is in someone else's head.* | A clogged sink. A fridge that won't start. Someone knows exactly how to fix it, and it isn't you. | — | F1, F2 |
| 0:12–0:20 | Title | Athena mark (amber diamond) and the line *Camera-verified skills*. Under it, small: *Meta Global AI Developer Hackathon · built Oct 27–Nov 4, 2026*. | Athena turns an expert's photos into a skill your phone walks you through, and checks, step by step. | — | title card |
| 0:20–0:50 | Teach a skill | Home → **Teach a skill**. Shutter three or four times (thumbnails fill the strip). Hold the mic: the live transcript appears as the expert talks. Tap **Compile**; the hint reads *Muse Spark is writing the steps, gates and checks…*. Review screen: title, *safety gate detected* card, steps with reference thumbnails, *gate* / *watch me* / *SAM:* chips, vocabulary line. | The expert photographs the job in order and says what they did. One Muse Spark call turns that into a skill: ordered steps, what a photo must show for each one, which step is a safety gate, the common mistakes, and the words a learner might say. Published in about twenty seconds. | `muse-voice-transcribe-1.0 · realtime dictation` then `muse-spark-1.3 · structured output → skill` | A1, A2 |
| 0:50–1:00 | Marketplace | Home: the new skill with the *yours* chip next to the seed skills. Open **Unclog a bathroom sink by cleaning the P-trap**: stats, tools, the gate card, steps with thumbnails. Tap **Install & start**. | A learner installs it like any app. Six steps, one gate, no parts. | — | B1 |
| 1:00–1:20 | Gate refused | Session opens on step 1; the guide bubble says *Safety gate before we start…* and speaks it. The cabinet has **no bucket**. Tap ✓. Verdict bubble: *Can't confirm the gate yet · … Show me: …* (sky blue) or *Not yet · … Fix: …* (coral). Open **Steps**: no *Skip step* button on a gate. | The first step is a gate. No bucket under the trap, no progress. The verifier returns a structured verdict, not a chat message: unclear, and exactly which photo would settle it. Gates cannot be skipped. *(If the verdict is "Not yet": …not yet, and exactly what to change.)* | `muse-spark-1.3 · verifier · strict JSON verdict` | C1 |
| 1:20–1:30 | Gate passed | Bucket placed under the trap. Tap ✓. *Verified* bubble, teal. The photo overlay appears with the bucket outlined and the label *Verified · bucket*. Progress bar turns teal on step 1. | Bucket in place: verified. SAM 3.1 outlines what it matched on your own photo. | `sam-3.1 · mask on the learner's photo` | C2 |
| 1:30–1:55 | Push to talk | Tap the amber button, point at the trap, ask *"Which nut do I loosen first, the one near the wall or the one under the sink?"*, tap send. Hint: *Sending the clip…* → *Transcribing what you said…* → the words appear in the user bubble → *The guide is looking at your clip…* → the reply streams into the guide bubble and is spoken. Let the phone's voice be heard for two or three seconds. | Questions are a video clip plus your voice. The clip's audio is transcribed first, biased to the skill's vocabulary, then Muse Spark watches the clip and answers in a sentence, out loud. | `muse-voice-transcribe-1.0 · clip audio → transcript` then `muse-spark-1.3 · clip + transcript → spoken reply` | C3 |
| 1:55–2:15 | Fail, fix, pass | Step 2 with only **one** slip nut loosened. Tap ✓: *Not yet · …* with the fix, and the overlay *Look here · white plastic nut* on the tight nut. Loosen the second nut. Tap ✓: *Verified*. Progress advances. | Only one nut loosened? Not yet, and here is the fix, with the part outlined. Fix it, check again, pass. | `sam-3.1` then `muse-spark-1.3 · verifier` | C4 |
| 2:15–2:30 | Fridge gate | Fridge skill, step 1. Plug **still in** the outlet: tap ✓, refused. Plug out, outlet in the same frame: tap ✓, *Verified*. | Same loop, different domain. The fridge skill's gate wants the plug out and the outlet in the same frame. Plug in: refused. Plug out: verified. It never infers what it cannot see. | `muse-spark-1.3 · safety gate` | D1, D2 |
| 2:30–2:45 | Under the hood | Quick cuts: the README models table; terminal `pytest` → *41 passed*; `git log` with Oct 27 and 28 dates; the Releases page with the APK. | Four Meta models. Muse Spark compiles, guides and verifies with strict JSON schemas. SAM 3.1 finds the part. Voice Transcribe hears you. Muse Image drew the seed reference photos. FastAPI, Expo, forty-one tests, all built this week. | `muse-image-1.0 · seed reference photos` | E1–E4 |
| 2:45–2:55 | Close | The glasses artboards from `design/` (GlassesStep, GlassesUnclear) pan slowly. End card: Athena · github.com/DenizYunus/athena. | Next: the same session on Ray-Ban Meta glasses, hands free. Athena. Expert know-how, verified by camera. | — | G1, end card |

Narration is about 300 words, which is two minutes of speech at a calm pace inside a 2:55 cut. The remaining time is for the phone's own voice (the guide speaking) and short pauses on each verdict. Do not fill the gaps.

## Narration script, continuous

> A clogged sink. A fridge that won't start. Someone knows exactly how to fix it, and it isn't you.
>
> Athena turns an expert's photos into a skill your phone walks you through, and checks, step by step.
>
> The expert photographs the job in order and says what they did. One Muse Spark call turns that into a skill: ordered steps, what a photo must show for each one, which step is a safety gate, the common mistakes, and the words a learner might say. Published in about twenty seconds.
>
> A learner installs it like any app. Six steps, one gate, no parts.
>
> The first step is a gate. No bucket under the trap, no progress. The verifier returns a structured verdict, not a chat message: unclear, and exactly which photo would settle it. Gates cannot be skipped.
>
> Bucket in place: verified. SAM 3.1 outlines what it matched on your own photo.
>
> Questions are a video clip plus your voice. The clip's audio is transcribed first, biased to the skill's vocabulary, then Muse Spark watches the clip and answers in a sentence, out loud.
>
> Only one nut loosened? Not yet, and here is the fix, with the part outlined. Fix it, check again, pass.
>
> Same loop, different domain. The fridge skill's gate wants the plug out and the outlet in the same frame. Plug in: refused. Plug out: verified. It never infers what it cannot see.
>
> Four Meta models. Muse Spark compiles, guides and verifies with strict JSON schemas. SAM 3.1 finds the part. Voice Transcribe hears you. Muse Image drew the seed reference photos. FastAPI, Expo, forty-one tests, all built this week.
>
> Next: the same session on Ray-Ban Meta glasses, hands free. Athena. Expert know-how, verified by camera.

## Text that appears on screen

Title card: **Athena** / Camera-verified skills / Meta Global AI Developer Hackathon · built Oct 27–Nov 4, 2026

Lower thirds (monospace model id, then a plain-language role):

- `muse-voice-transcribe-1.0` · realtime dictation
- `muse-spark-1.3` · structured output → skill
- `muse-spark-1.3` · verifier · strict JSON verdict
- `sam-3.1` · mask on the learner's photo
- `muse-voice-transcribe-1.0` · clip audio → transcript
- `muse-spark-1.3` · clip + transcript → spoken reply
- `muse-spark-1.3` · safety gate
- `muse-image-1.0` · seed reference photos

Captions (one line, bottom right, only where the narration needs a number on screen): *41 tests* at 2:34; *github.com/DenizYunus/athena* from 2:45 to the end.

End card: **Athena** · github.com/DenizYunus/athena · APK on the Releases page

## What the video must not claim

- No "live video call with the AI". The Model API has no realtime video session; the app sends clips and photos. The narration says "watches the clip".
- No tool calling. The guide and the verifier use structured output and the app's own loop, which is the agentic part. Say "structured verdict", not "function calls".
- No real-expert attribution. The seed skills are written by us with generated reference photos; the author in the demo is you, playing the expert.
- Nothing about health, medicine, diet or wellness.

## If a scene will not cooperate on the day

- Live dictation transcript does not appear in Teach a skill: type the note. Change the narration to "and describes what they did". The compiler scene still works.
- The gate verdict comes back as *Not yet* instead of *Can't confirm*: use the alternate narration line in the table. Both are structured verdicts.
- The guide's spoken reply is too quiet in the recording: keep the bubble on screen and let the narration carry it; mention "out loud" anyway, it is true.
- SAM finds nothing on a photo (no overlay): the verdict bubble still shows. Retake with the part filling more of the frame; plain objects (bucket, white nut) are reliable.
- The fridge plug is not reachable: drop scene 2:15–2:30, give the extra fifteen seconds to the fail-fix-pass scene, and let the narration say "the fridge skill's gate wants the plug out" over the skill detail page.
