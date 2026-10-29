// JS side of the glasses bridge: an Expo local module (modules/glasses-bridge/android) that wraps the
// Meta Wearables Device Access Toolkit. Null on platforms without the native module (iOS, web, Expo Go).
import { requireOptionalNativeModule, type NativeModule } from "expo";

export type GlassesState =
  | "idle"
  | "connecting"
  | "registering"
  | "permission"
  | "connected"
  | "streaming"
  | "paused"
  | "disconnected"
  | "error";

export type ConnectionEvent = {
  state: GlassesState;
  message: string | null;
  mock: boolean;
  device: string | null;
  displayCapable: boolean;
  registration: string;
  frames: number;
};

/** A downscaled JPEG of the latest glasses frame, as a data URI for <Image>. */
export type FrameEvent = { uri: string; width: number; height: number; ts: number; n: number };

export type ConnectOptions = {
  /** Use the Mock Device Kit instead of real glasses (no hardware, no Meta AI app). */
  mock?: boolean;
  /** Which simulated glasses to pair: the Display model lets the card path run on the mock kit. */
  mockModel?: "rayban_meta" | "meta_rayban_display";
  /** "camera:back", "camera:front", or a file/content URI of an H.265 video the mock streams. */
  mockFeed?: string;
  /** Image the mock returns from capturePhoto(); without it the latest frame is used. */
  mockPhotoUri?: string;
  /** 720x1280 (high), 504x896 (medium), 360x640 (low). The toolkit's ladder may step down. */
  quality?: "high" | "medium" | "low";
  /** 2, 7, 15, 24 or 30. */
  frameRate?: number;
  previewWidth?: number;
  previewFps?: number;
  previewQuality?: number;
  /** Pixel layout of the decoded frames; the toolkit asks its decoder for planar I420. */
  yuv?: "i420" | "nv12" | "nv21";
};

export type CapturedPhoto = {
  uri: string;
  width: number;
  height: number;
  /** "glasses" when capturePhoto() on the stream succeeded, "frame" when the latest stream frame was used instead. */
  source: "glasses" | "frame";
  note?: string | null;
};

/** One 600x600 card for Meta Ray-Ban Display; ignored (returns false) on glasses without a display. */
export type Card = {
  kind?: "step" | "gate" | "checking" | "pass" | "fail" | "unclear" | "listening";
  eyebrow?: string;
  title: string;
  body?: string;
  hint?: string;
  imageUri?: string;
};

type GlassesEvents = {
  connection: (event: ConnectionEvent) => void;
  frame: (event: FrameEvent) => void;
};

declare class GlassesBridgeModule extends NativeModule<GlassesEvents> {
  sdkVersion: string;
  getState(): ConnectionEvent;
  connect(options: ConnectOptions): Promise<ConnectionEvent>;
  disconnect(): Promise<void>;
  capturePhoto(): Promise<CapturedPhoto>;
  showCard(card: Card): Promise<boolean>;
  clearCard(): Promise<boolean>;
}

export const Glasses: GlassesBridgeModule | null = requireOptionalNativeModule<GlassesBridgeModule>("GlassesBridge");

/** True when the native bridge is in this binary (Android build with the module linked). */
export const glassesAvailable = Glasses !== null;
