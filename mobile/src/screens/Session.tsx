// Push-to-talk session over the live camera feed.
// Tap the big button: record video + mic, and stream the mic to Voice Transcribe so the
// words appear while you talk. Tap again: the clip and the transcript go to the guide.
// The reply shows as a bubble and is spoken. The check button takes one photo and runs
// the strict verification; SAM 3.1 marks the part on a fail or unclear verdict.
import { useAudioStream } from "expo-audio";
import { CameraView, useCameraPermissions, useMicrophonePermissions } from "expo-camera";
import * as Speech from "expo-speech";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { Api, Highlight, Session, Skill, Verdict } from "../api";
import { HighlightOverlay } from "../HighlightOverlay";
import { LiveTranscript } from "../live";
import { colors } from "../theme";
import { GhostButton, PrimaryButton } from "../ui";

type Bubble = { id: number; who: "user" | "guide"; text: string; tone?: "pass" | "fail" | "unclear" | "gate" };
type Phase = "idle" | "recording" | "sending" | "checking" | "speaking";

const BUBBLE_OPACITY = [1, 0.9, 0.7, 0.5, 0.3, 0.15];

export function SessionScreen({ api, skill, session: initial, onExit }: { api: Api; skill: Skill; session: Session; onExit: () => void }) {
  const [camPerm, requestCam] = useCameraPermissions();
  const [micPerm, requestMic] = useMicrophonePermissions();
  const camera = useRef<CameraView>(null);
  const [cameraMode, setCameraMode] = useState<"video" | "picture">("video");
  const [phase, setPhase] = useState<Phase>("idle");
  const [session, setSession] = useState<Session>(initial);
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [seconds, setSeconds] = useState(0);
  const [showSteps, setShowSteps] = useState(false);
  const [liveText, setLiveText] = useState("");
  const [liveState, setLiveState] = useState<"off" | "on" | "failed">("off");
  const [overlay, setOverlay] = useState<{ photoUri: string; highlight: Highlight; color: string; caption: string } | null>(null);
  const nextId = useRef(1);
  const recordingPromise = useRef<Promise<{ uri: string } | undefined> | null>(null);
  const live = useRef<LiveTranscript | null>(null);
  const liveDisabled = useRef(false); // set when the mic stream and the video recorder cannot share the mic

  const step = useMemo(() => skill.steps.find((st) => st.n === session.current_step) ?? skill.steps[0], [skill, session.current_step]);
  const total = skill.steps.length;

  // Raw PCM from the mic while recording, forwarded to the transcription relay.
  const { stream: mic } = useAudioStream({
    sampleRate: 16000,
    channels: 1,
    encoding: "int16",
    onBuffer: (buf) => live.current?.push(buf.data, buf.sampleRate, buf.channels),
  });

  const push = useCallback((b: Omit<Bubble, "id">) => {
    setBubbles((prev) => [...prev, { ...b, id: nextId.current++ }].slice(-8));
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

  useEffect(() => {
    return () => {
      live.current?.close();
      try {
        mic.stop();
      } catch {}
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (phase !== "recording") {
      setSeconds(0);
      return;
    }
    const t = setInterval(() => setSeconds((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, [phase]);

  const phaseRef = useRef<Phase>("idle");
  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  async function startLive() {
    if (liveDisabled.current) return;
    const lt = new LiveTranscript(api.asrUrl(), skill.id, api.apiKey, {
      onPartial: (t) => setLiveText(t),
      onError: () => setLiveState("failed"),
    });
    live.current = lt;
    setLiveText("");
    setLiveState("on");
    lt.open(); // not awaited: audio is buffered until the relay is ready
    try {
      await mic.start();
    } catch {
      setLiveState("failed");
      lt.close();
      live.current = null;
    }
  }

  async function stopLive(): Promise<string> {
    try {
      mic.stop();
    } catch {}
    const lt = live.current;
    live.current = null;
    if (!lt) return "";
    const text = await lt.finish();
    lt.close();
    return text;
  }

  async function onTapRecord() {
    if (!camera.current) return;
    if (phase === "idle" || phase === "speaking") {
      Speech.stop();
      setOverlay(null);
      if (cameraMode !== "video") {
        setCameraMode("video");
        await wait(400);
      }
      setPhase("recording");
      await startLive();
      try {
        recordingPromise.current = camera.current.recordAsync({ maxDuration: 20 });
      } catch (e) {
        // The video recorder could not open the mic while the live stream holds it: give up on live.
        await stopLive();
        liveDisabled.current = true;
        setLiveState("failed");
        recordingPromise.current = camera.current.recordAsync({ maxDuration: 20 });
      }
      recordingPromise.current
        .then((r) => {
          if (r?.uri && phaseRef.current === "recording") sendClip(r.uri);
          else if (!r?.uri) {
            stopLive();
            setPhase("idle");
          }
        })
        .catch(async (e) => {
          await stopLive();
          if (!liveDisabled.current && live.current === null && liveState !== "failed") {
            // first failure with the live stream on: remember and let the user try again
            liveDisabled.current = true;
          }
          push({ who: "guide", text: friendly(e), tone: "fail" });
          setPhase("idle");
        });
      return;
    }
    if (phase === "recording") {
      camera.current.stopRecording();
    }
  }

  async function sendClip(uri: string) {
    setPhase("sending");
    const heard = await stopLive();
    const secs = Math.max(seconds, 1);
    const userLine = heard ? heard : `Clip sent · ${secs} s · transcribing your words…`;
    const userId = nextId.current++;
    setBubbles((prev) => [...prev, { id: userId, who: "user" as const, text: userLine }].slice(-8));
    setLiveText("");
    try {
      const res = await api.turn(session.id, { clipUri: uri, transcript: heard });
      if (!heard && res.transcript) {
        setBubbles((prev) => prev.map((b) => (b.id === userId ? { ...b, text: res.transcript } : b)));
      }
      push({ who: "guide", text: res.reply });
      say(res.reply);
    } catch (e: any) {
      push({ who: "guide", text: friendly(e), tone: "fail" });
      setPhase("idle");
    }
  }

  async function onTapCheck() {
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
      const res = await api.verify(session.id, photo.uri);
      const v = res.verdict;
      push({ who: "guide", text: verdictLine(v, step.safety_gate), tone: v.status });
      if (res.highlight && res.highlight.objects.length) {
        const color = toneColor(v.status);
        const caption = v.status === "pass" ? "Verified" : v.status === "fail" ? "Look here" : "This is what I see";
        setOverlay({ photoUri: photo.uri, highlight: res.highlight, color, caption });
      }
      setSession((s) => ({ ...s, current_step: res.current_step, completed: res.completed }));
      if (res.completed) {
        say(`${v.reason} That was the last step. Nice work.`);
      } else if (!res.advanced) {
        say(verdictSpeech(v));
      } else {
        Speech.speak(`Step ${step.n} verified.`, { language: "en-US" });
        setPhase("idle");
      }
    } catch (e: any) {
      push({ who: "guide", text: friendly(e), tone: "fail" });
      setPhase("idle");
    } finally {
      setCameraMode("video");
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
  const dismissOverlay = useCallback(() => setOverlay(null), []);

  return (
    <View style={s.root}>
      <CameraView ref={camera} style={StyleSheet.absoluteFill} facing="back" mode={cameraMode} mute={false} />

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
            <Text style={[s.pillText, { color: colors.fail }]}>REC {fmt(seconds)}</Text>
          </View>
        ) : (
          <View style={s.pill}>
            <View style={[s.dot, { backgroundColor: colors.pass }]} />
            <Text style={[s.pillText, { color: colors.muted }]}>Live</Text>
          </View>
        )}
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
            <View key={b.id} style={[s.bubble, b.who === "user" ? s.bubbleUser : s.bubbleGuide, tone ? { borderColor: tone, borderWidth: 1 } : null, { opacity }]}>
              <Text style={[s.bubbleText, b.who === "guide" && { color: colors.text2 }, tone ? { color: colors.text } : null]}>{b.text}</Text>
            </View>
          );
        })}
        {phase === "recording" ? (
          <View style={[s.bubble, s.bubbleUser, s.bubbleLive]}>
            <Text style={s.liveLabel}>{liveState === "failed" ? "LISTENING (transcript after you send)" : "LISTENING"}</Text>
            <Text style={s.bubbleText}>{liveText || (liveState === "failed" ? "Your words are transcribed from the clip." : "…")}</Text>
          </View>
        ) : null}
      </View>

      <View style={s.controls}>
        <Text style={s.hint}>{hintFor(phase, session.completed)}</Text>
        <View style={s.controlRow}>
          <Pressable onPress={() => setShowSteps(true)} style={s.sideBtn} accessibilityLabel="Show steps">
            <Text style={s.sideBtnText}>Steps</Text>
          </Pressable>

          {phase === "idle" || phase === "speaking" ? (
            <Pressable onPress={onTapRecord} style={[s.bigBtn, { backgroundColor: colors.amber, borderColor: colors.amberDark }]} accessibilityLabel="Tap to record a question with video" disabled={session.completed}>
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

function hintFor(phase: Phase, completed: boolean) {
  if (completed) return "All steps verified";
  switch (phase) {
    case "idle":
      return "Tap to record video and your question";
    case "recording":
      return "Recording video + voice · tap to send";
    case "sending":
      return "Clip sent to the guide…";
    case "checking":
      return "Checking this step from a photo…";
    case "speaking":
      return "Reply on screen and spoken aloud";
  }
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
  top: { position: "absolute", top: 54, left: 16, right: 16, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
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
  bubbleLive: { borderWidth: 1, borderColor: colors.amber, minWidth: 180 },
  liveLabel: { color: colors.amber, fontSize: 10, fontWeight: "700", letterSpacing: 0.6, marginBottom: 2 },
  bubbleText: { color: colors.text, fontSize: 14, lineHeight: 19 },
  controls: { position: "absolute", left: 0, right: 0, bottom: 0, height: 150, paddingHorizontal: 20, paddingBottom: 26, justifyContent: "flex-end", alignItems: "center", gap: 10 },
  hint: { color: colors.text, fontSize: 13, fontWeight: "500", textShadowColor: "rgba(0,0,0,0.8)", textShadowRadius: 3 },
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
