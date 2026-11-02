// Thin client for the Athena backend (backend/app/main.py).
import type { Settings } from "./settings";

export type Step = {
  n: number;
  instruction: string;
  expected_state: string;
  reference_image: string | null;
  sam_concepts: string[];
  safety_gate: boolean;
  gate_requirement: string | null;
  watch_me: boolean;
  est_minutes: number;
  common_mistakes: string[];
  // Set only on skills digested from a video (POST /skills/digest); seed skills and photo-compiled
  // skills leave them out or null.
  /** Where the step came from: the video, the expert's document, both, or a hazard-removal gate
   *  neither covers that Athena added. */
  source?: StepSource | null;
  /** Seconds into the expert's video where the step starts and ends. */
  video_start_s?: number | null;
  video_end_s?: number | null;
  /** The expert doing this step, e.g. compiled/media/<id>/step_3.mp4 (load it with mediaUrl). */
  clip?: string | null;
  /** Specs and cautions from the expert's document for this step; empty when there are none. */
  document_note?: string;
};

export type StepSource = "video" | "document" | "both" | "added";

/** A point where the expert's video and written procedure disagree, and what the skill used. */
export type Conflict = { topic: string; video_says: string; document_says: string; resolution: string };

export type DigestInfo = { video_seconds: number; chunks: number; documents: string[] };

export type Skill = {
  id: string;
  title: string;
  domain: string;
  author: string;
  difficulty: number;
  est_minutes: number;
  price_usd: number;
  tools: string[];
  parts: string[];
  vocabulary: string[];
  steps: Step[];
  compiled: boolean;
  conflicts?: Conflict[];
  /** Set when the skill was digested from a video. */
  digest?: DigestInfo | null;
};

export type DigestStage = "queued" | "preparing" | "uploading" | "reading" | "merging" | "frames" | "saving" | "done" | "failed";

/** A video digest running on the backend (GET /skills/digest/{id}). `message` is one sentence for
 *  the progress line; `error` is a sentence too, never a status code. */
export type DigestJob = {
  id: string;
  status: "running" | "done" | "failed";
  stage: DigestStage;
  message: string;
  progress: number;
  chunks_total: number;
  chunks_done: number;
  video_seconds: number;
  documents: string[];
  skill: Skill | null;
  error: string | null;
  created_at: string;
};

/** A file the document picker returned. */
export type PickedFile = { uri: string; name: string; mimeType?: string; size?: number };

export type Session = {
  id: string;
  skill_id: string;
  mode: "ptt" | "snapshot" | "watch";
  current_step: number;
  completed: boolean;
};

export type Verdict = {
  status: "pass" | "fail" | "unclear";
  confidence: number;
  reason: string;
  what_to_fix: string;
  photo_request: { needed: boolean; what: string; angle: string };
  safety_concern: boolean;
};

export type Highlight = {
  concept: string;
  frame_width: number;
  frame_height: number;
  objects: {
    id: number;
    box: { x1: number; y1: number; x2: number; y2: number };
    confidence: number;
    mask: { left: number; top: number; width: number; height: number; path: string } | null;
  }[];
};

export type VerifyResponse = {
  verdict: Verdict;
  advanced: boolean;
  current_step: number;
  completed: boolean;
  highlight: Highlight | null;
};

export type TurnResponse = { reply: string; transcript: string; current_step: number };

export type Capture = { photoUri: string | null; note: string };

/** The multipart name and type for a clip: the phone records an mp4; a glasses turn records
 *  the question as audio only (m4a), which the backend transcribes without treating it as video. */
function clipPart(uri: string): { name: string; type: string } {
  const lower = uri.toLowerCase().split("?")[0];
  if (lower.endsWith(".m4a") || lower.endsWith(".aac")) return { name: "question.m4a", type: "audio/mp4" };
  if (lower.endsWith(".wav")) return { name: "question.wav", type: "audio/wav" };
  if (lower.endsWith(".3gp")) return { name: "question.3gp", type: "audio/3gpp" };
  return { name: "clip.mp4", type: "video/mp4" };
}

const VIDEO_TYPES: Record<string, string> = {
  mp4: "video/mp4",
  m4v: "video/x-m4v",
  mov: "video/quicktime",
  webm: "video/webm",
  mkv: "video/x-matroska",
  "3gp": "video/3gpp",
  avi: "video/x-msvideo",
};

