import React, { useState } from "react";
import { ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { Api } from "../api";
import { saveSettings, Settings } from "../settings";
import { colors, radius } from "../theme";
import { GhostButton, PrimaryButton } from "../ui";

export function SettingsScreen({ settings, onSaved, onBack }: { settings: Settings; onSaved: (s: Settings) => void; onBack: () => void }) {
  const [backendUrl, setBackendUrl] = useState(settings.backendUrl);
  const [apiKey, setApiKey] = useState(settings.apiKey);
  const [status, setStatus] = useState<string>("");

  async function save() {
    const next = { backendUrl: backendUrl.trim().replace(/\/+$/, ""), apiKey: apiKey.trim() };
    await saveSettings(next);
    onSaved(next);
    setStatus("Saved.");
  }

  async function test() {
    setStatus("Testing…");
    try {
      const next = { backendUrl: backendUrl.trim().replace(/\/+$/, ""), apiKey: apiKey.trim() };
      const h = await new Api(next).health();
      setStatus(
        `Backend OK · ${h.skills} skill(s) · ` +
          (h.server_has_key ? "server has a key" : next.apiKey ? "using the key from this phone" : "no key anywhere yet"),
      );
    } catch (e: any) {
      setStatus(`Failed: ${e.message ?? e}`);
    }
  }

  return (
    <ScrollView style={s.root} contentContainerStyle={s.content} keyboardShouldPersistTaps="handled">
      <Text style={s.h1}>Settings</Text>
      <Text style={s.help}>
        The backend runs on your Mac. Start it with{" "}
        <Text style={s.code}>uvicorn app.main:app --host 0.0.0.0 --port 8000</Text> and enter the Mac's Wi-Fi address below.
      </Text>

      <Text style={s.label}>Backend URL</Text>
      <TextInput
        style={s.input}
        value={backendUrl}
        onChangeText={setBackendUrl}
        placeholder="http://192.168.1.20:8000"
        placeholderTextColor={colors.dim}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="url"
      />

      <Text style={s.label}>Meta Model API key</Text>
      <TextInput
        style={s.input}
        value={apiKey}
        onChangeText={setApiKey}
        placeholder="LLM|…|…  (optional if the backend has one in .env)"
        placeholderTextColor={colors.dim}
        autoCapitalize="none"
        autoCorrect={false}
        secureTextEntry
      />
      <Text style={s.help}>Stored in the phone's secure keystore and sent to your backend only, as the X-Model-API-Key header.</Text>

      <View style={{ height: 8 }} />
      <PrimaryButton label="Save" onPress={save} />
      <View style={s.row}>
        <GhostButton label="Test connection" onPress={test} style={{ flex: 1 }} />
        <GhostButton label="Back" onPress={onBack} color={colors.muted} style={{ flex: 1 }} />
      </View>
      {status ? <Text style={s.status}>{status}</Text> : null}
    </ScrollView>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 20, paddingTop: 64, gap: 10 },
  h1: { color: colors.text, fontSize: 28, fontWeight: "700", letterSpacing: -0.5, marginBottom: 6 },
  label: { color: colors.muted, fontSize: 12, fontWeight: "600", textTransform: "uppercase", letterSpacing: 0.6, marginTop: 10 },
  input: {
    height: 52,
    borderRadius: radius.md,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    color: colors.text,
    paddingHorizontal: 14,
    fontSize: 16,
  },
  help: { color: colors.muted, fontSize: 13, lineHeight: 18 },
  code: { color: colors.text2, fontFamily: "monospace" },
  row: { flexDirection: "row", gap: 10, marginTop: 10 },
  status: { color: colors.text2, fontSize: 14, marginTop: 12, lineHeight: 20 },
});
