// Push-to-talk session over the live camera feed.
// Tap the big button: record video + mic. Tap again: the clip goes to the backend, which
// pulls the audio track, transcribes it (Voice Transcribe, biased to the skill's vocabulary)
// and only then asks the guide. The transcript lands in your bubble first; the reply streams
// into the guide's bubble and is spoken. The check button takes one photo and runs the strict
// verification; SAM 3.1 marks the part on the photo when it finds it.
//
// Glasses toggle (Android, modules/glasses-bridge): the frames come from Ray-Ban Meta glasses
// through the Device Access Toolkit instead of the phone camera. Push to talk then records the
// question as an audio file with expo-audio and sends it with a photo from the glasses; the check
// button sends a glasses photo. Nothing here changes for the phone path while the toggle is off.
import { RecordingPresets, setAudioModeAsync, useAudioRecorder, type RecordingOptions } from "expo-audio";
import { CameraView, useCameraPermissions, useMicrophonePermissions } from "expo-camera";
import * as Speech from "expo-speech";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Image, Pressable, StyleSheet, Text, View } from "react-native";
import { Api, Highlight, Session, Skill, Step, Verdict } from "../api";
import { useGlasses, type Card, type GlassesState } from "../glasses";
import { HighlightOverlay } from "../HighlightOverlay";
import { colors } from "../theme";
import { GhostButton, PrimaryButton } from "../ui";

type Bubble = { id: number; who: "user" | "guide"; text: string; tone?: "pass" | "fail" | "unclear" | "gate"; pending?: boolean };
type Phase = "idle" | "recording" | "sending" | "checking" | "speaking";

const BUBBLE_OPACITY = [1, 0.9, 0.7, 0.5, 0.3, 0.15];

// The question for a glasses turn: 16 kHz mono AAC in an m4a. The backend transcribes it and
// never forwards an audio-only file to the guide as video.
const QUESTION_AUDIO: RecordingOptions = { ...RecordingPresets.HIGH_QUALITY, extension: ".m4a", sampleRate: 16000, numberOfChannels: 1, bitRate: 64000 };

