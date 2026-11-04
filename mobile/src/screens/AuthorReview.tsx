// Author flow, step 2: what the compiler (photos) or the digester (a video, and the manual when
// one was given) made. It is already published; the author can try it, keep it, or discard it.
// A digested skill also shows where each step came from, when it happens in the video, the
// manual's notes, the points where video and manual disagree, and the expert clip per step.
import React, { useState } from "react";
import { Image, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Api, Skill, Step } from "../api";
import { ClipPlayer } from "../ClipPlayer";
import { clock, videoCredit } from "../format";
import { colors } from "../theme";
import { Card, Chip, GhostButton, PrimaryButton } from "../ui";

export function AuthorReviewScreen({ api, skill, onTry, onDone, onDiscarded }: { api: Api; skill: Skill; onTry: () => Promise<void>; onDone: () => void; onDiscarded: () => void }) {
  const [busy, setBusy] = useState<"" | "try" | "discard">("");
  const [error, setError] = useState("");
  const [playing, setPlaying] = useState<Step | null>(null);
  const gates = skill.steps.filter((st) => st.safety_gate);
  const digest = skill.digest ?? null;
  const conflicts = skill.conflicts ?? [];
  const playingUrl = playing ? api.mediaUrl(skill.id, playing.clip ?? null) : null;

  async function run(kind: "try" | "discard", fn: () => Promise<void>) {
    setBusy(kind);
    setError("");
    try {
      await fn();
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy("");
    }
  }

  return (
    <View style={s.root}>
      <ScrollView contentContainerStyle={s.content}>
        <Text style={s.kicker}>{digest ? "DIGESTED BY MUSE SPARK · PUBLISHED" : "COMPILED BY MUSE SPARK · PUBLISHED"}</Text>
        <Text style={s.h1}>{skill.title}</Text>
        <Text style={s.meta}>
          {skill.author} · {skill.domain} · {skill.steps.length} steps · {skill.est_minutes} min · difficulty {skill.difficulty}/5
        </Text>
        {digest ? (
          <Text style={s.meta}>
            From a {clock(digest.video_seconds)} video ({digest.chunks} part{digest.chunks === 1 ? "" : "s"})
            {digest.documents.length ? ` + ${digest.documents.join(", ")}` : ""}
          </Text>
        ) : null}
        {digest && videoCredit(digest) ? <Text style={s.meta}>{videoCredit(digest)}</Text> : null}

        <View style={s.chips}>
          {skill.tools.map((t) => (
            <Chip key={`t-${t}`}>{t}</Chip>
          ))}
          {skill.parts.map((p) => (
            <Chip key={`p-${p}`} color={colors.amber} style={{ borderColor: colors.amber }}>
              {p}
            </Chip>
          ))}
        </View>

        {gates.length ? (
          <Card style={{ borderColor: colors.amberDark }}>
            <Text style={{ color: colors.text, fontWeight: "600", marginBottom: 3 }}>
              {gates.length} safety gate{gates.length > 1 ? "s" : ""} detected
            </Text>
            {(digest ? gates : gates.slice(0, 1)).map((g) => (
              <Text key={g.n} style={{ color: colors.muted, fontSize: 13, lineHeight: 18 }}>
                {gateLine(g)}
              </Text>
            ))}
          </Card>
        ) : (
          <Card>
            <Text style={{ color: colors.muted, fontSize: 13, lineHeight: 18 }}>
              {digest
                ? "No safety gate detected. If this job needs power, gas or water off first, film that step or add the manual, then digest again."
                : "No safety gate detected. If this job needs power or gas off first, add that photo and compile again."}
            </Text>
          </Card>
        )}

        {conflicts.length ? (
          <Card style={{ borderColor: colors.unclear, gap: 10 }}>
            <Text style={{ color: colors.text, fontWeight: "600" }}>
              The video and the document disagree on {conflicts.length} point{conflicts.length > 1 ? "s" : ""}
            </Text>
            {conflicts.map((c, i) => (
              <View key={`${i}-${c.topic}`} style={{ gap: 2 }}>
                <Text style={{ color: colors.text, fontSize: 13, fontWeight: "600", lineHeight: 18 }}>{c.topic}</Text>
                <Text style={s.small}>Video: {c.video_says}</Text>
                <Text style={s.small}>Document: {c.document_says}</Text>
                <Text style={[s.small, { color: colors.pass }]}>Used: {c.resolution}</Text>
              </View>
            ))}
          </Card>
        ) : null}

        {skill.steps.map((st) => {
          const ref = api.mediaUrl(skill.id, st.reference_image);
          const clip = api.mediaUrl(skill.id, st.clip ?? null);
          const thumb = ref ? <Image source={{ uri: ref }} style={s.ref} /> : <View style={[s.ref, s.refEmpty]}><Text style={{ color: colors.dim, fontSize: 11 }}>no photo</Text></View>;
          const source = st.source ? SOURCE_LABEL[st.source] : null;
          return (
            <View key={st.n} style={s.step}>
              <View style={s.stepHead}>
                <Text style={s.stepN}>{st.n}</Text>
                <Text style={s.stepText}>{st.instruction}</Text>
              </View>
              <View style={{ flexDirection: "row", gap: 10 }}>
                {clip ? (
                  <Pressable onPress={() => setPlaying(st)} accessibilityRole="button" accessibilityLabel={`Play the expert's clip for step ${st.n}`}>
                    {thumb}
                    <View style={s.playBadge} pointerEvents="none">
                      <View style={s.playIcon} />
                    </View>
                  </Pressable>
                ) : (
                  thumb
                )}
                <View style={{ flex: 1, gap: 4 }}>
                  <Text style={s.label}>Done looks like</Text>
                  <Text style={s.small}>{st.expected_state}</Text>
                  <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 4, marginTop: 2 }}>
                    {source ? (
                      <Chip color={SOURCE_COLOR[st.source!]} style={{ height: 22, ...(st.source === "added" ? { borderColor: colors.amber } : {}) }}>
                        {source}
                      </Chip>
                    ) : null}
                    {typeof st.video_start_s === "number" && st.video_start_s >= 0 ? (
                      <Chip color={colors.muted} style={{ height: 22 }}>
                        at {clock(st.video_start_s)}
                      </Chip>
                    ) : null}
                    {st.safety_gate ? <Chip color={colors.amber} style={{ borderColor: colors.amber, height: 22 }}>gate</Chip> : null}
                    {st.watch_me ? <Chip color={colors.unclear} style={{ height: 22 }}>watch me</Chip> : null}
                    {st.sam_concepts.slice(0, 2).map((c) => (
                      <Chip key={c} color={colors.muted} style={{ height: 22 }}>
                        SAM: {c}
                      </Chip>
                    ))}
                  </View>
                </View>
              </View>
              {st.document_note ? (
                <View style={s.note}>
                  <Text style={s.label}>From the document</Text>
                  <Text style={s.small}>{st.document_note}</Text>
                </View>
              ) : null}
            </View>
          );
        })}

        <Text style={s.label}>Voice vocabulary</Text>
        <Text style={s.small}>{skill.vocabulary.join(" · ") || "none"}</Text>
        {error ? <Text style={s.error}>{error}</Text> : null}
      </ScrollView>
      <View style={s.footer}>
        <GhostButton label={busy === "discard" ? "…" : "Discard"} color={colors.fail} onPress={() => run("discard", async () => { await api.deleteSkill(skill.id); onDiscarded(); })} style={{ flex: 1 }} />
        <GhostButton label="Keep" onPress={onDone} style={{ flex: 1 }} />
        <PrimaryButton label={busy === "try" ? "Starting…" : "Try it"} onPress={() => run("try", onTry)} disabled={busy !== ""} style={{ flex: 1.4 }} />
      </View>
      {playing && playingUrl ? <ClipPlayer key={playingUrl} uri={playingUrl} title={`STEP ${playing.n} · THE EXPERT`} onClose={() => setPlaying(null)} /> : null}
    </View>
  );
}

