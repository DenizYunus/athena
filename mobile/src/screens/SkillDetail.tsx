import React, { useState } from "react";
import { Image, ScrollView, StyleSheet, Text, View } from "react-native";
import { Api, Skill } from "../api";
import { colors } from "../theme";
import { Card, Chip, GhostButton, PrimaryButton } from "../ui";

export function SkillDetailScreen({ api, skill, onStart, onBack }: { api: Api; skill: Skill; onStart: () => Promise<void>; onBack: () => void }) {
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const gates = skill.steps.filter((st) => st.safety_gate);

  async function start() {
    setStarting(true);
    setError("");
    try {
      await onStart();
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setStarting(false);
    }
  }

  return (
    <View style={s.root}>
      <ScrollView contentContainerStyle={s.content}>
        <GhostButton label="‹ Back" onPress={onBack} color={colors.muted} style={{ alignSelf: "flex-start", height: 40 }} />
        <Text style={s.h1}>{skill.title}</Text>
        <Text style={s.meta}>
          {skill.author} · {skill.domain}
        </Text>

        <View style={s.stats}>
          <Stat big={String(skill.steps.length)} small="steps" />
          <Stat big={`${skill.est_minutes}m`} small="typical" />
          <Stat big={`${skill.difficulty}/5`} small="difficulty" />
          <Stat big={String(gates.length)} small="gates" color={colors.amber} />
        </View>

        <Text style={s.label}>You'll need</Text>
        <View style={s.chips}>
          {skill.tools.map((t) => (
            <Chip key={t}>{t}</Chip>
          ))}
          {skill.parts.map((p) => (
            <Chip key={p} color={colors.amber} style={{ borderColor: colors.amber }}>
              {p}
            </Chip>
          ))}
        </View>

        {gates.length ? (
          <Card style={{ borderColor: colors.amberDark }}>
            <Text style={{ color: colors.text, fontWeight: "600", marginBottom: 3 }}>
              {gates.length} safety gate{gates.length > 1 ? "s" : ""}
            </Text>
            <Text style={{ color: colors.muted, fontSize: 13, lineHeight: 18 }}>
              Athena won't move past step {gates[0].n} until your camera shows: {gates[0].gate_requirement ?? gates[0].expected_state}
            </Text>
          </Card>
        ) : null}

        <Text style={s.label}>Steps</Text>
        {skill.steps.map((st) => (
          <View key={st.n} style={s.stepRow}>
            <Text style={s.stepN}>{st.n}</Text>
            <Text style={s.stepText}>{st.instruction}</Text>
            {api.mediaUrl(skill.id, st.reference_image) ? <Image source={{ uri: api.mediaUrl(skill.id, st.reference_image)! }} style={{ width: 44, height: 44, borderRadius: 8, backgroundColor: colors.surface2 }} /> : null}
          </View>
        ))}

        <Text style={[s.meta, { marginTop: 10 }]}>
          Push to talk: tap the big button, show and ask, tap again to send. The guide replies by voice. Tap the check button to verify a step from a photo.
        </Text>
        {error ? <Text style={s.error}>{error}</Text> : null}
      </ScrollView>
      <View style={s.footer}>
        <PrimaryButton label={starting ? "Starting…" : skill.price_usd ? `Install & start · $${skill.price_usd}` : "Start"} onPress={start} disabled={starting} />
      </View>
    </View>
  );
}

function Stat({ big, small, color = colors.text }: { big: string; small: string; color?: string }) {
  return (
    <View style={s.stat}>
      <Text style={{ color, fontSize: 17, fontWeight: "700" }}>{big}</Text>
      <Text style={{ color: colors.muted, fontSize: 11 }}>{small}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 20, paddingTop: 56, gap: 12, paddingBottom: 24 },
  h1: { color: colors.text, fontSize: 24, fontWeight: "700", lineHeight: 29, letterSpacing: -0.4 },
  meta: { color: colors.muted, fontSize: 13, lineHeight: 18 },
  stats: { flexDirection: "row", gap: 8 },
  stat: { flex: 1, padding: 10, borderRadius: 12, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border, alignItems: "center", gap: 2 },
  label: { color: colors.muted, fontSize: 12, fontWeight: "600", textTransform: "uppercase", letterSpacing: 0.6, marginTop: 6 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  stepRow: { flexDirection: "row", gap: 10, alignItems: "flex-start" },
  stepN: { width: 22, color: colors.muted, fontSize: 14, marginTop: 1 },
  stepText: { flex: 1, color: colors.text, fontSize: 14, lineHeight: 20 },
  footer: { padding: 20, paddingBottom: 28, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.bg },
  error: { color: colors.fail, fontSize: 13 },
});
