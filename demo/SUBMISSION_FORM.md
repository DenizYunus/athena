# Submission form text (copy and paste)

Most hackathon forms ask for the same fields. Everything below is written to paste as is; edit the team line and add the video link.

**Project name:** Athena

**Tagline:** A marketplace of camera-verified skills: an expert's photos and voice become a step-by-step guide that checks your work.

**Track:** Indie Developer · **Problem statement:** Build for Your World

**Team:** Deniz Yunus Gogus (GitHub @DenizYunus), solo

**Repository:** https://github.com/DenizYunus/athena · **Demo video:** TODO link

**Meta models used:** muse-spark-1.3 (skill compiler, guide, verifier; structured output), sam-3.1 (part masks on the learner's photo), muse-voice-transcribe-1.0 (batch transcription of question clips, realtime transcription of dictated notes), muse-image-1.0 (seed reference photos).

## Inspiration

Repair know-how lives in people's heads and dies with every retirement. The videos that try to transfer it cannot see what you are doing, so they cannot tell you that you skipped the one step that matters. A phone camera can.

## What it does

An expert photographs a job in order and says what they did for each photo. One Muse Spark call compiles that into a skill: ordered steps, the visual state that proves each step is done, which steps are safety gates, common mistakes, reference photos, and the vocabulary a learner might say. A learner installs the skill and a guide walks them through it from the live camera. They ask questions by recording a short clip with their voice; the clip's audio is transcribed and the guide answers from the video, in a sentence, spoken aloud. They prove each step with a photo; the verifier compares it with the expert's reference and returns a strict JSON verdict: pass, fail with the concrete fix, or unclear with the exact photo that would settle it. SAM 3.1 outlines the part in question on the learner's own photo. Safety gates cannot be skipped: no evidence, no next step.

## How we built it

FastAPI backend with the `openai` SDK pointed at the Meta Model API. Muse Spark 1.3 handles three jobs with strict JSON schemas: compiling a skill from interleaved photos and notes, the push-to-talk turn (mp4 clip plus transcript in, streamed reply out), and verification (reference photo plus learner photo in, verdict out). SAM 3.1 runs in parallel with every verification, trying each step's visual phrases at once; its masks are decoded with Meta's parser and drawn with react-native-svg. Voice Transcribe transcribes the clip's audio track (ffmpeg pulls it out) before the guide sees the clip, and streams a live transcript while an author dictates a note. The Android app is Expo with no navigation library: marketplace, skill detail, settings, the push-to-talk session, and the author capture and review screens. 41 backend tests run against a fake client; every request shape was also verified live.

## Challenges

There is no realtime video session on the Model API, so the honest design is clips and photos rather than a video call. SAM 3.1 answers to plain visual phrases ("small black box") and not to part names ("start relay"); the compiler is prompted to produce the former. Two things wanting the microphone at once (video recording and a live transcript) is a mess on phones, so the session records first and transcribes on the server, streaming the transcript back before the reply.

## Accomplishments

The verifier rejects a leaking pipe joint with the exact nut to tighten, asks for the plug and the outlet in the same frame before it will let anyone touch a fridge, and tells a fridge photo from a sink photo. An expert's photos and dictated notes become a published, checkable skill in about twenty seconds. Everything was built during the hackathon, from the first commit on Oct 27.

## What we learned

Structured output is what makes a verification loop trustworthy: a schema with pass, fail and unclear, a confidence, and a photo request forces the model to commit, and lets the app refuse a hesitant pass on a safety gate. Grounding (SAM) belongs on the learner's own photo, not on a diagram.

## What's next

The same session on Ray-Ban Meta glasses through the Wearables Device Access Toolkit, hands free, with step cards on Meta Ray-Ban Display. Paid skills with per-completion payouts to the expert. An eval set of real pass, fail and unclear photos per skill.

## Built with

Python, FastAPI, openai SDK, meta-sam-parser, ffmpeg, Expo, React Native, TypeScript, react-native-svg, expo-camera, expo-audio, expo-speech.
