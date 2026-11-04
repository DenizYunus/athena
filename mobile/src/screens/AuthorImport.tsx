// Author flow, step 1: upload a video of the job, or paste a YouTube link to it, plus the written
// procedure (PDF) when there is one. The backend (POST /skills/digest) checks the files and answers at
// once with a job (for a link it downloads the video first, inside the job); Muse Spark
// then watches the video and reads the document there while this screen polls the job
// (GET /skills/digest/{id}) and lands on the review screen once the skill is published.
// Photographing the steps (AuthorCapture) stays as the second way in.
import * as DocumentPicker from "expo-document-picker";
import { File } from "expo-file-system";
import * as LegacyFileSystem from "expo-file-system/legacy";
import React, { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { Api, ApiError, DigestJob, PickedFile, Skill } from "../api";
import { megabytes } from "../format";
import { youtubeVideoId } from "../links";
import { colors, radius } from "../theme";
import { Card, GhostButton, PrimaryButton } from "../ui";

// The backend's defaults (DIGEST_MAX_UPLOAD_BYTES, DIGEST_MAX_DOCUMENTS, DIGEST_MAX_DOCUMENT_BYTES in
// backend/app/config.py). Checked here only to spare the author a long upload that would be turned
// away; the backend has the final say, and its sentence is shown when it refuses a file.
const MAX_VIDEO_BYTES = 1024 * 1024 * 1024;
const MAX_DOCUMENTS = 3;
const MAX_DOCUMENT_BYTES = 50 * 1024 * 1024;
const POLL_MS = 2000;
// Polls that may fail in a row (a Wi-Fi blip, a busy backend) before the screen says it lost touch.
// The digest keeps running on the backend either way.
const POLL_FAILURES = 5;

type Phase = "idle" | "picking" | "uploading" | "digesting" | "lost" | "failed";

export function AuthorImportScreen({ api, onCompiled, onBack, onPhotos }: { api: Api; onCompiled: (skill: Skill) => void; onBack: () => void; onPhotos: () => void }) {
  const [video, setVideo] = useState<PickedFile | null>(null);
  const [link, setLink] = useState("");
  const [docs, setDocs] = useState<PickedFile[]>([]);
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [uploaded, setUploaded] = useState(0);
  const [job, setJob] = useState<DigestJob | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const mounted = useRef(true);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const upload = useRef<AbortController | null>(null);
  const jobId = useRef<string | null>(null);
  const failures = useRef(0);
  // The picked files, for the cleanup on unmount (state in that closure would be stale).
  const picked = useRef<{ video: PickedFile | null; docs: PickedFile[] }>({ video: null, docs: [] });
  picked.current = { video, docs };

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (pollTimer.current) clearTimeout(pollTimer.current);
      // Leaving while the video is still being sent cancels the upload, and no job starts. Once the
      // whole video has been sent, the abort only drops the reply: the backend still checks the
      // video, starts the job and may publish the skill to the marketplace. A job that already
      // answered keeps running too and publishes its skill when it is done.
      upload.current?.abort();
      if (picked.current.video) discard(picked.current.video);
      picked.current.docs.forEach(discard);
    };
  }, []);

  const working = phase === "picking" || phase === "uploading" || phase === "digesting";
  // A picked file wins over a link; the link field shows only while no file is picked.
  const linkOk = !video && youtubeVideoId(link) !== null;
  const ready = !!video || linkOk;

  async function pickVideo() {
    if (working) return;
    // A cancelled pick puts the screen back as it was (a "Check again" after a lost poll included).
    const before = { phase, error };
    let changed = false;
    setError("");
    setNotice("");
    setPhase("picking");
    try {
      // No copy by the picker: on Android it would copy the whole video on the UI thread before
      // reporting its size, freezing the screen for a large file and leaving a partial copy behind
      // when the storage fills up. The size check below runs first, then copyToCache makes the copy
      // on a background thread.
      const res = await DocumentPicker.getDocumentAsync({ type: "video/*", copyToCacheDirectory: false });
      if (res.canceled || !res.assets?.length) return;
      changed = true;
      const a = res.assets[0];
      const file: PickedFile = { uri: a.uri, name: a.name, mimeType: a.mimeType, size: a.size };
      if (!mounted.current) {
        discard(file);
        return;
      }
      // A provider that reports no size leaves the check to the backend's own limit.
      if (file.size && file.size > MAX_VIDEO_BYTES) {
        discard(file);
        setError("This video is larger than 1 GB. Trim it or export it at a lower resolution, then choose it again.");
        return;
      }
      const local = await copyToCache(file);
      if (!mounted.current) {
        discard(local);
        return;
      }
      if (video && video.uri !== local.uri) discard(video);
      setVideo(local);
      setJob(null);
    } catch (e: any) {
      changed = true;
      if (mounted.current) setError(`The video could not be opened. ${message(e)}`);
    } finally {
      if (mounted.current) {
        setPhase(changed ? "idle" : before.phase);
        if (!changed) setError(before.error);
      }
    }
  }

  async function pickDocuments() {
    const room = MAX_DOCUMENTS - docs.length;
    if (working || room <= 0) return;
    const before = { phase, error };
    let changed = false;
    setError("");
    setNotice("");
    setPhase("picking");
    try {
      const res = await DocumentPicker.getDocumentAsync({ type: "application/pdf", multiple: true, copyToCacheDirectory: true });
      if (res.canceled || !res.assets?.length) return;
      changed = true;
      const files: PickedFile[] = res.assets.map((a) => ({ uri: a.uri, name: a.name, mimeType: a.mimeType, size: a.size }));
      if (!mounted.current) {
        files.forEach(discard);
        return;
      }
      const fresh = files.filter((f) => !docs.some((d) => d.name === f.name && d.size === f.size));
      const tooBig = fresh.filter((f) => (f.size ?? 0) > MAX_DOCUMENT_BYTES);
      const fits = fresh.filter((f) => (f.size ?? 0) <= MAX_DOCUMENT_BYTES);
      const keep = fits.slice(0, room);
      const over = fits.slice(room);
      files.filter((f) => !keep.includes(f)).forEach(discard);
      const notes: string[] = [];
      if (tooBig.length) notes.push(`${tooBig.map((f) => f.name).join(", ")} ${tooBig.length === 1 ? "is" : "are"} larger than 50 MB, so Athena left ${tooBig.length === 1 ? "it" : "them"} out.`);
      if (over.length) notes.push(`Athena reads up to ${MAX_DOCUMENTS} documents, so ${over.length} ${over.length === 1 ? "was" : "were"} left out.`);
      setNotice(notes.join(" "));
      if (keep.length) setDocs((prev) => [...prev, ...keep]);
    } catch (e: any) {
      changed = true;
      if (mounted.current) setError(`The document could not be opened. ${message(e)}`);
    } finally {
      if (mounted.current) {
        setPhase(changed ? "idle" : before.phase);
        if (!changed) setError(before.error);
      }
    }
  }

  /** Back to the link field. Like picking another video, it leaves a lost or failed digest behind. */
  function removeVideo() {
    if (working || !video) return;
    discard(video);
    setVideo(null);
    setJob(null);
    setError("");
    setPhase("idle");
  }

  function removeDocument(doc: PickedFile) {
    if (working) return;
    discard(doc);
    setDocs((prev) => prev.filter((d) => d !== doc));
    setNotice("");
  }

  async function digest() {
    if (!ready || working) return;
    setError("");
    setNotice("");
    setJob(null);
    setUploaded(0);
    setPhase("uploading");
    jobId.current = null;
    const ctl = new AbortController();
    upload.current = ctl;
    let shown = -1;
    try {
      const started = await api.startDigest(
        video,
        video ? "" : link.trim(),
        docs,
        author.trim() || "You",
        title.trim(),
        (fraction) => {
          // Progress events arrive many times a second; re-render only when the percent changes.
          const pct = Math.floor(fraction * 100);
          if (pct === shown || !mounted.current) return;
          shown = pct;
          setUploaded(fraction);
        },
        ctl.signal,
      );
      upload.current = null;
      if (!mounted.current) return;
      jobId.current = started.id;
      failures.current = 0;
      setJob(started);
      follow(started);
    } catch (e: any) {
      upload.current = null;
      if (!mounted.current) return;
      setError(message(e));
      setPhase("failed");
    }
  }

  /** Act on a job snapshot: done goes to review, failed shows the backend's sentence, running polls again. */
  function follow(j: DigestJob) {
    if (j.status === "done") {
      if (j.skill) {
        onCompiled(j.skill);
      } else {
        setError("The digest finished but the backend sent no skill. Try again.");
        setPhase("failed");
      }
      return;
    }
    if (j.status === "failed") {
      setError(j.error || "The digest failed. Try again.");
      setPhase("failed");
      return;
    }
    setPhase("digesting");
    if (pollTimer.current) clearTimeout(pollTimer.current);
    pollTimer.current = setTimeout(poll, POLL_MS);
  }

  async function poll() {
    const id = jobId.current;
    if (!id || !mounted.current) return;
    try {
      const j = await api.getDigest(id);
      if (!mounted.current || jobId.current !== id) return;
      failures.current = 0;
      setJob(j);
      follow(j);
    } catch (e: any) {
      if (!mounted.current || jobId.current !== id) return;
      if (e instanceof ApiError && e.status === 404) {
        // Jobs live in the backend's memory; a restart forgets them.
        jobId.current = null;
        setError("The backend no longer has this digest, probably because it restarted. Digest the video again.");
        setPhase("failed");
        return;
      }
      failures.current += 1;
      if (failures.current >= POLL_FAILURES) {
        setError(`Lost touch with the backend while it works on the video. ${message(e)}`);
        setPhase("lost");
        return;
      }
      pollTimer.current = setTimeout(poll, POLL_MS);
    }
  }

  /** After "lost": the job was never cancelled, so ask about it again instead of uploading again. */
  function checkAgain() {
    if (!jobId.current) return;
    failures.current = 0;
    setError("");
    setPhase("digesting");
    poll();
  }

  const pct = Math.floor(uploaded * 100);
  // With a link, only the PDFs (if any) go up from the phone.
  const sending = !!video || docs.length > 0;
  const sendingLabel = !video
    ? docs.length
      ? uploaded >= 0.999
        ? "Sent. The backend is checking the manual."
        : `Sending the link and the manual ${pct}%`
      : "Sending the link to the backend."
    : uploaded >= 0.999
      ? "Upload done. The backend is checking the video."
      : `Uploading ${pct}%`;
  const primaryLabel = phase === "uploading" ? "Uploading…" : phase === "digesting" ? "Digesting…" : phase === "lost" ? "Check again" : phase === "failed" ? "Try again" : "Digest the video";

  return (
    <View style={s.root}>
      <ScrollView contentContainerStyle={s.content} keyboardShouldPersistTaps="handled">
        <GhostButton label="‹ Back" onPress={onBack} color={colors.muted} style={{ alignSelf: "flex-start", height: 40 }} />
        <Text style={s.kicker}>TEACH A SKILL</Text>
        <Text style={s.h1}>Add a video of the job</Text>
        <Text style={s.lead}>Muse Spark watches it, writes the steps, safety gates and checks, and takes a reference photo and a short clip of you for each step.</Text>

        <Text style={s.label}>The video</Text>
        {video ? (
          <Card style={s.fileCard}>
            <View style={{ flex: 1, gap: 2 }}>
              <Text style={s.fileName} numberOfLines={2}>
                {video.name}
              </Text>
              <Text style={s.fileMeta}>{video.size ? megabytes(video.size) : "size unknown"}</Text>
            </View>
            <Pressable onPress={pickVideo} disabled={working} hitSlop={10} accessibilityRole="button" accessibilityLabel="Choose a different video">
              <Text style={[s.link, working && { opacity: 0.5 }]}>Change</Text>
            </Pressable>
            <Pressable onPress={removeVideo} disabled={working} hitSlop={10} accessibilityRole="button" accessibilityLabel="Remove the video">
              <Text style={[s.link, { color: colors.fail }, working && { opacity: 0.5 }]}>Remove</Text>
            </Pressable>
          </Card>
        ) : (
          <>
          <Pressable onPress={pickVideo} disabled={working} style={({ pressed }) => [s.drop, pressed && { opacity: 0.85 }]} accessibilityRole="button" accessibilityLabel="Choose the video">
            {phase === "picking" ? (
              <>
                <ActivityIndicator color={colors.amber} />
                <Text style={s.dropHint}>Opening the file…</Text>
              </>
            ) : (
              <>
                <Text style={s.dropTitle}>Choose the video</Text>
                <Text style={s.dropHint}>From your videos or files. Up to 1 GB and 60 minutes.</Text>
              </>
            )}
          </Pressable>
          <Text style={s.or}>or paste a YouTube link</Text>
          <TextInput
            value={link}
            onChangeText={setLink}
            editable={!working}
            placeholder="https://youtu.be/…"
            placeholderTextColor={colors.dim}
            style={s.input}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
            returnKeyType="done"
            accessibilityLabel="YouTube link"
          />
          {link.trim() && !linkOk ? <Text style={s.small}>That is not a link to one YouTube video. Copy it from YouTube's Share button.</Text> : null}
          </>
        )}

        <Text style={s.label}>The manual (optional)</Text>
        {docs.map((d) => (
          <Card key={d.uri} style={s.fileCard}>
            <View style={{ flex: 1, gap: 2 }}>
              <Text style={s.fileName} numberOfLines={2}>
                {d.name}
              </Text>
              <Text style={s.fileMeta}>{d.size ? megabytes(d.size) : "PDF"}</Text>
            </View>
            <Pressable onPress={() => removeDocument(d)} disabled={working} hitSlop={10} accessibilityRole="button" accessibilityLabel={`Remove ${d.name}`}>
              <Text style={[s.link, { color: colors.fail }, working && { opacity: 0.5 }]}>Remove</Text>
            </Pressable>
          </Card>
        ))}
        {docs.length < MAX_DOCUMENTS ? (
          <GhostButton label={docs.length ? "Add another PDF" : "Add the manual (PDF)"} onPress={pickDocuments} disabled={working} color={colors.amber} />
        ) : (
          <Text style={s.small}>Athena reads up to {MAX_DOCUMENTS} documents.</Text>
        )}
        {notice ? <Text style={s.small}>{notice}</Text> : null}

        <TextInput value={title} onChangeText={setTitle} editable={!working} placeholder="What is this skill? (optional)" placeholderTextColor={colors.dim} style={s.input} />
        <TextInput value={author} onChangeText={setAuthor} editable={!working} placeholder="Your name (optional)" placeholderTextColor={colors.dim} style={s.input} />

        <Text style={s.small}>Use a video you filmed or have permission to use, whether you upload it or link it. A narrated video works best, and the manual adds safety steps and specs.</Text>
      </ScrollView>

      <View style={s.footer}>
        {phase === "uploading" ? <Progress label={sendingLabel} value={sending ? uploaded : 0} /> : null}
        {phase === "uploading" && sending && uploaded >= 0.999 ? <Text style={s.small}>Leaving now does not cancel the digest. If the backend accepts the video, the skill is published to the marketplace when it is done.</Text> : null}
        {phase === "digesting" || phase === "lost" ? (
          <>
            <Progress label={job?.message || "Muse Spark is starting."} value={job?.progress ?? 0} />
            <Text style={s.small}>This usually takes a few minutes. You can leave this screen; the skill is published to the marketplace when it is done.</Text>
          </>
        ) : null}
        {error ? <Text style={s.error}>{error}</Text> : null}
        <PrimaryButton label={primaryLabel} onPress={phase === "lost" ? checkAgain : digest} disabled={phase === "lost" ? false : !ready || working} />
        <Pressable onPress={onPhotos} hitSlop={8} style={{ alignSelf: "center" }} accessibilityRole="button">
          <Text style={s.secondary}>Photograph the steps instead</Text>
        </Pressable>
      </View>
    </View>
  );
}

