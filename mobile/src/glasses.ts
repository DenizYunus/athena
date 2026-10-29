// Glasses as the session's camera: a hook over the native bridge (modules/glasses-bridge).
// The toggle connects/disconnects; frames arrive as small JPEG data URIs for the preview;
// capturePhoto() returns a file URI the API client can upload like a phone photo.
import { useCallback, useEffect, useRef, useState } from "react";
import { PermissionsAndroid, Platform } from "react-native";
import { Glasses, glassesAvailable, type Card, type CapturedPhoto, type ConnectionEvent, type ConnectOptions, type GlassesState } from "../modules/glasses-bridge";

export type { Card, CapturedPhoto, GlassesState };

export type GlassesHook = {
  /** The native module is in this build. */
  available: boolean;
  /** The toggle. */
  on: boolean;
  state: GlassesState;
  message: string | null;
  /** Frames are flowing (or paused from the glasses touchpad). */
  live: boolean;
  mock: boolean;
  displayCapable: boolean;
  /** Latest preview frame as a data URI, null until the first frame. */
  frameUri: string | null;
  toggle: () => void;
  capturePhoto: () => Promise<CapturedPhoto>;
  /** Fire-and-forget card for Meta Ray-Ban Display; a no-op on glasses without a display. */
  showCard: (card: Card) => void;
};

const IDLE: ConnectionEvent = { state: "idle", message: null, mock: false, device: null, displayCapable: false, registration: "uninitialized", frames: 0 };

export function useGlasses(options: ConnectOptions): GlassesHook {
  const [on, setOn] = useState(false);
  const onRef = useRef(false);
  const [conn, setConn] = useState<ConnectionEvent>(() => Glasses?.getState() ?? IDLE);
  const [frameUri, setFrameUri] = useState<string | null>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;
  // Each toggle is one attempt; the outcome of an older attempt is ignored.
  const attemptRef = useRef(0);

  useEffect(() => {
    const native = Glasses;
    if (!native) return;
    const a = native.addListener("connection", (e) => {
      setConn(e);
      if (e.state !== "streaming" && e.state !== "paused") setFrameUri(null);
    });
    const b = native.addListener("frame", (e) => {
      if (onRef.current) setFrameUri(e.uri);
    });
    return () => {
      a.remove();
      b.remove();
      onRef.current = false;
      native.disconnect().catch(() => {});
    };
  }, []);

  // A failure leaves the toggle on with state "error" (pill "Glasses !", message on screen);
  // the next tap on the pill turns it off. `attempt` is the toggle that scheduled this call: a
  // timer from an earlier toggle never starts a native connect.
  const connect = useCallback(async (attempt: number) => {
    const native = Glasses;
    const stale = () => attempt !== attemptRef.current || !onRef.current;
    if (!native || stale()) return;
    if (Platform.OS === "android" && Number(Platform.Version) >= 31 && !optionsRef.current.mock) {
      const r = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.BLUETOOTH_CONNECT);
      if (stale()) return;
      if (r !== PermissionsAndroid.RESULTS.GRANTED) {
        setConn((c) => ({ ...c, state: "error", message: "Bluetooth permission is needed to reach the glasses" }));
        return;
      }
    }
    try {
      await native.connect(optionsRef.current);
    } catch (e: any) {
      if (stale()) return;
      // The native side already reported the error as a connection event; keep that snapshot so
      // the screen shows the message once, without Expo's "Call to function ... rejected" wrapper.
      const snap = native.getState();
      setConn((c) => (snap.state === "error" ? snap : { ...c, state: "error", message: nativeMessage(e) }));
    }
  }, []);

  const toggle = useCallback(() => {
    if (!Glasses) return;
    const next = !onRef.current;
    onRef.current = next;
    attemptRef.current += 1;
    setOn(next);
    if (next) {
      setConn((c) => ({ ...c, state: "connecting", message: null }));
      // The phone camera view is unmounted first; the Mock Device Kit may feed from that same camera.
      const attempt = attemptRef.current;
      setTimeout(() => connect(attempt), 350);
    } else {
      setFrameUri(null);
      Glasses.disconnect().catch(() => {});
    }
  }, [connect]);

  const capturePhoto = useCallback(async (): Promise<CapturedPhoto> => {
    if (!Glasses) throw new Error("Glasses are not available in this build");
    try {
      return await Glasses.capturePhoto();
    } catch (e: any) {
      throw new Error(nativeMessage(e));
    }
  }, []);

  const showCard = useCallback((card: Card) => {
    if (!Glasses || !onRef.current) return;
    Glasses.showCard(card).catch(() => {});
  }, []);

  const live = on && (conn.state === "streaming" || conn.state === "paused");
  return {
    available: glassesAvailable,
    on,
    state: on ? conn.state : "idle",
    message: conn.message,
    live,
    mock: conn.mock,
    displayCapable: conn.displayCapable,
    frameUri: live ? frameUri : null,
    toggle,
    capturePhoto,
    showCard,
  };
}

/** The native error's own text: Expo prefixes it with "Call to function '…' has been rejected. → Caused by:". */
function nativeMessage(e: any): string {
  const m = String(e?.message ?? e);
  const parts = m.split(/Caused by:\s*/);
  return parts[parts.length - 1].trim() || m;
}
