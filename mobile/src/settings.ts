import * as SecureStore from "expo-secure-store";

const KEY_URL = "athena.backendUrl";
const KEY_API = "athena.modelApiKey";

export type Settings = { backendUrl: string; apiKey: string };

export async function loadSettings(): Promise<Settings> {
  const [backendUrl, apiKey] = await Promise.all([
    SecureStore.getItemAsync(KEY_URL),
    SecureStore.getItemAsync(KEY_API),
  ]);
  return { backendUrl: backendUrl ?? "", apiKey: apiKey ?? "" };
}

export async function saveSettings(s: Settings): Promise<void> {
  await Promise.all([
    SecureStore.setItemAsync(KEY_URL, s.backendUrl.trim().replace(/\/+$/, "")),
    SecureStore.setItemAsync(KEY_API, s.apiKey.trim()),
  ]);
}
