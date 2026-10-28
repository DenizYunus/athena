import * as SecureStore from "expo-secure-store";

const KEY_URL = "athena.backendUrl";
const KEY_API = "athena.modelApiKey";
const KEY_GLASSES_MOCK = "athena.glassesMock";

/** glassesMock: the Glasses toggle in a session uses the Mock Device Kit (no hardware) instead of real Ray-Ban Meta glasses. */
export type Settings = { backendUrl: string; apiKey: string; glassesMock: boolean };

export async function loadSettings(): Promise<Settings> {
  const [backendUrl, apiKey, glassesMock] = await Promise.all([
    SecureStore.getItemAsync(KEY_URL),
    SecureStore.getItemAsync(KEY_API),
    SecureStore.getItemAsync(KEY_GLASSES_MOCK),
  ]);
  return { backendUrl: backendUrl ?? "", apiKey: apiKey ?? "", glassesMock: glassesMock === null ? true : glassesMock === "1" };
}

export async function saveSettings(s: Settings): Promise<void> {
  await Promise.all([
    SecureStore.setItemAsync(KEY_URL, s.backendUrl.trim().replace(/\/+$/, "")),
    SecureStore.setItemAsync(KEY_API, s.apiKey.trim()),
    SecureStore.setItemAsync(KEY_GLASSES_MOCK, s.glassesMock ? "1" : "0"),
  ]);
}
