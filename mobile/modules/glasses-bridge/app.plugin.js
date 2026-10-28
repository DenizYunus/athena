// Expo config plugin for the glasses bridge. Adds what the Device Access Toolkit expects in the
// app manifest: the APPLICATION_ID / CLIENT_TOKEN meta-data (both "0" in Developer Mode; real values
// come from the Wearables Developer Center for a published integration) and the URI scheme the Meta AI
// app uses to return to this app after registration and permission prompts.
//
// The two values go through string resources: a literal android:value="0" compiles to an integer,
// and the toolkit reads the meta-data as a string ("Failed to read metadata from manifest").
const { withAndroidManifest, withStringsXml, AndroidConfig } = require("expo/config-plugins");

const META_APP_ID = "com.meta.wearable.mwdat.APPLICATION_ID";
const META_TOKEN = "com.meta.wearable.mwdat.CLIENT_TOKEN";
const RES_APP_ID = "mwdat_application_id";
const RES_TOKEN = "mwdat_client_token";

function withGlassesBridge(config, props = {}) {
  const applicationId = String(props.applicationId ?? "0");
  const clientToken = String(props.clientToken ?? "0");
  const scheme = String(props.scheme ?? config.scheme ?? "athena");

  config = withStringsXml(config, (c) => {
    c.modResults = AndroidConfig.Strings.setStringItem(
      [
        { $: { name: RES_APP_ID, translatable: "false" }, _: applicationId },
        { $: { name: RES_TOKEN, translatable: "false" }, _: clientToken },
      ],
      c.modResults,
    );
    return c;
  });

  return withAndroidManifest(config, (c) => {
    const manifest = c.modResults;
    const app = AndroidConfig.Manifest.getMainApplicationOrThrow(manifest);
    AndroidConfig.Manifest.addMetaDataItemToMainApplication(app, META_APP_ID, `@string/${RES_APP_ID}`);
    AndroidConfig.Manifest.addMetaDataItemToMainApplication(app, META_TOKEN, `@string/${RES_TOKEN}`);

    const activity = AndroidConfig.Manifest.getMainActivityOrThrow(manifest);
    activity["intent-filter"] = activity["intent-filter"] ?? [];
    const present = activity["intent-filter"].some((filter) =>
      (filter.data ?? []).some((d) => d.$ && d.$["android:scheme"] === scheme),
    );
    if (!present) {
      activity["intent-filter"].push({
        action: [{ $: { "android:name": "android.intent.action.VIEW" } }],
        category: [
          { $: { "android:name": "android.intent.category.DEFAULT" } },
          { $: { "android:name": "android.intent.category.BROWSABLE" } },
        ],
        data: [{ $: { "android:scheme": scheme } }],
      });
    }
    return c;
  });
}

module.exports = withGlassesBridge;
