// Author flow, step 1: photograph the job in order and dictate a note for each photo.
// The note is transcribed live (Voice Transcribe over the relay); if the live socket
// fails, the WAV the mic wrote is sent to the batch endpoint instead. Compile sends
// everything to Muse Spark and lands on the review screen.
import { useAudioStream } from "expo-audio";
import { CameraView, useCameraPermissions, useMicrophonePermissions } from "expo-camera";
import React, { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Image, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { Api, Capture, Skill } from "../api";
import { LiveTranscript } from "../live";
import { colors, radius } from "../theme";
import { GhostButton, PrimaryButton } from "../ui";

type Item = Capture & { id: number };

export function AuthorCaptureScreen({ api, onCompiled, onBack }: { api: Api; onCompiled: (skill: Skill) => void; onBack: () => void }) {
  const [camPerm, requestCam] = useCameraPermissions();
  const [micPerm, requestMic] = useMicrophonePermissions();
  const camera = useRef<CameraView>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [recording, setRecording] = useState(false);
  const [liveText, setLiveText] = useState("");
  const [busy, setBusy] = useState<"" | "snap" | "note" | "compile">("");
  const [error, setError] = useState("");
  const [author, setAuthor] = useState("");
  const [title, setTitle] = useState("");
  const [showMeta, setShowMeta] = useState(false);
  const nextId = useRef(1);
  const live = useRef<LiveTranscript | null>(null);
  const fileRecording = useRef(false);

  const { stream: mic } = useAudioStream({
    sampleRate: 16000,
    channels: 1,
    encoding: "int16",
    onBuffer: (buf) => live.current?.push(buf.data, buf.sampleRate, buf.channels),
  });

  useEffect(() => {
    (async () => {
      if (!camPerm?.granted) await requestCam();
      if (!micPerm?.granted) await requestMic();
    })();
    return () => {
      live.current?.close();
      try {
        mic.stop();
      } catch {}
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const current = items.find((i) => i.id === selected) ?? null;

  async function snap() {
    if (!camera.current || busy) return;
    setBusy("snap");
    setError("");
    try {
      const photo = await camera.current.takePictureAsync({ quality: 0.85, skipProcessing: true });
      if (!photo?.uri) throw new Error("no photo");
      const item: Item = { id: nextId.current++, photoUri: photo.uri, note: "" };
      setItems((prev) => [...prev, item]);
      setSelected(item.id);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy("");
    }
  }

  async function startNote() {
    if (busy || recording) return;
    setError("");
    if (!current) {
      // A note with no photo is allowed (e.g. "then put it all back together").
      const item: Item = { id: nextId.current++, photoUri: null, note: "" };
      setItems((prev) => [...prev, item]);
      setSelected(item.id);
    }
    const lt = new LiveTranscript(api.asrUrl(), "", api.apiKey, { onPartial: setLiveText });
    live.current = lt;
    setLiveText("");
    lt.open();
    try {
      await mic.start();
      try {
        await mic.startFileRecordingAsync({ format: "wav" });
        fileRecording.current = true;
      } catch {
        fileRecording.current = false;
      }
      setRecording(true);
    } catch (e: any) {
      lt.close();
      live.current = null;
      setError(`Microphone: ${e.message ?? e}`);
    }
  }

  async function stopNote() {
    if (!recording) return;
    setRecording(false);
    setBusy("note");
    let wavUri: string | null = null;
    try {
      if (fileRecording.current) {
        const r = await mic.stopFileRecordingAsync();
        wavUri = r.uri;
      }
    } catch {}
    fileRecording.current = false;
    try {
      mic.stop();
    } catch {}
    const lt = live.current;
    live.current = null;
    let text = lt ? await lt.finish() : "";
    lt?.close();
    if (!text && wavUri) {
      try {
        text = await api.authorTranscribe(wavUri);
      } catch (e: any) {
        setError(`Transcription: ${e.message ?? e}`);
      }
    }
    setLiveText("");
    if (text) setNote(text);
    setBusy("");
  }

  function setNote(note: string) {
    const id = selected;
    if (id === null) return;
    setItems((prev) => prev.map((i) => (i.id === id ? { ...i, note } : i)));
  }

  function remove(id: number) {
    setItems((prev) => prev.filter((i) => i.id !== id));
    if (selected === id) setSelected(null);
  }

  async function compile() {
    if (busy) return;
    const usable = items.filter((i) => i.photoUri || i.note.trim());
    if (!usable.length) {
      setError("Take at least one photo first.");
      return;
    }
    setBusy("compile");
    setError("");
    try {
      const skill = await api.compile(usable, author.trim() || "You", title.trim());
      onCompiled(skill);
    } catch (e: any) {
      setError(e.message ?? String(e));
      setBusy("");
    }
  }

  if (!camPerm?.granted) {
    return (
      <View style={s.center}>
        <Text style={s.permText}>Athena needs the camera to photograph your steps.</Text>
        <PrimaryButton label="Allow camera" onPress={requestCam} style={{ alignSelf: "stretch" }} />
        <GhostButton label="Back" onPress={onBack} color={colors.muted} />
      </View>
    );
  }

  const photos = items.filter((i) => i.photoUri).length;

  return (
    <View style={s.root}>
      <CameraView ref={camera} style={StyleSheet.absoluteFill} facing="back" mode="picture" />

      <View style={s.top}>
        <Pressable onPress={onBack} style={s.roundBtn} accessibilityLabel="Back">
          <Text style={s.roundBtnText}>‹</Text>
        </Pressable>
        <View style={s.pill}>
          <Text style={s.pillText}>Teach a skill · {photos} photo{photos === 1 ? "" : "s"}</Text>
        </View>
        <Pressable onPress={() => setShowMeta((v) => !v)} style={s.roundBtn} accessibilityLabel="Title and author">
          <Text style={s.roundBtnText}>Aa</Text>
        </Pressable>
      </View>

      {showMeta ? (
        <View style={s.metaBox}>
          <TextInput value={title} onChangeText={setTitle} placeholder="What is this skill? (optional)" placeholderTextColor={colors.dim} style={s.input} />
          <TextInput value={author} onChangeText={setAuthor} placeholder="Your name" placeholderTextColor={colors.dim} style={s.input} />
        </View>
      ) : null}

      {/* note editor for the selected capture */}
      <View style={s.notePanel}>
        {current ? (
          <>
            <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
              <Text style={s.noteLabel}>{current.photoUri ? `Photo ${items.filter((i) => i.photoUri).findIndex((i) => i.id === current.id) + 1}` : "Note without a photo"}</Text>
              <Pressable onPress={() => remove(current.id)} hitSlop={10}>
                <Text style={{ color: colors.fail, fontSize: 12, fontWeight: "600" }}>Remove</Text>
              </Pressable>
            </View>
            {recording ? (
              <Text style={[s.noteText, { color: colors.amber }]}>{liveText || "Listening…"}</Text>
            ) : (
              <TextInput value={current.note} onChangeText={setNote} placeholder="Hold the mic and say what you did, or type here" placeholderTextColor={colors.dim} multiline style={[s.noteText, s.noteInput]} />
            )}
          </>
        ) : (
          <Text style={s.noteHint}>Snap a photo of each step in order. Then hold the mic and say what you did and what to watch for.</Text>
        )}
      </View>

      {/* capture strip */}
      <ScrollView horizontal style={s.strip} contentContainerStyle={{ gap: 8, paddingHorizontal: 16 }} showsHorizontalScrollIndicator={false}>
        {items.map((i, idx) => (
          <Pressable key={i.id} onPress={() => setSelected(i.id)} style={[s.thumb, selected === i.id && { borderColor: colors.amber }]}>
            {i.photoUri ? <Image source={{ uri: i.photoUri }} style={s.thumbImg} /> : <Text style={{ color: colors.muted, fontSize: 18 }}>Aa</Text>}
            <View style={[s.thumbBadge, { backgroundColor: i.note ? colors.pass : "rgba(15,17,21,0.8)" }]}>
              <Text style={{ color: i.note ? colors.onAmber : colors.text, fontSize: 10, fontWeight: "700" }}>{idx + 1}</Text>
            </View>
          </Pressable>
        ))}
      </ScrollView>

      {error ? <Text style={s.error}>{error}</Text> : null}

      <View style={s.controls}>
        <Pressable
          onPressIn={startNote}
          onPressOut={stopNote}
          style={[s.sideBtn, recording && { backgroundColor: colors.fail, borderColor: colors.fail }]}
          accessibilityLabel="Hold to dictate a note for the selected photo"
          disabled={busy !== ""}
        >
          {busy === "note" ? <ActivityIndicator color={colors.amber} /> : <View style={[s.micIcon, recording && { backgroundColor: colors.bg }]} />}
        </Pressable>
        <Pressable onPress={snap} style={s.bigBtn} accessibilityLabel="Take a photo of this step" disabled={busy !== "" || recording}>
          {busy === "snap" ? <ActivityIndicator color={colors.onAmber} /> : <View style={s.shutter} />}
        </Pressable>
        <Pressable onPress={compile} style={[s.sideBtn, { borderColor: colors.pass, minWidth: 72 }]} accessibilityLabel="Compile the skill" disabled={busy !== "" || recording}>
          {busy === "compile" ? <ActivityIndicator color={colors.pass} /> : <Text style={{ color: colors.pass, fontWeight: "700", fontSize: 13 }}>Compile</Text>}
        </Pressable>
      </View>
      <Text style={s.hint}>{busy === "compile" ? "Muse Spark is writing the steps, gates and checks…" : recording ? "Release to stop" : "Hold mic to talk · tap shutter to add a photo"}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface2 },
  center: { flex: 1, backgroundColor: colors.bg, padding: 24, justifyContent: "center", gap: 14 },
  permText: { color: colors.text, fontSize: 17, textAlign: "center", lineHeight: 24 },
  top: { position: "absolute", top: 54, left: 16, right: 16, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  roundBtn: { width: 44, height: 44, borderRadius: 22, backgroundColor: "rgba(15,17,21,0.75)", alignItems: "center", justifyContent: "center" },
  roundBtnText: { color: colors.text, fontSize: 18, fontWeight: "600" },
  pill: { height: 32, paddingHorizontal: 12, borderRadius: 16, backgroundColor: "rgba(15,17,21,0.75)", flexDirection: "row", alignItems: "center" },
  pillText: { color: colors.text, fontSize: 13, fontWeight: "600" },
  metaBox: { position: "absolute", top: 106, left: 16, right: 16, gap: 8 },
  input: { height: 44, borderRadius: radius.md, backgroundColor: "rgba(15,17,21,0.9)", borderWidth: 1, borderColor: colors.border, color: colors.text, paddingHorizontal: 12, fontSize: 15 },
  notePanel: { position: "absolute", left: 16, right: 16, bottom: 236, padding: 12, borderRadius: 14, backgroundColor: "rgba(15,17,21,0.9)", borderWidth: 1, borderColor: colors.border, gap: 6, minHeight: 70 },
  noteLabel: { color: colors.amber, fontSize: 11, fontWeight: "700", letterSpacing: 0.5 },
  noteText: { color: colors.text, fontSize: 14, lineHeight: 19 },
  noteInput: { minHeight: 40, maxHeight: 100, padding: 0 },
  noteHint: { color: colors.text2, fontSize: 13, lineHeight: 18 },
  strip: { position: "absolute", left: 0, right: 0, bottom: 160, maxHeight: 70 },
  thumb: { width: 64, height: 64, borderRadius: 10, borderWidth: 2, borderColor: "rgba(255,255,255,0.25)", overflow: "hidden", backgroundColor: "rgba(15,17,21,0.8)", alignItems: "center", justifyContent: "center" },
  thumbImg: { width: "100%", height: "100%" },
  thumbBadge: { position: "absolute", top: 3, left: 3, width: 18, height: 18, borderRadius: 9, alignItems: "center", justifyContent: "center" },
  error: { position: "absolute", left: 16, right: 16, bottom: 142, color: colors.fail, fontSize: 12, textAlign: "center", textShadowColor: "rgba(0,0,0,0.8)", textShadowRadius: 3 },
  controls: { position: "absolute", left: 20, right: 20, bottom: 50, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  sideBtn: { width: 60, height: 60, borderRadius: 30, backgroundColor: "rgba(26,29,35,0.85)", borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  micIcon: { width: 14, height: 26, borderRadius: 7, backgroundColor: colors.amber },
  bigBtn: { width: 84, height: 84, borderRadius: 42, borderWidth: 5, borderColor: colors.amberDark, backgroundColor: colors.amber, alignItems: "center", justifyContent: "center" },
  shutter: { width: 60, height: 60, borderRadius: 30, borderWidth: 3, borderColor: colors.onAmber },
  hint: { position: "absolute", left: 0, right: 0, bottom: 22, textAlign: "center", color: colors.text, fontSize: 13, fontWeight: "500", textShadowColor: "rgba(0,0,0,0.8)", textShadowRadius: 3 },
});
