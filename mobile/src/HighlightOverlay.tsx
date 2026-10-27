// The photo the learner just sent, with the SAM 3.1 mask drawn over the part in question.
// Coordinates come in the frame of the resized photo; the SVG viewBox maps them onto
// the image, which is shown with resizeMode "contain" inside the same box.
import React, { useEffect } from "react";
import { Image, Pressable, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import Svg, { G, Path, Rect } from "react-native-svg";
import type { Highlight } from "./api";
import { colors } from "./theme";

export function HighlightOverlay({
  photoUri,
  highlight,
  color,
  caption,
  onDismiss,
  autoDismissMs = 7000,
}: {
  photoUri: string;
  highlight: Highlight;
  color: string;
  caption: string;
  onDismiss: () => void;
  autoDismissMs?: number;
}) {
  const { width: W, height: H } = useWindowDimensions();
  const fw = highlight.frame_width;
  const fh = highlight.frame_height;
  // Fit the frame into the window like resizeMode="contain".
  const scale = Math.min(W / fw, (H - 220) / fh);
  const w = fw * scale;
  const h = fh * scale;

  useEffect(() => {
    const t = setTimeout(onDismiss, autoDismissMs);
    return () => clearTimeout(t);
  }, [onDismiss, autoDismissMs]);

  return (
    <Pressable style={s.backdrop} onPress={onDismiss} accessibilityLabel="Dismiss highlighted photo">
      <View style={{ width: w, height: h }}>
        <Image source={{ uri: photoUri }} style={{ width: w, height: h }} resizeMode="contain" />
        <Svg width={w} height={h} viewBox={`0 0 ${fw} ${fh}`} style={StyleSheet.absoluteFill as any} pointerEvents="none">
          {highlight.objects.map((o) => (
            <G key={o.id}>
              {o.mask ? (
                <G transform={`translate(${o.mask.left}, ${o.mask.top})`}>
                  <Path d={o.mask.path} fill={color} fillOpacity={0.35} stroke={color} strokeWidth={3 / scale} strokeLinejoin="round" />
                </G>
              ) : null}
              <Rect
                x={o.box.x1}
                y={o.box.y1}
                width={o.box.x2 - o.box.x1}
                height={o.box.y2 - o.box.y1}
                fill="none"
                stroke={color}
                strokeWidth={2 / scale}
                strokeDasharray={`${8 / scale} ${6 / scale}`}
              />
            </G>
          ))}
        </Svg>
      </View>
      <View style={[s.label, { borderColor: color }]}>
        <View style={[s.swatch, { backgroundColor: color }]} />
        <Text style={s.labelText}>
          {caption} · <Text style={{ color }}>{highlight.concept}</Text>
        </Text>
      </View>
      <Text style={s.hint}>Tap to go back to the camera</Text>
    </Pressable>
  );
}

const s = StyleSheet.create({
  backdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: "rgba(15,17,21,0.92)", alignItems: "center", justifyContent: "center", gap: 14 },
  label: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 14, height: 36, borderRadius: 18, borderWidth: 1, backgroundColor: colors.surface },
  swatch: { width: 10, height: 10, borderRadius: 5 },
  labelText: { color: colors.text, fontSize: 13, fontWeight: "600" },
  hint: { color: colors.muted, fontSize: 12 },
});
