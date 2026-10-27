import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, FlatList, Image, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import { Api, Skill } from "../api";
import { colors, radius } from "../theme";
import { Chip, GhostButton, PrimaryButton } from "../ui";

export function MarketplaceScreen({ api, onOpen, onSettings, onCreate }: { api: Api; onOpen: (skill: Skill) => void; onSettings: () => void; onCreate: () => void }) {
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [error, setError] = useState<string>("");
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setSkills(await api.listSkills());
    } catch (e: any) {
      setError(e.message ?? String(e));
      setSkills([]);
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <View style={s.root}>
      <View style={s.header}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
          <View style={s.logo} />
          <Text style={s.brand}>Athena</Text>
        </View>
        <GhostButton label="Settings" onPress={onSettings} color={colors.muted} style={{ height: 40 }} />
      </View>
      <Text style={s.h1}>What needs fixing?</Text>
      <Pressable onPress={onCreate} style={({ pressed }) => [s.teach, pressed && { opacity: 0.85 }]} accessibilityRole="button">
        <View style={{ flex: 1 }}>
          <Text style={s.teachTitle}>Teach a skill</Text>
          <Text style={s.teachText}>Photograph the job, say what you did. Muse Spark writes the steps and the checks.</Text>
        </View>
        <Text style={s.teachArrow}>+</Text>
      </Pressable>

      {error ? (
        <View style={s.errorBox}>
          <Text style={s.errorTitle}>Can't reach the backend</Text>
          <Text style={s.errorText}>{error}</Text>
          <GhostButton label="Open Settings" onPress={onSettings} style={{ marginTop: 10 }} />
        </View>
      ) : null}

      {skills === null ? (
        <ActivityIndicator color={colors.amber} style={{ marginTop: 40 }} />
      ) : (
        <FlatList
          data={skills}
          keyExtractor={(k) => k.id}
          contentContainerStyle={{ gap: 12, paddingBottom: 40 }}
          refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={colors.amber} />}
          ListEmptyComponent={!error ? <Text style={s.empty}>No skills published yet.</Text> : undefined}
          renderItem={({ item }) => (
            <Pressable onPress={() => onOpen(item)} style={({ pressed }) => [s.card, pressed && { opacity: 0.85 }]}>
              <View style={s.thumb}>
                {thumbFor(api, item) ? <Image source={{ uri: thumbFor(api, item)! }} style={{ width: "100%", height: "100%" }} resizeMode="cover" /> : null}
                <Chip color={colors.pass} style={{ position: "absolute", top: 10, left: 10 }}>
                  {item.steps.filter((st) => st.safety_gate).length} safety gate · camera-checked
                </Chip>
                {item.compiled ? (
                  <Chip color={colors.amber} style={{ position: "absolute", top: 10, right: 10, borderColor: colors.amber }}>
                    yours
                  </Chip>
                ) : null}
              </View>
              <View style={{ padding: 14, gap: 6 }}>
                <Text style={s.title}>{item.title}</Text>
                <Text style={s.meta}>
                  {item.author} · {item.domain}
                </Text>
                <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
                  <Text style={s.meta}>
                    {item.steps.length} steps · {item.est_minutes} min · Difficulty {item.difficulty}/5
                  </Text>
                  <Text style={s.price}>{item.price_usd ? `$${item.price_usd}` : "Free"}</Text>
                </View>
              </View>
            </Pressable>
          )}
        />
      )}
    </View>
  );
}

function thumbFor(api: Api, skill: Skill): string | null {
  const withRef = skill.steps.find((st) => st.reference_image);
  return withRef ? api.mediaUrl(skill.id, withRef.reference_image) : null;
}

const s = StyleSheet.create({
  teach: { flexDirection: "row", alignItems: "center", gap: 12, padding: 14, borderRadius: 16, backgroundColor: colors.amberDark, borderWidth: 1, borderColor: colors.amber, marginBottom: 14 },
  teachTitle: { color: colors.amber, fontSize: 15, fontWeight: "700" },
  teachText: { color: colors.text2, fontSize: 12, lineHeight: 16, marginTop: 2 },
  teachArrow: { color: colors.amber, fontSize: 28, fontWeight: "300" },
  root: { flex: 1, backgroundColor: colors.bg, paddingHorizontal: 20, paddingTop: 56 },
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 14 },
  logo: { width: 22, height: 22, borderRadius: 4, backgroundColor: colors.amber, transform: [{ rotate: "45deg" }], marginLeft: 3 },
  brand: { color: colors.text, fontSize: 20, fontWeight: "700" },
  h1: { color: colors.text, fontSize: 30, fontWeight: "700", letterSpacing: -0.6, marginBottom: 16 },
  card: { borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border, overflow: "hidden" },
  thumb: { height: 120, backgroundColor: colors.surface2 },
  title: { color: colors.text, fontSize: 17, fontWeight: "600", lineHeight: 22 },
  meta: { color: colors.muted, fontSize: 13 },
  price: { color: colors.text, fontSize: 15, fontWeight: "700" },
  empty: { color: colors.muted, textAlign: "center", marginTop: 40 },
  errorBox: { borderRadius: radius.md, borderWidth: 1, borderColor: colors.fail, padding: 14, marginBottom: 14, backgroundColor: colors.surface },
  errorTitle: { color: colors.fail, fontWeight: "700", fontSize: 15, marginBottom: 4 },
  errorText: { color: colors.text2, fontSize: 13 },
});
