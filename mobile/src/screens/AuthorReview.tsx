// Author flow, step 2: what the compiler made. It is already published; the author can
// try it, keep it, or discard it.
import React, { useState } from "react";
import { Image, ScrollView, StyleSheet, Text, View } from "react-native";
import { Api, Skill } from "../api";
import { colors } from "../theme";
import { Card, Chip, GhostButton, PrimaryButton } from "../ui";

export function AuthorReviewScreen({ api, skill, onTry, onDone, onDiscarded }: { api: Api; skill: Skill; onTry: () => Promise<void>; onDone: () => void; onDiscarded: () => void }) {
  const [busy, setBusy] = useState<"" | "try" | "discard">("");
  const [error, setError] = useState("");
  const gates = skill.steps.filter((st) => st.safety_gate);

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
        <Text style={s.kicker}>COMPILED BY MUSE SPARK · PUBLISHED</Text>
        <Text style={s.h1}>{skill.title}</Text>
        <Text style={s.meta}>
          {skill.author} · {skill.domain} · {skill.steps.length} steps · {skill.est_minutes} min · difficulty {skill.difficulty}/5
        </Text>

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
            <Text style={{ color: colors.muted, fontSize: 13, lineHeight: 18 }}>Learners cannot skip step {gates[0].n}: {gates[0].gate_requirement ?? gates[0].expected_state}</Text>
          </Card>
        ) : (
          <Card>
            <Text style={{ color: colors.muted, fontSize: 13, lineHeight: 18 }}>No safety gate detected. If this job needs power or gas off first, add that photo and compile again.</Text>
          </Card>
        )}

        {skill.steps.map((st) => {
          const ref = api.mediaUrl(skill.id, st.reference_image);
          return (
            <View key={st.n} style={s.step}>
              <View style={s.stepHead}>
                <Text style={s.stepN}>{st.n}</Text>
                <Text style={s.stepText}>{st.instruction}</Text>
              </View>
              <View style={{ flexDirection: "row", gap: 10 }}>
                {ref ? <Image source={{ uri: ref }} style={s.ref} /> : <View style={[s.ref, s.refEmpty]}><Text style={{ color: colors.dim, fontSize: 11 }}>no photo</Text></View>}
                <View style={{ flex: 1, gap: 4 }}>
                  <Text style={s.label}>Done looks like</Text>
                  <Text style={s.small}>{st.expected_state}</Text>
                  <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 4, marginTop: 2 }}>
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
    </View>
  );
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
  label: { color: colors.muted, fontSize: 11, fontWeight: "600", textTransform: "uppercase", letterSpacing: 0.6 },
  small: { color: colors.text2, fontSize: 13, lineHeight: 18 },
  error: { color: colors.fail, fontSize: 13 },
  footer: { position: "absolute", left: 0, right: 0, bottom: 0, padding: 16, paddingBottom: 30, flexDirection: "row", gap: 10, backgroundColor: colors.bg, borderTopWidth: 1, borderTopColor: colors.border },
});
