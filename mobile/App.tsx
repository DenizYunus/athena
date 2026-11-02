import { StatusBar } from "expo-status-bar";
import React, { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { Api, Session, Skill } from "./src/api";
import { AuthorCaptureScreen } from "./src/screens/AuthorCapture";
import { AuthorImportScreen } from "./src/screens/AuthorImport";
import { AuthorReviewScreen } from "./src/screens/AuthorReview";
import { MarketplaceScreen } from "./src/screens/Marketplace";
import { SessionScreen } from "./src/screens/Session";
import { SettingsScreen } from "./src/screens/Settings";
import { SkillDetailScreen } from "./src/screens/SkillDetail";
import { loadSettings, Settings } from "./src/settings";
import { colors } from "./src/theme";

type Route =
  | { name: "market" }
  | { name: "settings" }
  | { name: "skill"; skill: Skill }
  | { name: "session"; skill: Skill; session: Session }
  // Teach a skill: "author" uploads a video (and the manual); "authorPhotos" is the older
  // photograph-each-step path, reached from the upload screen.
  | { name: "author" }
  | { name: "authorPhotos" }
  | { name: "review"; skill: Skill };

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

  const startSession = async (skill: Skill) => {
    const session = await api.startSession(skill.id);
    setRoute({ name: "session", skill, session });
  };

  let screen: React.ReactNode;
  switch (route.name) {
    case "settings":
      screen = <SettingsScreen settings={settings} onSaved={(s) => setSettings(s)} onBack={() => setRoute({ name: "market" })} />;
      break;
    case "skill":
      screen = <SkillDetailScreen api={api} skill={route.skill} onBack={() => setRoute({ name: "market" })} onStart={() => startSession(route.skill)} />;
      break;
    case "session":
      screen = <SessionScreen api={api} skill={route.skill} session={route.session} onExit={() => setRoute({ name: "market" })} glassesMock={settings.glassesMock} />;
      break;
    case "author":
      screen = (
        <AuthorImportScreen
          api={api}
          onCompiled={(skill) => setRoute({ name: "review", skill })}
          onBack={() => setRoute({ name: "market" })}
          onPhotos={() => setRoute({ name: "authorPhotos" })}
        />
      );
      break;
    case "authorPhotos":
      screen = <AuthorCaptureScreen api={api} onCompiled={(skill) => setRoute({ name: "review", skill })} onBack={() => setRoute({ name: "author" })} />;
      break;
    case "review":
      screen = (
        <AuthorReviewScreen
          api={api}
          skill={route.skill}
          onTry={() => startSession(route.skill)}
          onDone={() => setRoute({ name: "market" })}
          onDiscarded={() => setRoute({ name: "author" })}
        />
      );
      break;
    default:
      screen = (
        <MarketplaceScreen
          api={api}
          onOpen={(skill) => setRoute({ name: "skill", skill })}
          onSettings={() => setRoute({ name: "settings" })}
          onCreate={() => setRoute({ name: "author" })}
        />
      );
  }

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      {screen}
      <StatusBar style="light" />
    </View>
  );
}
