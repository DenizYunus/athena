# Submission form text (copy and paste)

Most hackathon forms ask for the same fields. Everything below is written to paste as is; edit the team line and add the video link.

**Project name:** Athena

**Tagline:** A marketplace of camera-verified skills: an expert's video and manual become a step-by-step guide that checks your work.

**Track:** Indie Developer · **Problem statement:** Build for Your World

**Team:** Deniz Yunus Gogus (GitHub @DenizYunus), Emre Can Dikmen

**Repository:** https://github.com/DenizYunus/athena · **Demo video:** TODO link

**Meta models used:** muse-spark-1.3 (watches the expert's video and reads the manual to write the skill; the verifier, with structured output; the guide's spoken replies), sam-3.1 (picks each step's reference frame from the expert's video; part masks on the learner's photo), muse-voice-transcribe-1.0 (batch transcription of question clips, sentence-by-sentence timing of the expert's narration while a video is digested, realtime transcription of dictated notes), muse-image-1.0 (seed reference photos).

## Inspiration

Repair know-how lives in people's heads and dies with every retirement. The videos that try to transfer it cannot see what you are doing, so they cannot tell you that you skipped the one step that matters. A phone camera can.

## What it does

An expert films a job while talking through it, uploads the video or pastes a YouTube link to it, and adds the manual as a PDF if there is one. Muse Spark watches the video and reads the manual and writes a skill: ordered steps, the visual state that proves each step is done, which steps are safety gates (the manual's lockout and drain steps become gates even when the video skips them), the manual's specs for each step, the points where the video and the manual disagree, common mistakes, and the vocabulary a learner might say. SAM 3.1 picks a reference frame for every step from the expert's own video, and each step keeps a short clip of the expert doing it. A learner installs the skill and a guide walks them through it from the live camera. They ask questions by recording a short clip with their voice; the clip's audio is transcribed and the guide answers from the video, in a sentence, spoken aloud. They prove each step with a photo; the verifier judges it against what the expert said done looks like, next to the expert's reference photo where the step has one, and returns a strict JSON verdict: pass, fail with the concrete fix, or unclear with the exact photo that would settle it. SAM 3.1 outlines the part in question on the learner's own photo. Safety gates cannot be skipped: no evidence, no next step.

## How we built it

FastAPI backend with the `openai` SDK pointed at the Meta Model API. Muse Spark 1.3 handles three jobs: digesting the expert's video and manual into a skill (both through the Files API, strict JSON schema; a long video is cut into parts that are read in parallel and merged by one more call), the push-to-talk turn (mp4 clip plus transcript in, streamed spoken reply out), and verification (learner photo, plus the expert's reference photo where the step has one, in; strict JSON verdict out). SAM 3.1 grounds each step's candidate reference frames while a video is digested, and runs in parallel with every verification, trying each step's visual phrases at once; its masks are decoded with Meta's parser and drawn with react-native-svg. Voice Transcribe transcribes the clip's audio track (ffmpeg pulls it out) before the guide sees the clip, streams a live transcript while an author dictates a note, and, while a video is digested, splits the expert's narration into sentences with their start and end so that every reader of the video gets them. The Android app is Expo with no navigation library: marketplace, skill detail, settings, the push-to-talk session, the video and manual upload with job progress, the photo capture fallback, and the review screen with conflicts and the expert's step clips. 243 backend tests run against a fake client and real ffmpeg; every request shape was also verified live.

## Challenges

There is no realtime video session on the Model API, so the honest design is clips and photos rather than a video call. Muse Spark reads a video through a fixed budget of frames: we measured accurate timestamps up to about 4.5 minutes and compressed ones beyond, so the digester cuts long videos into parts and merges the steps. Muse Spark also hears narration but places it loosely in time, up to 18 s off on a real classroom video; with each sentence's measured start and end in the prompt, 95% of step starts land within 3 s instead of 68%, judged blind. SAM 3.1 answers to plain visual phrases ("small black box") and not to part names ("start relay"); the compiler is prompted to produce the former. Two things wanting the microphone at once (video recording and a live transcript) is a mess on phones, so the session records first and transcribes on the server, streaming the transcript back before the reply.

## Accomplishments

The verifier rejects a leaking pipe joint with the exact nut to tighten, asks for the plug and the outlet in the same frame before it will let anyone touch a fridge, and tells a fridge photo from a sink photo. On a narrated test video with a one-page procedure, the digester turned the procedure's lockout, valve and drain steps into safety gates the video never showed, and flagged a planted conflict between the socket size said in the video and the one in the manual. Everything was built during the hackathon, from the first commit on Oct 27.

## What we learned

Structured output is what makes a verification loop trustworthy: a schema with pass, fail and unclear, a confidence, and a photo request forces the model to commit, and lets the app refuse a hesitant pass on a safety gate. Grounding (SAM) belongs on the learner's own photo, not on a diagram.

## What's next

The glasses source already runs on Meta's Mock Device Kit (stream, photos for checks, audio push-to-talk, lens cards); next is the same session on real Ray-Ban Meta glasses and Meta Ray-Ban Display, hands free. Paid skills with per-completion payouts to the expert. An eval set of real pass, fail and unclear photos per skill.

## Built with

Python, FastAPI, openai SDK, meta-sam-parser, ffmpeg, Expo, React Native, TypeScript, react-native-svg, expo-camera, expo-audio, expo-speech, expo-document-picker, expo-video.