const SOURCE_LABEL: Record<NonNullable<Step["source"]>, string> = {
  video: "video",
  document: "document",
  both: "video + document",
  added: "added by Athena",
};

const SOURCE_COLOR: Record<NonNullable<Step["source"]>, string> = {
  video: colors.text2,
  document: colors.unclear,
  both: colors.text2,
  added: colors.amber,
};

/** The gate card's line for one gate. A gate the video does not show (one from the document, or one
 *  Athena added) says where it came from, so the author is not left wondering why it is there. */
function gateLine(st: Step): string {
  const what = (st.gate_requirement ?? st.expected_state).trim();
  const origin =
    st.source === "document"
      ? "The document asks for this; the video does not show it."
      : st.source === "added"
        ? "Athena added this gate; neither the video nor the document covers it."
        : "";
  if (!origin) return `Learners cannot skip step ${st.n}: ${what}`;
  return `Learners cannot skip step ${st.n}: ${/[.!?]$/.test(what) ? what : `${what}.`} ${origin}`;
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  content: { paddingHorizontal: 20, paddingTop: 60, paddingBottom: 120, gap: 12 },
  kicker: { color: colors.pass, fontSize: 11, fontWeight: "700", letterSpacing: 0.8 },
  h1: { color: colors.text, fontSize: 26, fontWeight: "700", letterSpacing: -0.4, lineHeight: 32 },
  meta: { color: colors.muted, fontSize: 13 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  step: { gap: 8, paddingVertical: 10, borderTopWidth: 1, borderTopColor: colors.border },
  stepHead: { flexDirection: "row", gap: 10, alignItems: "flex-start" },
  stepN: { width: 24, height: 24, borderRadius: 12, backgroundColor: colors.surface2, color: colors.text, textAlign: "center", lineHeight: 24, fontSize: 12, fontWeight: "700", overflow: "hidden" },
  stepText: { flex: 1, color: colors.text, fontSize: 15, lineHeight: 21 },
  ref: { width: 92, height: 92, borderRadius: 10, backgroundColor: colors.surface2 },
  refEmpty: { alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: colors.border },
  playBadge: { position: "absolute", top: 26, left: 26, width: 40, height: 40, borderRadius: 20, backgroundColor: "rgba(15,17,21,0.75)", borderWidth: 1, borderColor: colors.amber, alignItems: "center", justifyContent: "center" },
  // A right-pointing triangle drawn with borders, nudged right so it looks centred in the circle.
  playIcon: { width: 0, height: 0, marginLeft: 4, borderTopWidth: 8, borderBottomWidth: 8, borderLeftWidth: 13, borderTopColor: "transparent", borderBottomColor: "transparent", borderLeftColor: colors.amber },
  note: { gap: 3, marginLeft: 34, paddingLeft: 10, borderLeftWidth: 2, borderLeftColor: colors.unclear },
  label: { color: colors.muted, fontSize: 11, fontWeight: "600", textTransform: "uppercase", letterSpacing: 0.6 },
  small: { color: colors.text2, fontSize: 13, lineHeight: 18 },
  error: { color: colors.fail, fontSize: 13 },
  footer: { position: "absolute", left: 0, right: 0, bottom: 0, padding: 16, paddingBottom: 30, flexDirection: "row", gap: 10, backgroundColor: colors.bg, borderTopWidth: 1, borderTopColor: colors.border },
});
