// The YouTube links the backend takes (backend/app/digest.py youtube_link): one video, by watch link,
// youtu.be, Shorts, live or embed. It mirrors the backend's parser step by step, so the Digest button
// is enabled for exactly the links the backend accepts; backend/tests/test_links.py runs both over the
// same table (backend/tests/fixtures/youtube_links.json).

const HOSTS = ["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"];
const SHORT_HOSTS = ["youtu.be", "www.youtu.be"];
const ID_PATHS = ["shorts", "live", "embed", "v"];
const NOT_VIDEO_IDS = ["videoseries", "live_stream"];
const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;

/** The 11-character video id of a link to one YouTube video, or null for anything else. */
export function youtubeVideoId(text: string): string | null {
  let t = text.trim();
  if (!t || /\s/.test(t)) return null;
  if (!t.includes("://")) t = `https://${t}`;
  // Like Python's urlsplit: scheme, then the netloc up to the first / ? or #, then path, query, fragment.
  const m = /^([A-Za-z][A-Za-z0-9+.-]*):\/\/([^/?#]*)([^?#]*)(?:\?([^#]*))?/.exec(t);
  if (!m) return null;
  const scheme = m[1].toLowerCase();
  if (scheme !== "http" && scheme !== "https") return null;
  const host = hostname(m[2]);
  if (host === null) return null;
  const path = m[3].split("/").filter(Boolean);
  let candidate = "";
  if (SHORT_HOSTS.includes(host)) {
    candidate = path.length === 1 ? path[0] : "";
  } else if (HOSTS.includes(host)) {
    if (path.length === 1 && path[0] === "watch") candidate = firstValue(m[4] ?? "", "v");
    else if (path.length === 2 && ID_PATHS.includes(path[0])) candidate = path[1];
  } else {
    return null;
  }
  return VIDEO_ID.test(candidate) && !NOT_VIDEO_IDS.includes(candidate) ? candidate : null;
}

/** urlsplit(...).hostname: the netloc without user info and port, lowercased; null when malformed. */
function hostname(netloc: string): string | null {
  if (netloc.includes("[") !== netloc.includes("]")) return null; // Python raises "Invalid IPv6 URL"
  const at = netloc.lastIndexOf("@");
  let host = at >= 0 ? netloc.slice(at + 1) : netloc;
  if (host.includes("[")) return host.slice(host.indexOf("[") + 1).split("]")[0].toLowerCase();
  const colon = host.indexOf(":");
  if (colon >= 0) host = host.slice(0, colon);
  return host.toLowerCase();
}

/** parse_qs(query)[name][0]: pairs split on &, + and %XX decoded, pairs without a value skipped. */
function firstValue(query: string, name: string): string {
  for (const pair of query.split("&")) {
    const eq = pair.indexOf("=");
    if (eq < 0) continue;
    const key = decode(pair.slice(0, eq));
    const value = decode(pair.slice(eq + 1));
    if (key === name && value) return value;
  }
  return "";
}

function decode(part: string): string {
  const plain = part.replace(/\+/g, " ");
  try {
    return decodeURIComponent(plain);
  } catch {
    return plain; // Python leaves a malformed escape as it is
  }
}