function Progress({ label, value }: { label: string; value: number }) {
  const v = Math.min(1, Math.max(0, value));
  return (
    <View style={{ gap: 6 }}>
      <Text style={s.progressLabel}>{label}</Text>
      <View style={s.track}>
        <View style={[s.fill, { width: `${Math.round(v * 100)}%` }]} />
      </View>
    </View>
  );
}

/** Copy a video picked on Android from its content:// URI into the picker's cache folder, on a
 *  background thread. React Native sizes a content:// upload by what the stream says is available,
 *  which is wrong for a provider that streams through a pipe (a video kept only in the cloud), and
 *  the upload then fails; a local copy always has its true length. When the copy fails (storage
 *  full), the video is uploaded from the provider directly, which works for files on the device. */
async function copyToCache(file: PickedFile): Promise<PickedFile> {
  const cache = LegacyFileSystem.cacheDirectory;
  if (Platform.OS !== "android" || !file.uri.startsWith("content://") || !cache) return file;
  const dir = `${cache}DocumentPicker/`;
  const ext = /\.[A-Za-z0-9]{1,5}$/.exec(file.name)?.[0] ?? ".mp4";
  const to = `${dir}video-${Date.now()}${ext}`;
  try {
    await LegacyFileSystem.makeDirectoryAsync(dir, { intermediates: true });
    await LegacyFileSystem.copyAsync({ from: file.uri, to });
    return { ...file, uri: to };
  } catch {
    await LegacyFileSystem.deleteAsync(to, { idempotent: true }).catch(() => {});
    return file;
  }
}