export function SessionScreen({
  api,
  skill,
  session: initial,
  onExit,
  glassesMock = true,
}: {
  api: Api;
  skill: Skill;
  session: Session;
  onExit: () => void;
  /** Settings: the Glasses toggle uses the Mock Device Kit instead of real glasses. */
  glassesMock?: boolean;
}) {
  const [camPerm, requestCam] = useCameraPermissions();
  const [micPerm, requestMic] = useMicrophonePermissions();
  const camera = useRef<CameraView>(null);
  const [cameraMode, setCameraMode] = useState<"video" | "picture">("video");
  const [phase, setPhase] = useState<Phase>("idle");
  const [session, setSession] = useState<Session>(initial);
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [seconds, setSeconds] = useState(0);
  const [showSteps, setShowSteps] = useState(false);
  const [stage, setStage] = useState<"" | "uploading" | "transcribing" | "thinking">("");
  const [overlay, setOverlay] = useState<{ photoUri: string; highlight: Highlight; color: string; caption: string } | null>(null);
  const nextId = useRef(1);
  const recordingPromise = useRef<Promise<{ uri: string } | undefined> | null>(null);
  const secondsRef = useRef(0);

  const glasses = useGlasses({ mock: glassesMock, quality: "high", frameRate: 15 });
  const glassesOn = glasses.on;
  const recorder = useAudioRecorder(QUESTION_AUDIO);

  const step = useMemo(() => skill.steps.find((st) => st.n === session.current_step) ?? skill.steps[0], [skill, session.current_step]);
  const total = skill.steps.length;

  const push = useCallback((b: Omit<Bubble, "id">): number => {
    const id = nextId.current++;
    setBubbles((prev) => [...prev, { ...b, id }].slice(-8));
    return id;
  }, []);

  const update = useCallback((id: number, patch: Partial<Bubble>) => {
    setBubbles((prev) => prev.map((b) => (b.id === id ? { ...b, ...patch } : b)));
  }, []);

  const say = useCallback((text: string) => {
    Speech.stop();
    setPhase("speaking");
    Speech.speak(text, {
      language: "en-US",
      rate: 1.0,
      onDone: () => setPhase("idle"),
      onStopped: () => setPhase("idle"),
      onError: () => setPhase("idle"),
    });
  }, []);

  useEffect(() => {
    (async () => {
      if (!camPerm?.granted) await requestCam();
      if (!micPerm?.granted) await requestMic();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const intro = step.safety_gate
      ? `Safety gate before we start. ${step.instruction} Show me, then tap check.`
      : `Step ${step.n} of ${total}. ${step.instruction}`;
    push({ who: "guide", text: intro, tone: step.safety_gate ? "gate" : undefined });
    say(intro);
    return () => {
      Speech.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.current_step]);

  // Lens card for the current step, once the glasses are live and whenever the step changes.
  useEffect(() => {
    if (!glasses.live) return;
    glasses.showCard(stepCard(step, total, session.completed));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [glasses.live, session.current_step, session.completed]);

  useEffect(() => {
    if (glasses.state === "error" && glasses.message) push({ who: "guide", text: `Glasses: ${glasses.message}`, tone: "fail" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [glasses.state, glasses.message]);

  useEffect(() => {
    if (phase !== "recording") {
      setSeconds(0);
      secondsRef.current = 0;
      return;
    }
    const t = setInterval(
      () =>
        setSeconds((x) => {
          secondsRef.current = x + 1;
          return x + 1;
        }),
      1000,
    );
    return () => clearInterval(t);
  }, [phase]);

  const phaseRef = useRef<Phase>("idle");
  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  async function onTapRecord() {
    if (glassesOn) return onTapRecordGlasses();
    if (!camera.current) return;
    if (phase === "idle" || phase === "speaking") {
      Speech.stop();
      setOverlay(null);
      if (cameraMode !== "video") {
        setCameraMode("video");
        await wait(400);
      }
      setPhase("recording");
      try {
        recordingPromise.current = camera.current.recordAsync({ maxDuration: 20 });
      } catch (e: any) {
        push({ who: "guide", text: friendly(e), tone: "fail" });
        setPhase("idle");
        return;
      }
      recordingPromise.current
        .then((r) => {
          if (r?.uri && phaseRef.current === "recording") sendClip(r.uri);
          else if (!r?.uri) setPhase("idle");
        })
        .catch((e) => {
          push({ who: "guide", text: friendly(e), tone: "fail" });
          setPhase("idle");
        });
      return;
    }
    if (phase === "recording") {
      camera.current.stopRecording();
    }
  }

  /** Glasses turn: the question is recorded by expo-audio (no camera recording), then sent with a glasses photo. */
  async function onTapRecordGlasses() {
    if (phase === "idle" || phase === "speaking") {
      Speech.stop();
      setOverlay(null);
      try {
        await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
        await recorder.prepareToRecordAsync();
        recorder.record();
        setPhase("recording");
        glasses.showCard({ kind: "listening", eyebrow: `step ${step.n}`, title: "Listening…", body: "A photo of what you're looking at goes with it", hint: "Tap again to send · reply comes as voice + text" });
      } catch (e: any) {
        push({ who: "guide", text: friendly(e), tone: "fail" });
        setPhase("idle");
      }
      return;
    }
    if (phase === "recording") {
      const secs = Math.max(secondsRef.current, 1);
      setPhase("sending");
      setStage("uploading");
      let audioUri: string | null = null;
      try {
        await recorder.stop();
        audioUri = recorder.uri;
      } catch (e: any) {
        push({ who: "guide", text: friendly(e), tone: "fail" });
      }
      setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true }).catch(() => {});
      let photoUri: string | undefined;
      try {
        photoUri = (await glasses.capturePhoto()).uri;
      } catch (e: any) {
        push({ who: "guide", text: `No glasses photo: ${friendly(e)}`, tone: "unclear" });
      }
      if (!audioUri && !photoUri) {
        setStage("");
        setPhase("idle");
        return;
      }
      sendClip(audioUri ?? undefined, photoUri, secs);
    }
  }

  async function sendClip(clipUri?: string, photoUri?: string, secsOverride?: number) {
    setPhase("sending");
    setStage("uploading");
    const secs = Math.max(secsOverride ?? seconds, 1);
    const what = photoUri ? `Voice · ${secs} s · glasses photo` : `Clip · ${secs} s`;
    const userId = push({ who: "user", text: `${what} · sending…`, pending: true });
    let guideId: number | null = null;
    try {
      const res = await api.turnStream(
        session.id,
        { clipUri, photoUri },
        {
          transcribing: () => {
            setStage("transcribing");
            update(userId, { text: `${what} · transcribing your words…` });
          },
          transcript: (text) => {
            setStage("thinking");
            update(userId, { text: text || `${what} · no words heard, the guide looks at the ${photoUri ? "photo" : "video"}`, pending: false });
          },
          delta: (_d, reply) => {
            if (guideId === null) guideId = push({ who: "guide", text: reply, pending: true });
            else update(guideId, { text: reply });
          },
        },
      );
      if (guideId === null) guideId = push({ who: "guide", text: res.reply });
      update(guideId, { text: res.reply, pending: false });
      update(userId, { pending: false });
      setStage("");
      say(res.reply);
    } catch (e: any) {
      setStage("");
      update(userId, { pending: false });
      if (guideId !== null) update(guideId, { pending: false });
      push({ who: "guide", text: friendly(e), tone: "fail" });
      setPhase("idle");
    }
  }

  async function onTapCheck() {
    if (glassesOn) return onTapCheckGlasses();
    if (!camera.current || (phase !== "idle" && phase !== "speaking")) return;
    Speech.stop();
    setOverlay(null);
    setPhase("checking");
    try {
      if (cameraMode !== "picture") {
        setCameraMode("picture");
        await wait(500);
      }
      const photo = await camera.current.takePictureAsync({ quality: 0.85, skipProcessing: true });
      if (!photo?.uri) throw new Error("no photo");
      push({ who: "user", text: `Photo sent for step ${step.n}` });
      await verifyPhoto(photo.uri);
    } catch (e: any) {
      push({ who: "guide", text: friendly(e), tone: "fail" });
      setPhase("idle");
    } finally {
      setCameraMode("video");
    }
  }

  /** Glasses check: capturePhoto() on the stream (or the latest frame) goes to the verifier. */
  async function onTapCheckGlasses() {
    if (phase !== "idle" && phase !== "speaking") return;
    Speech.stop();
    setOverlay(null);
    setPhase("checking");
    glasses.showCard({ kind: "checking", eyebrow: `STEP ${step.n} OF ${total} · photo taken`, title: "Checking…", body: "Comparing with the reference photo. Keep your hands where they are.", hint: "usually 3 to 5 seconds" });
    try {
      const photo = await glasses.capturePhoto();
      push({ who: "user", text: `Photo from the glasses sent for step ${step.n}` });
      await verifyPhoto(photo.uri);
    } catch (e: any) {
      push({ who: "guide", text: friendly(e), tone: "fail" });
      setPhase("idle");
    }
  }

  /** The strict verification and what follows from the verdict, for a photo from either camera. */
  async function verifyPhoto(photoUri: string) {
    const res = await api.verify(session.id, photoUri);
    const v = res.verdict;
    push({ who: "guide", text: verdictLine(v, step.safety_gate), tone: v.status });
    if (res.highlight && res.highlight.objects.length) {
      const color = toneColor(v.status);
      const caption = v.status === "pass" ? "Verified" : v.status === "fail" ? "Look here" : "This is what I see";
      setOverlay({ photoUri, highlight: res.highlight, color, caption });
    }
    setSession((s) => ({ ...s, current_step: res.current_step, completed: res.completed }));
    glasses.showCard(verdictCard(v, step, res.completed));
    if (res.completed) {
      say(`${v.reason} That was the last step. Nice work.`);
    } else if (!res.advanced) {
      say(verdictSpeech(v));
    } else {
      Speech.speak(`Step ${step.n} verified.`, { language: "en-US" });
      setPhase("idle");
    }
  }

  async function onSkip() {
    try {
      const s = await api.skip(session.id);
      setSession(s);
      setShowSteps(false);
    } catch (e: any) {
      push({ who: "guide", text: friendly(e), tone: "fail" });
    }
  }

  const dismissOverlay = useCallback(() => setOverlay(null), []);

  if (!camPerm?.granted) {
    return (
      <View style={s.center}>
        <Text style={s.permText}>Athena needs the camera to see what you're working on.</Text>
        <PrimaryButton label="Allow camera" onPress={requestCam} style={{ alignSelf: "stretch" }} />
        <GhostButton label="Exit" onPress={onExit} color={colors.muted} />
      </View>
    );
  }

  const visible = bubbles.slice(-BUBBLE_OPACITY.length);
  const busy = phase === "recording" || phase === "sending" || phase === "checking";
  const glassesColor = glasses.live ? colors.pass : glasses.state === "error" ? colors.fail : colors.amber;
  const glassesLabel = !glassesOn ? "Glasses" : glasses.live ? "Glasses ✓" : glasses.state === "error" ? "Glasses !" : "Glasses …";
  const hint = glassesOn && !glasses.live && (phase === "idle" || phase === "speaking") ? glassesWaitText(glasses.state, glasses.message, glasses.mock) : hintFor(phase, stage, session.completed, glassesOn);

  return (
    <View style={s.root}>
      {glassesOn ? (
        glasses.frameUri ? (
          <Image source={{ uri: glasses.frameUri }} style={StyleSheet.absoluteFill} resizeMode="cover" accessibilityLabel="Live view from the glasses" />
        ) : (
          <View style={[StyleSheet.absoluteFill, s.glassesWait]}>
            <ActivityIndicator color={colors.amber} />
            <Text style={s.glassesWaitText}>{glassesWaitText(glasses.state, glasses.message, glasses.mock)}</Text>
          </View>
        )
      ) : (
        <CameraView ref={camera} style={StyleSheet.absoluteFill} facing="back" mode={cameraMode} mute={false} />
      )}

      <View style={s.top}>
        <Pressable onPress={onExit} style={s.roundBtn} accessibilityLabel="Exit session">
          <Text style={s.roundBtnText}>✕</Text>
        </Pressable>
        <View style={s.pill}>
          <Text style={s.pillText}>{session.completed ? "Done" : `Step ${step.n} of ${total}`}</Text>
        </View>
        {phase === "recording" ? (
          <View style={[s.pill, { borderColor: colors.fail }]}>
            <View style={[s.dot, { backgroundColor: colors.fail }]} />
            <Text style={[s.pillText, { color: colors.fail }]}>
              REC {fmt(seconds)} · {glassesOn ? "voice + glasses" : "video + voice"}
            </Text>
          </View>
        ) : (
          <View style={s.pill}>
            <View style={[s.dot, { backgroundColor: colors.pass }]} />
            <Text style={[s.pillText, { color: colors.muted }]}>Live</Text>
          </View>
        )}
        {glasses.available ? (
          <Pressable
            onPress={glasses.toggle}
            disabled={busy}
            style={[s.pill, glassesOn ? { borderColor: glassesColor } : null, busy ? { opacity: 0.6 } : null]}
            accessibilityLabel={glassesOn ? "Glasses on, tap to use the phone camera" : "Glasses off, tap to use the glasses camera"}
          >
            <View style={[s.dot, { backgroundColor: glassesOn ? glassesColor : colors.dim }]} />
            <Text style={[s.pillText, { color: glassesOn ? colors.text : colors.muted }]}>{glassesLabel}</Text>
          </Pressable>
        ) : null}
      </View>
      <View style={s.progress}>
        {skill.steps.map((st) => (
          <View
            key={st.n}
            style={[
              s.seg,
              { backgroundColor: st.n < session.current_step || session.completed ? colors.pass : st.n === session.current_step ? colors.amber : "rgba(243,244,246,0.35)" },
            ]}
          />
        ))}
      </View>
      {phase === "recording" ? <View pointerEvents="none" style={s.recFrame} /> : null}

      <View style={s.chat} pointerEvents="none">
        {visible.map((b, i) => {
          const fromBottom = visible.length - 1 - i;
          const opacity = BUBBLE_OPACITY[fromBottom] ?? 0;
          const tone = b.tone ? toneColor(b.tone) : undefined;
          return (
            <View
              key={b.id}
              style={[s.bubble, b.who === "user" ? s.bubbleUser : s.bubbleGuide, tone ? { borderColor: tone, borderWidth: 1 } : null, b.pending ? s.bubblePending : null, { opacity }]}
            >
              <Text style={[s.bubbleText, b.who === "guide" && { color: colors.text2 }, tone ? { color: colors.text } : null, b.pending ? { color: colors.muted } : null]}>{b.text}</Text>
            </View>
          );
        })}
      </View>

      <View style={s.controls}>
        <Text style={s.hint}>{hint}</Text>
        <View style={s.controlRow}>
          <Pressable onPress={() => setShowSteps(true)} style={s.sideBtn} accessibilityLabel="Show steps">
            <Text style={s.sideBtnText}>Steps</Text>
          </Pressable>

          {phase === "idle" || phase === "speaking" ? (
            <Pressable
              onPress={onTapRecord}
              style={[s.bigBtn, { backgroundColor: colors.amber, borderColor: colors.amberDark }]}
              accessibilityLabel={glassesOn ? "Tap to record a question with a glasses photo" : "Tap to record a question with video"}
              disabled={session.completed}
            >
              <View style={s.micIcon} />
            </Pressable>
          ) : phase === "recording" ? (
            <Pressable onPress={onTapRecord} style={[s.bigBtn, { backgroundColor: colors.fail, borderColor: "rgba(255,107,87,0.35)" }]} accessibilityLabel="Tap to stop and send">
              <View style={s.stopIcon} />
            </Pressable>
          ) : (
            <View style={[s.bigBtn, { backgroundColor: "rgba(26,29,35,0.9)", borderColor: colors.border }]}>
              <ActivityIndicator color={colors.amber} />
            </View>
          )}

          <Pressable onPress={onTapCheck} style={[s.sideBtn, { borderColor: colors.pass }]} accessibilityLabel="Check this step now" disabled={phase !== "idle" && phase !== "speaking"}>
            <Text style={[s.sideBtnText, { color: colors.pass, fontSize: 20 }]}>✓</Text>
          </Pressable>
        </View>
      </View>

      {showSteps ? (
        <View style={s.sheet}>
          <Text style={s.sheetTitle}>
            Step {step.n} of {total}
            {step.safety_gate ? "  · SAFETY GATE" : ""}
          </Text>
          <Text style={s.sheetText}>{step.instruction}</Text>
          <Text style={s.sheetLabel}>Done looks like</Text>
          <Text style={s.sheetText}>{step.expected_state}</Text>
          {step.common_mistakes.length ? (
            <>
              <Text style={s.sheetLabel}>Watch out</Text>
              <Text style={s.sheetText}>{step.common_mistakes.join(". ")}</Text>
            </>
          ) : null}
          <View style={{ flexDirection: "row", gap: 10, marginTop: 14 }}>
            <GhostButton label="Close" onPress={() => setShowSteps(false)} style={{ flex: 1 }} />
            {!step.safety_gate && !session.completed ? <GhostButton label="Skip step" onPress={onSkip} color={colors.muted} style={{ flex: 1 }} /> : null}
            {session.completed ? <PrimaryButton label="Finish" onPress={onExit} style={{ flex: 1 }} /> : null}
          </View>
        </View>
      ) : null}

      {overlay ? <HighlightOverlay photoUri={overlay.photoUri} highlight={overlay.highlight} color={overlay.color} caption={overlay.caption} onDismiss={dismissOverlay} /> : null}
    </View>
  );
}

function wait(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

function fmt(sec: number) {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

function toneColor(t: NonNullable<Bubble["tone"]>) {
  return t === "pass" ? colors.pass : t === "fail" ? colors.fail : t === "unclear" ? colors.unclear : colors.amber;
}

function verdictLine(v: Verdict, gate: boolean): string {
  const head = v.status === "pass" ? "Verified" : v.status === "fail" ? "Not yet" : gate ? "Can't confirm the gate yet" : "Can't tell yet";
  let line = `${head} · ${v.reason}`;
  if (v.status === "fail" && v.what_to_fix) line += ` Fix: ${v.what_to_fix}`;
  if (v.photo_request?.needed) line += ` Show me: ${v.photo_request.what}${v.photo_request.angle ? ` (${v.photo_request.angle})` : ""}.`;
  if (v.safety_concern) line += " Careful, I see a hazard.";
  return line;
}

function verdictSpeech(v: Verdict): string {
  if (v.status === "fail") return `${v.reason} ${v.what_to_fix}`.trim();
  if (v.photo_request?.needed) return `${v.reason} Show me ${v.photo_request.what}${v.photo_request.angle ? `, ${v.photo_request.angle}` : ""}.`;
  return v.reason;
}

function hintFor(phase: Phase, stage: "" | "uploading" | "transcribing" | "thinking", completed: boolean, glassesOn = false) {
  if (completed) return "All steps verified";
  switch (phase) {
    case "idle":
      return glassesOn ? "Tap to ask; a photo from the glasses goes with your voice" : "Tap to record video and your question";
    case "recording":
      return glassesOn ? "Recording your voice · tap to send with a glasses photo" : "Recording video + voice · tap to send";
    case "sending":
      return stage === "transcribing" ? "Transcribing what you said…" : stage === "thinking" ? (glassesOn ? "The guide is looking at the glasses photo…" : "The guide is looking at your clip…") : glassesOn ? "Sending your voice and the photo…" : "Sending the clip…";
    case "checking":
      return glassesOn ? "Checking this step from the glasses photo…" : "Checking this step from a photo…";
    case "speaking":
      return "Reply on screen and spoken aloud";
  }
}

function glassesWaitText(state: GlassesState, message: string | null, mock: boolean): string {
  const where = mock ? "Mock Device Kit" : "Ray-Ban Meta";
  switch (state) {
    case "connecting":
      return `Connecting to the glasses (${where})…`;
    case "registering":
      return "Approve Athena in the Meta AI app…";
    case "permission":
      return "Allow camera access in the Meta AI app…";
    case "connected":
      return "Glasses connected · starting the camera…";
    case "streaming":
      return "Waiting for the first frame…";
    case "paused":
      return "Stream paused from the glasses · tap the touchpad to resume";
    case "error":
      return `Glasses: ${message ?? "error"}`;
    case "disconnected":
      return `Glasses disconnected${message ? ` · ${message}` : ""}`;
    default:
      return "Glasses off";
  }
}

// 600x600 lens cards for Meta Ray-Ban Display, after design/Glasses*.dc.html. Ignored on glasses without a display.
function stepCard(step: Step, total: number, completed: boolean): Card {
  if (completed) return { kind: "pass", eyebrow: "DONE", title: "All steps verified", body: "Nice work.", hint: "" };
  if (step.safety_gate) return { kind: "gate", eyebrow: `SAFETY GATE · before step ${step.n}`, title: step.instruction, body: step.gate_requirement ?? step.expected_state, hint: 'Say "done" or tap check' };
  return { kind: "step", eyebrow: `STEP ${step.n} OF ${total}`, title: step.instruction, body: step.expected_state, hint: 'Tap check when done · "repeat" · "help"' };
}

function verdictCard(v: Verdict, step: Step, completed: boolean): Card {
  if (v.status === "pass") return { kind: "pass", eyebrow: completed ? "DONE" : `STEP ${step.n} VERIFIED`, title: completed ? "All steps verified" : "Verified", body: v.reason, hint: completed ? "" : "Next step coming up" };
  if (v.status === "fail") return { kind: "fail", eyebrow: `NOT YET · step ${step.n}`, title: v.what_to_fix || v.reason, body: v.what_to_fix ? v.reason : "", hint: "Fix it, then check again" };
  const ask = v.photo_request?.needed ? `${v.photo_request.what}${v.photo_request.angle ? ` · ${v.photo_request.angle}` : ""}` : v.reason;
  return { kind: "unclear", eyebrow: `NEED A BETTER LOOK · step ${step.n}`, title: ask, body: v.photo_request?.needed ? v.reason : "", hint: "Hold still, then check again" };
}

function friendly(e: any): string {
  const m: string = e?.message ?? String(e);
  if (m.includes("503")) return "No API key yet. Open Settings and enter your Meta Model API key.";
  if (m.includes("Network request failed")) return "Can't reach the backend. Check the URL in Settings and that uvicorn is running with --host 0.0.0.0.";
  return m;
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface2 },
  center: { flex: 1, backgroundColor: colors.bg, padding: 24, justifyContent: "center", gap: 14 },
  permText: { color: colors.text, fontSize: 17, textAlign: "center", lineHeight: 24 },
  glassesWait: { backgroundColor: colors.bg, alignItems: "center", justifyContent: "center", gap: 12 },
  glassesWaitText: { color: colors.muted, fontSize: 13, paddingHorizontal: 32, textAlign: "center", lineHeight: 18 },
  top: { position: "absolute", top: 54, left: 16, right: 16, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 6 },
  roundBtn: { width: 44, height: 44, borderRadius: 22, backgroundColor: "rgba(15,17,21,0.75)", alignItems: "center", justifyContent: "center" },
  roundBtnText: { color: colors.text, fontSize: 18 },
  pill: { height: 32, paddingHorizontal: 12, borderRadius: 16, backgroundColor: "rgba(15,17,21,0.75)", flexDirection: "row", alignItems: "center", gap: 6, borderWidth: 1, borderColor: "transparent" },
  pillText: { color: colors.text, fontSize: 13, fontWeight: "600" },
  dot: { width: 8, height: 8, borderRadius: 4 },
  progress: { position: "absolute", top: 98, left: 16, right: 16, flexDirection: "row", gap: 4 },
  seg: { flex: 1, height: 4, borderRadius: 2 },
  recFrame: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, borderWidth: 4, borderColor: colors.fail },
  chat: { position: "absolute", left: 16, right: 16, bottom: 150, maxHeight: 330, justifyContent: "flex-end", gap: 8 },
  bubble: { maxWidth: 300, paddingVertical: 9, paddingHorizontal: 13, borderRadius: 16 },
  bubbleGuide: { alignSelf: "flex-start", backgroundColor: "rgba(26,29,35,0.92)", borderBottomLeftRadius: 4 },
  bubbleUser: { alignSelf: "flex-end", backgroundColor: "rgba(42,47,56,0.95)", borderBottomRightRadius: 4 },
  bubblePending: { borderWidth: 1, borderColor: "rgba(245,184,46,0.6)", borderStyle: "dashed" },
  bubbleText: { color: colors.text, fontSize: 14, lineHeight: 19 },
  controls: { position: "absolute", left: 0, right: 0, bottom: 0, height: 150, paddingHorizontal: 20, paddingBottom: 26, justifyContent: "flex-end", alignItems: "center", gap: 10 },
  hint: { color: colors.text, fontSize: 13, fontWeight: "500", textShadowColor: "rgba(0,0,0,0.8)", textShadowRadius: 3, textAlign: "center" },
  controlRow: { width: "100%", flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  sideBtn: { width: 56, height: 56, borderRadius: 28, backgroundColor: "rgba(26,29,35,0.85)", borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  sideBtnText: { color: colors.text, fontSize: 11, fontWeight: "600" },
  bigBtn: { width: 84, height: 84, borderRadius: 42, borderWidth: 5, alignItems: "center", justifyContent: "center" },
  micIcon: { width: 16, height: 30, borderRadius: 8, backgroundColor: colors.onAmber },
  stopIcon: { width: 28, height: 28, borderRadius: 6, backgroundColor: colors.bg },
  sheet: { position: "absolute", left: 12, right: 12, bottom: 160, padding: 18, borderRadius: 18, backgroundColor: "rgba(15,17,21,0.96)", borderWidth: 1, borderColor: colors.border, gap: 6 },
  sheetTitle: { color: colors.amber, fontSize: 12, fontWeight: "700", letterSpacing: 0.5 },
  sheetLabel: { color: colors.muted, fontSize: 11, fontWeight: "600", textTransform: "uppercase", letterSpacing: 0.6, marginTop: 8 },
  sheetText: { color: colors.text, fontSize: 15, lineHeight: 21 },
});
