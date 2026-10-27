import { StatusBar } from "expo-status-bar";
import React, { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { Api, Session, Skill } from "./src/api";
import { MarketplaceScreen } from "./src/screens/Marketplace";
import { SessionScreen } from "./src/screens/Session";
import { SettingsScreen } from "./src/screens/Settings";
import { SkillDetailScreen } from "./src/screens/SkillDetail";
import { loadSettings, Settings } from "./src/settings";
import { colors } from "./src/theme";

type Route = { name: "market" } | { name: "settings" } | { name: "skill"; skill: Skill } | { name: "session"; skill: Skill; session: Session };

export default function App() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [route, setRoute] = useState<Route>({ name: "market" });

  useEffect(() => {
    loadSettings().then((s) => {
      setSettings(s);
      if (!s.backendUrl) setRoute({ name: "settings" });
    });
  }, []);

  const api = useMemo(() => (settings ? new Api(settings) : null), [settings]);

  if (!settings || !api) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center" }}>
        <ActivityIndicator color={colors.amber} />
      </View>
    );
  }

  let screen: React.ReactNode;
  switch (route.name) {
    case "settings":
      screen = <SettingsScreen settings={settings} onSaved={(s) => setSettings(s)} onBack={() => setRoute({ name: "market" })} />;
      break;
    case "skill":
      screen = (
        <SkillDetailScreen
          skill={route.skill}
          onBack={() => setRoute({ name: "market" })}
          onStart={async () => {
            const session = await api.startSession(route.skill.id);
            setRoute({ name: "session", skill: route.skill, session });
          }}
        />
      );
      break;
    case "session":
      screen = <SessionScreen api={api} skill={route.skill} session={route.session} onExit={() => setRoute({ name: "market" })} />;
      break;
    default:
      screen = <MarketplaceScreen api={api} onOpen={(skill) => setRoute({ name: "skill", skill })} onSettings={() => setRoute({ name: "settings" })} />;
  }

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      {screen}
      <StatusBar style="light" />
    </View>
  );
}