/** The multipart type for the author's video. The backend accepts any video/* type or a known
 *  video extension; a picker that reports no type (or a generic one) still gets a video type here,
 *  so a file named without an extension is not turned away. */
function videoPartType(file: PickedFile): string {
  if (file.mimeType?.startsWith("video/")) return file.mimeType;
  const ext = file.name.toLowerCase().split(".").pop() ?? "";
  return VIDEO_TYPES[ext] ?? "video/mp4";
}

export class ApiError extends Error {
  constructor(message: string, public status?: number) {
    super(message);
  }
}

export class Api {
  constructor(private settings: Settings) {}

  get apiKey(): string {
    return this.settings.apiKey;
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    const h: Record<string, string> = { ...extra };
    if (this.settings.apiKey) h["X-Model-API-Key"] = this.settings.apiKey;
    return h;
  }

  private url(path: string): string {
    if (!this.settings.backendUrl) throw new ApiError("Backend URL is not set. Open Settings.");
    return `${this.settings.backendUrl}${path}`;
  }

  /** ws://host/asr for the live transcript relay. */
  asrUrl(): string {
    return this.url("/asr").replace(/^http/, "ws");
  }

  /** Where a step's reference photo or expert clip can be loaded from, or null. */
  mediaUrl(skillId: string, reference: string | null): string | null {
    if (!reference) return null;
    if (/^https?:\/\//.test(reference)) return reference;
    const name = reference.split("/").pop();
    return name ? this.url(`/skills/${skillId}/media/${name}`) : null;
  }

  /** POST a multipart form and parse the JSON reply. React Native's XMLHttpRequest accepts
   *  `{ uri, name, type }` file parts; the `fetch` that Expo SDK 58 installs globally does not
   *  (its FormData conversion only takes strings and Blobs), so every upload goes through here.
   *  `onUploadProgress` gets bytes sent so far and the body's length (0 when the runtime does not
   *  know it); `signal` aborts the upload. */
  private multipart<T>(
    path: string,
    form: FormData,
    timeoutMs = 120000,
    opts: { onUploadProgress?: (sent: number, total: number) => void; signal?: AbortSignal } = {},
  ): Promise<T> {
    const url = this.url(path);
    const headers = this.headers();
    return new Promise<T>((resolve, reject) => {
      if (opts.signal?.aborted) {
        reject(new ApiError("The upload was cancelled"));
        return;
      }
      const xhr = new XMLHttpRequest();
      xhr.open("POST", url);
      for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
      xhr.timeout = timeoutMs;
      const onAbort = () => xhr.abort();
      opts.signal?.addEventListener("abort", onAbort);
      const done = () => opts.signal?.removeEventListener("abort", onAbort);
      if (opts.onUploadProgress) {
        const report = opts.onUploadProgress;
        xhr.upload.onprogress = (e) => report(e.loaded, e.lengthComputable && e.total > 0 ? e.total : 0);
      }
      xhr.onabort = () => {
        done();
        reject(new ApiError("The upload was cancelled"));
      };
      xhr.onerror = () => {
        done();
        reject(new ApiError("Network request failed"));
      };
      xhr.ontimeout = () => {
        done();
        reject(new ApiError("The backend took too long to answer"));
      };
      xhr.onload = () => {
        done();
        const text = xhr.responseText || "";
        if (xhr.status < 200 || xhr.status >= 300) {
          let detail = xhr.statusText || `HTTP ${xhr.status}`;
          try {
            detail = JSON.parse(text).detail ?? detail;
          } catch {}
          reject(new ApiError(`${xhr.status}: ${detail}`, xhr.status));
          return;
        }
        if (xhr.status === 204 || !text) {
          resolve(undefined as T);
          return;
        }
        try {
          resolve(JSON.parse(text) as T);
        } catch {
          reject(new ApiError("The backend sent a reply that is not JSON"));
        }
      };
      xhr.send(form as any);
    });
  }

  private async handle<T>(res: Response): Promise<T> {
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail ?? JSON.stringify(body);
      } catch {}
      throw new ApiError(`${res.status}: ${detail}`, res.status);
    }
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  async health(): Promise<{ ok: boolean; skills: number; server_has_key: boolean; ffmpeg: boolean }> {
    const res = await fetch(this.url("/health"), { headers: this.headers() });
    return this.handle(res);
  }

  async listSkills(): Promise<Skill[]> {
    const res = await fetch(this.url("/skills"), { headers: this.headers() });
    return this.handle(res);
  }

