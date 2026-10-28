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
};

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
};

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

  /** Where a step's reference photo can be loaded from, or null. */
  mediaUrl(skillId: string, reference: string | null): string | null {
    if (!reference) return null;
    if (/^https?:\/\//.test(reference)) return reference;
    const name = reference.split("/").pop();
    return name ? this.url(`/skills/${skillId}/media/${name}`) : null;
  }

  /** POST a multipart form and parse the JSON reply. React Native's XMLHttpRequest accepts
   *  `{ uri, name, type }` file parts; the `fetch` that Expo SDK 58 installs globally does not
   *  (its FormData conversion only takes strings and Blobs), so every upload goes through here. */
  private multipart<T>(path: string, form: FormData, timeoutMs = 120000): Promise<T> {
    const url = this.url(path);
    const headers = this.headers();
    return new Promise<T>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", url);
      for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
      xhr.timeout = timeoutMs;
      xhr.onerror = () => reject(new ApiError("Network request failed"));
      xhr.ontimeout = () => reject(new ApiError("The backend took too long to answer"));
      xhr.onload = () => {
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

  async verify(sessionId: string, photoUri: string): Promise<VerifyResponse> {
    const form = new FormData();
    form.append("photo", { uri: photoUri, name: "photo.jpg", type: "image/jpeg" } as any);
    return this.multipart(`/sessions/${sessionId}/verify`, form);
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
}
