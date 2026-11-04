// Small display helpers shared by the authoring and skill screens.

/** Seconds as a clock: 101 -> "1:41", 3725 -> "1:02:05". */
export function clock(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

/** Who made the video a skill was learned from, when the author gave a YouTube link:
 *  "“Brake pads in 5 minutes” by Dana's Garage on YouTube". Null for an uploaded video. */
export function videoCredit(digest: { video_url?: string | null; video_title?: string | null; video_channel?: string | null }): string | null {
  if (!digest.video_url) return null;
  const title = digest.video_title ? `“${digest.video_title}”` : "A video";
  return `${title}${digest.video_channel ? ` by ${digest.video_channel}` : ""} on YouTube`;
}

/** Bytes as a file size: "20 kB" under 1 MB, then megabytes with one decimal under 10 MB: "3.4 MB", "212 MB". */
export function megabytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} kB`;
  const mb = bytes / (1024 * 1024);
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}