  async getSkill(id: string): Promise<Skill> {
    const res = await fetch(this.url(`/skills/${id}`), { headers: this.headers() });
    return this.handle(res);
  }

  async deleteSkill(id: string): Promise<void> {
    const res = await fetch(this.url(`/skills/${id}`), { method: "DELETE", headers: this.headers() });
    return this.handle(res);
  }

  async startSession(skillId: string): Promise<Session> {
    const res = await fetch(this.url("/sessions"), {
      method: "POST",
      headers: this.headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ skill_id: skillId, mode: "ptt" }),
    });
    return this.handle(res);
  }

  /** Push-to-talk turn: the clip carries the user's voice; the transcript is what the live
   *  transcription heard (the backend transcribes the clip itself when it is empty). */
  async turn(sessionId: string, opts: { transcript?: string; clipUri?: string; photoUri?: string }): Promise<TurnResponse> {
    const form = new FormData();
    form.append("transcript", opts.transcript ?? "");
    if (opts.clipUri) form.append("clip", { uri: opts.clipUri, ...clipPart(opts.clipUri) } as any);
    if (opts.photoUri) form.append("photo", { uri: opts.photoUri, name: "photo.jpg", type: "image/jpeg" } as any);
    return this.multipart(`/sessions/${sessionId}/turn`, form);
  }

  /** Streamed push-to-talk turn. The backend transcribes the clip's audio first, then asks the guide.
   *  Events arrive in order: transcribing, transcript, delta..., done (or error). Uses XMLHttpRequest
   *  because React Native's fetch cannot read a response body incrementally; if the runtime delivers
   *  no incremental progress events, everything is parsed when the response completes. */
  turnStream(
    sessionId: string,
    opts: { transcript?: string; clipUri?: string; photoUri?: string },
    on: { transcribing?: () => void; transcript?: (text: string) => void; delta?: (delta: string, reply: string) => void } = {},
  ): Promise<TurnResponse> {
    const form = new FormData();
    form.append("transcript", opts.transcript ?? "");
    if (opts.clipUri) form.append("clip", { uri: opts.clipUri, ...clipPart(opts.clipUri) } as any);
    if (opts.photoUri) form.append("photo", { uri: opts.photoUri, name: "photo.jpg", type: "image/jpeg" } as any);
    const url = this.url(`/sessions/${sessionId}/turn?stream=true`);
    const headers = this.headers({ Accept: "text/event-stream" });

    return new Promise<TurnResponse>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      let seen = 0;
      let transcript = "";
      let reply = "";
      let step = 0;
      let done = false;
      let failed: string | null = null;

      const consume = () => {
        let text: string;
        try {
          text = xhr.responseText || "";
        } catch {
          return;
        }
        let idx: number;
        while ((idx = text.indexOf("\n\n", seen)) !== -1) {
          const block = text.slice(seen, idx);
          seen = idx + 2;
          for (const line of block.split("\n")) {
            if (!line.startsWith("data:")) continue;
            let payload: any;
            try {
              payload = JSON.parse(line.slice(5).trim());
            } catch {
              continue;
            }
            if (payload.transcribing) on.transcribing?.();
            if (typeof payload.transcript === "string") {
              transcript = payload.transcript;
              on.transcript?.(transcript);
            }
            if (typeof payload.delta === "string") {
              reply += payload.delta;
              on.delta?.(payload.delta, reply);
            }
            if (payload.error) failed = String(payload.error);
            if (payload.done) {
              done = true;
              step = payload.current_step ?? 0;
            }
          }
        }
      };

      xhr.open("POST", url);
      for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
      xhr.onprogress = consume;
      xhr.onerror = () => reject(new ApiError("Network request failed"));
      xhr.ontimeout = () => reject(new ApiError("The guide took too long to answer"));
      xhr.timeout = 120000;
      xhr.onreadystatechange = () => {
        if (xhr.readyState !== 4) return;
        if (xhr.status !== 200) {
          let detail = xhr.statusText || `HTTP ${xhr.status}`;
          try {
            detail = JSON.parse(xhr.responseText).detail ?? detail;
          } catch {}
          reject(new ApiError(`${xhr.status}: ${detail}`, xhr.status));
          return;
        }
        consume();
        if (failed) reject(new ApiError(failed));
        else if (!done) reject(new ApiError(reply ? "The reply was cut off" : "The guide did not answer"));
        else resolve({ reply: reply.trim(), transcript, current_step: step });
      };
      xhr.send(form as any);
    });
  }

  /** `step` is the step on screen; the backend answers 409 if the session has already left it. */
  async verify(sessionId: string, photoUri: string, step?: number): Promise<VerifyResponse> {
    const form = new FormData();
    form.append("photo", { uri: photoUri, name: "photo.jpg", type: "image/jpeg" } as any);
    if (step !== undefined) form.append("step", String(step));
    return this.multipart(`/sessions/${sessionId}/verify`, form);
  }

  async getSession(sessionId: string): Promise<Session> {
    const res = await fetch(this.url(`/sessions/${sessionId}`), { headers: this.headers() });
    return this.handle(res);
  }

  async highlight(sessionId: string, photoUri: string, concept = ""): Promise<Highlight | null> {
    const form = new FormData();
    form.append("photo", { uri: photoUri, name: "photo.jpg", type: "image/jpeg" } as any);
    form.append("concept", concept);
    return this.multipart(`/sessions/${sessionId}/highlight`, form);
  }

  async skip(sessionId: string): Promise<Session> {
    const res = await fetch(this.url(`/sessions/${sessionId}/skip`), { method: "POST", headers: this.headers() });
    return this.handle(res);
  }

  /** Transcribe a dictated note (wav or mp4) with the batch endpoint. */
  async authorTranscribe(uri: string, keywords: string[] = []): Promise<string> {
    const form = new FormData();
    const isWav = uri.toLowerCase().endsWith(".wav");
    form.append("clip", { uri, name: isWav ? "note.wav" : "note.mp4", type: isWav ? "audio/wav" : "video/mp4" } as any);
    form.append("keywords", keywords.join(", "));
    const body = await this.multipart<{ transcript: string }>("/author/transcribe", form);
    return body.transcript;
  }

  /** Photos in order with one note each -> a published skill. */
  async compile(captures: Capture[], author: string, titleHint: string): Promise<Skill> {
    const form = new FormData();
    form.append("author", author);
    form.append("title_hint", titleHint);
    // Keep notes and photos index-aligned: a capture without a photo still gets its note slot.
    const withPhoto = captures.filter((c) => c.photoUri);
    const withoutPhoto = captures.filter((c) => !c.photoUri);
    withPhoto.forEach((c, i) => {
      form.append("photos", { uri: c.photoUri, name: `photo_${i + 1}.jpg`, type: "image/jpeg" } as any);
      form.append("notes", c.note);
    });
    withoutPhoto.forEach((c) => form.append("notes", c.note));
    return this.multipart("/skills/compile", form, 300000);
  }

  /** Upload the expert's video (and up to three PDFs of the written procedure) and start a digest.
   *  The backend checks the files, answers 202 with the job, and keeps working after this returns;
   *  poll getDigest for progress. `onUploadProgress` gets the fraction of the upload sent (0..1).
   *  The timeout is long because a video of up to 1 GB may go over a slow Wi-Fi link. */
  startDigest(
    video: PickedFile,
    documents: PickedFile[],
    author: string,
    titleHint: string,
    onUploadProgress?: (fraction: number) => void,
    signal?: AbortSignal,
  ): Promise<DigestJob> {
    const form = new FormData();
    form.append("author", author);
    form.append("title_hint", titleHint);
    form.append("video", { uri: video.uri, name: video.name || "video.mp4", type: videoPartType(video) } as any);
    for (const d of documents) form.append("documents", { uri: d.uri, name: d.name || "procedure.pdf", type: "application/pdf" } as any);
    // When the runtime cannot say how long the body is, the picked files' sizes stand in for it
    // (the form fields and multipart framing are a few hundred bytes).
    const known = (video.size ?? 0) + documents.reduce((sum, d) => sum + (d.size ?? 0), 0);
    const report = onUploadProgress
      ? (sent: number, total: number) => {
          const whole = total > 0 ? total : known;
          if (whole > 0) onUploadProgress(Math.min(1, Math.max(0, sent / whole)));
        }
      : undefined;
    return this.multipart<DigestJob>("/skills/digest", form, 30 * 60 * 1000, { onUploadProgress: report, signal });
  }

  async getDigest(jobId: string): Promise<DigestJob> {
    const res = await fetch(this.url(`/skills/digest/${encodeURIComponent(jobId)}`), { headers: this.headers() });
    return this.handle(res);
  }
}