/** Delete the app's cached copy of a picked file. Only copies inside the picker's cache folder are
 *  touched, never a file handed over in place (a content:// URI belongs to the app that provides
 *  it, and on iOS the picker's own copy sits in the system's tmp folder). */
function discard(file: PickedFile) {
  if (!file.uri.startsWith("file://") || !file.uri.includes("/DocumentPicker/")) return;
  try {
    new File(file.uri).delete();
  } catch {}
}

function message(e: any): string {
  const m: string = e?.message ?? String(e);
  // The backend answers 503 only when it has no Meta API key at all.
  if (e?.status === 503) return "No API key yet. Open Settings and enter your Meta Model API key.";
  if (m.includes("Network request failed")) return "Can't reach the backend. Check the URL in Settings and that uvicorn is running with --host 0.0.0.0.";
  // The backend's refusals (413, 422) and failed Meta calls (502, 504) are sentences meant for the
  // author; drop the status prefix the client puts in front.
  return m.replace(/^\d{3}: /, "");
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 20, paddingTop: 56, gap: 12, paddingBottom: 24 },
  kicker: { color: colors.amber, fontSize: 11, fontWeight: "700", letterSpacing: 0.8, marginTop: 4 },
  h1: { color: colors.text, fontSize: 26, fontWeight: "700", letterSpacing: -0.4, lineHeight: 32 },
  lead: { color: colors.text2, fontSize: 14, lineHeight: 20 },
  label: { color: colors.muted, fontSize: 12, fontWeight: "600", textTransform: "uppercase", letterSpacing: 0.6, marginTop: 8 },
  drop: { minHeight: 110, borderRadius: radius.lg, borderWidth: 1.5, borderStyle: "dashed", borderColor: colors.amber, backgroundColor: colors.amberDark, alignItems: "center", justifyContent: "center", gap: 6, padding: 16 },
  dropTitle: { color: colors.amber, fontSize: 17, fontWeight: "700" },
  dropHint: { color: colors.text2, fontSize: 12, textAlign: "center" },
  or: { color: colors.muted, fontSize: 13, textAlign: "center" },
  fileCard: { flexDirection: "row", alignItems: "center", gap: 12 },
  fileName: { color: colors.text, fontSize: 15, fontWeight: "600", lineHeight: 20 },
  fileMeta: { color: colors.muted, fontSize: 12 },
  link: { color: colors.amber, fontSize: 13, fontWeight: "700" },
  input: { height: 46, borderRadius: radius.md, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border, color: colors.text, paddingHorizontal: 12, fontSize: 15 },
  small: { color: colors.muted, fontSize: 13, lineHeight: 18 },
  footer: { padding: 20, paddingBottom: 28, gap: 12, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.bg },
  progressLabel: { color: colors.text, fontSize: 14, fontWeight: "600", lineHeight: 19 },
  track: { height: 6, borderRadius: 3, backgroundColor: colors.surface2, overflow: "hidden" },
  fill: { height: 6, borderRadius: 3, backgroundColor: colors.amber },
  error: { color: colors.fail, fontSize: 13, lineHeight: 18 },
  secondary: { color: colors.muted, fontSize: 14, fontWeight: "600", textDecorationLine: "underline", paddingVertical: 4 },
});
