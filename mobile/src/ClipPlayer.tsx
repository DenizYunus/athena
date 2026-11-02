// The expert doing one step: the short clip the digester cut from the author's video
// (compiled/media/<id>/step_<n>.mp4, served by GET /skills/{id}/media/{name}).
// A Modal, so the Android back button closes it and it opens above the session's camera preview.
// useVideoPlayer releases the native player when this unmounts, so callers mount it only while a
// clip is open.
import { useEvent } from "expo";
import { useVideoPlayer, VideoView } from "expo-video";
import React from "react";
import { ActivityIndicator, Modal, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import { colors, radius } from "./theme";
import { GhostButton } from "./ui";

export function ClipPlayer({ uri, title, onClose }: { uri: string; title: string; onClose: () => void }) {
  const { width: W, height: H } = useWindowDimensions();
  const player = useVideoPlayer(uri, (p) => {
    p.loop = false;
    p.play();
  });
  const { status } = useEvent(player, "statusChange", { status: player.status });

  // One box for portrait and landscape clips alike; contentFit "contain" letterboxes the other one.
  const boxW = W - 24;
  const boxH = Math.min(H * 0.62, boxW * 1.5);

  return (
    <Modal transparent animationType="fade" onRequestClose={onClose}>
      <View style={s.backdrop}>
        <Text style={s.title}>{title}</Text>
        <View style={[s.box, { width: boxW, height: boxH }]}>
          {/* A TextureView composes with the views around it; the session's camera preview is a
              SurfaceView, and two overlapping SurfaceViews can hide one another. The clips are
              short and small, so the extra cost does not matter. */}
          <VideoView player={player} nativeControls contentFit="contain" surfaceType="textureView" fullscreenOptions={{ enable: false }} style={StyleSheet.absoluteFill} />
          {/* Only while the clip loads. On Android expo-video reports "idle" again once a clip has
              played to its end, and a spinner over the last frame would look like buffering. */}
          {status === "loading" ? (
            <View style={s.center} pointerEvents="none">
              <ActivityIndicator color={colors.amber} />
            </View>
          ) : null}
          {status === "error" ? (
            <View style={s.center}>
              <Text style={s.errorText}>The clip did not load. Check that the backend is reachable, then try again.</Text>
            </View>
          ) : null}
        </View>
        <GhostButton label="Close" onPress={onClose} style={{ alignSelf: "stretch", marginHorizontal: 12 }} />
      </View>
    </Modal>
  );
}

const s = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: "rgba(15,17,21,0.96)", alignItems: "center", justifyContent: "center", gap: 14 },
  title: { color: colors.amber, fontSize: 12, fontWeight: "700", letterSpacing: 0.5, paddingHorizontal: 16, textAlign: "center" },
  box: { borderRadius: radius.md, overflow: "hidden", backgroundColor: colors.bg, borderWidth: 1, borderColor: colors.border },
  center: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, alignItems: "center", justifyContent: "center", padding: 24 },
  errorText: { color: colors.text2, fontSize: 14, lineHeight: 20, textAlign: "center" },
});
