// Live transcription over the backend's /asr relay (Muse Voice Transcribe, push-to-talk).
//
// open()  -> connect, send the start message, resolve when the relay says ready
// push()  -> raw 16-bit mono PCM; resampled to 16 kHz here if the mic gave another rate
// finish()-> send endStream and resolve with the final transcript (or the last partial)

export type LiveEvents = { onPartial?: (text: string) => void; onError?: (message: string) => void };

export class LiveTranscript {
  private ws: WebSocket | null = null;
  private ready = false;
  private last = "";
  private finalText: string | null = null;
  private finalWaiters: ((t: string) => void)[] = [];
  private pending: ArrayBuffer[] = [];
  failed = false;

  constructor(private url: string, private skillId: string, private apiKey: string, private events: LiveEvents = {}) {}

  open(): Promise<void> {
    return new Promise((resolve) => {
      let settled = false;
      const done = () => {
        if (!settled) {
          settled = true;
          resolve();
        }
      };
      try {
        const ws = new WebSocket(this.url);
        this.ws = ws;
        ws.binaryType = "arraybuffer";
        ws.onopen = () => {
          ws.send(JSON.stringify({ type: "start", skill_id: this.skillId, api_key: this.apiKey, sample_rate: 16000 }));
        };
        ws.onmessage = (ev) => {
          let msg: any;
          try {
            msg = JSON.parse(String(ev.data));
          } catch {
            return;
          }
          if (msg.type === "ready") {
            this.ready = true;
            for (const buf of this.pending) ws.send(buf);
            this.pending = [];
            done();
          } else if (msg.type === "transcript") {
            const text = String(msg.transcript ?? "");
            if (text) this.last = text;
            this.events.onPartial?.(this.last);
            if (msg.final) this.resolveFinal(this.last);
          } else if (msg.type === "error") {
            this.fail(String(msg.message ?? "transcription error"));
            done();
          }
        };
        ws.onerror = () => {
          this.fail("live transcription unavailable");
          done();
        };
        ws.onclose = () => {
          if (this.finalText === null) this.resolveFinal(this.last);
          done();
        };
        setTimeout(done, 4000); // never block recording on the socket
      } catch (e: any) {
        this.fail(e?.message ?? String(e));
        done();
      }
    });
  }

  push(data: ArrayBuffer, sampleRate: number, channels: number) {
    if (this.failed) return;
    const pcm = toMono16k(data, sampleRate, channels);
    if (this.ready && this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(pcm);
    else if (this.pending.length < 400) this.pending.push(pcm); // ~30 s at 80 ms frames
  }

  finish(timeoutMs = 5000): Promise<string> {
    if (this.finalText !== null) return Promise.resolve(this.finalText);
    if (this.failed || !this.ws) return Promise.resolve(this.last);
    try {
      if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "endStream" }));
    } catch {}
    return new Promise((resolve) => {
      const t = setTimeout(() => resolve(this.last), timeoutMs);
      this.finalWaiters.push((text) => {
        clearTimeout(t);
        resolve(text);
      });
    });
  }

  close() {
    try {
      this.ws?.close();
    } catch {}
    this.ws = null;
  }

  private resolveFinal(text: string) {
    if (this.finalText !== null) return;
    this.finalText = text;
    for (const w of this.finalWaiters) w(text);
    this.finalWaiters = [];
    this.close();
  }

  private fail(message: string) {
    if (this.failed) return;
    this.failed = true;
    this.events.onError?.(message);
    this.resolveFinal(this.last);
  }
}

/** Int16 interleaved PCM at any rate -> mono Int16 at 16 kHz (linear decimation). */
export function toMono16k(data: ArrayBuffer, sampleRate: number, channels: number): ArrayBuffer {
  const src = new Int16Array(data);
  const frames = Math.floor(src.length / Math.max(1, channels));
  if (sampleRate === 16000 && channels === 1) return data;
  const ratio = sampleRate / 16000;
  const outLen = Math.floor(frames / ratio);
  const out = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio;
    const i0 = Math.floor(pos);
    const i1 = Math.min(frames - 1, i0 + 1);
    const frac = pos - i0;
    let a = 0;
    let b = 0;
    for (let c = 0; c < channels; c++) {
      a += src[i0 * channels + c];
      b += src[i1 * channels + c];
    }
    a /= channels;
    b /= channels;
    out[i] = Math.round(a + (b - a) * frac);
  }
  return out.buffer;
}
