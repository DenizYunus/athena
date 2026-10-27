import React from "react";
import { Pressable, StyleSheet, Text, View, ViewStyle } from "react-native";
import { colors, radius } from "./theme";

export function Chip({ children, color = colors.text, style }: { children: React.ReactNode; color?: string; style?: ViewStyle }) {
  return (
    <View style={[s.chip, style]}>
      <Text style={[s.chipText, { color }]}>{children}</Text>
    </View>
  );
}

export function PrimaryButton({ label, onPress, disabled, style }: { label: string; onPress: () => void; disabled?: boolean; style?: ViewStyle }) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [s.primary, disabled && { opacity: 0.5 }, pressed && { opacity: 0.85 }, style]}
      accessibilityRole="button"
    >
      <Text style={s.primaryText}>{label}</Text>
    </Pressable>
  );
}

export function GhostButton({ label, onPress, color = colors.text, style }: { label: string; onPress: () => void; color?: string; style?: ViewStyle }) {
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [s.ghost, pressed && { opacity: 0.7 }, style]} accessibilityRole="button">
      <Text style={[s.ghostText, { color }]}>{label}</Text>
    </Pressable>
  );
}

export function Card({ children, style }: { children: React.ReactNode; style?: ViewStyle }) {
  return <View style={[s.card, style]}>{children}</View>;
}

const s = StyleSheet.create({
  chip: {
    height: 28,
    paddingHorizontal: 10,
    borderRadius: 14,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
  },
  chipText: { fontSize: 12, fontWeight: "600" },
  primary: {
    height: 54,
    borderRadius: radius.lg,
    backgroundColor: colors.amber,
    alignItems: "center",
    justifyContent: "center",
  },
  primaryText: { color: colors.onAmber, fontSize: 16, fontWeight: "700" },
  ghost: {
    height: 46,
    paddingHorizontal: 14,
    borderRadius: radius.md,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
  },
  ghostText: { fontSize: 14, fontWeight: "600" },
  card: {
    borderRadius: radius.md,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 14,
  },
});
