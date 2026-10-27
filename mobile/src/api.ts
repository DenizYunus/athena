// Thin client for the Athena backend (backend/app/main.py).
import type { Settings } from "./settings";

export type Step = {
  n: number;
  instruction: string;
  expected_state: string;
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
  steps: Step[];
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

export type VerifyResponse = {
  verdict: Verdict;
  advanced: boolean;
  current_step: number;
  completed: boolean;
};

export class ApiError extends Error {
  constructor(message: string, public status?: number) {
    super(message);
  }
}

export class Api {
  constructor(private settings: Settings) {}

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    const h: Record<string, string> = { ...extra };
    if (this.settings.apiKey) h["X-Model-API-Key"] = this.settings.apiKey;
    return h;
  }

  private url(path: string): string {
    if (!this.settings.backendUrl) throw new ApiError("Backend URL is not set. Open Settings.");
    return `${this.settings.backendUrl}${path}`;
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
    return (await res.json()) as T;
  }

  async health(): Promise<{ ok: boolean; skills: number; server_has_key: boolean }> {
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

  async startSession(skillId: string): Promise<Session> {
    const res = await fetch(this.url("/sessions"), {
      method: "POST",
      headers: this.headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ skill_id: skillId, mode: "ptt" }),
    });
    return this.handle(res);
  }

  /** Push-to-talk turn: the clip carries the user's voice; transcript is optional. */
  async turn(
    sessionId: string,
    opts: { transcript?: string; clipUri?: string; photoUri?: string },
  ): Promise<{ reply: string; current_step: number }> {
    const form = new FormData();
    form.append("transcript", opts.transcript ?? "");
    if (opts.clipUri) {
      form.append("clip", { uri: opts.clipUri, name: "clip.mp4", type: "video/mp4" } as any);
    }
    if (opts.photoUri) {
      form.append("photo", { uri: opts.photoUri, name: "photo.jpg", type: "image/jpeg" } as any);
    }
    const res = await fetch(this.url(`/sessions/${sessionId}/turn`), {
      method: "POST",
      headers: this.headers(),
      body: form,
    });
    return this.handle(res);
  }

  async verify(sessionId: string, photoUri: string): Promise<VerifyResponse> {
    const form = new FormData();
    form.append("photo", { uri: photoUri, name: "photo.jpg", type: "image/jpeg" } as any);
    const res = await fetch(this.url(`/sessions/${sessionId}/verify`), {
      method: "POST",
      headers: this.headers(),
      body: form,
    });
    return this.handle(res);
  }

  async skip(sessionId: string): Promise<Session> {
    const res = await fetch(this.url(`/sessions/${sessionId}/skip`), {
      method: "POST",
      headers: this.headers(),
    });
    return this.handle(res);
  }
}
