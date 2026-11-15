import { create } from "zustand";
import { persist } from "zustand/middleware";
export type MotionTier = "Full" | "Balanced" | "Reduced" | "Off";
export const tiers: MotionTier[] = ["Full", "Balanced", "Reduced", "Off"];
export const nextTier = (tier: MotionTier): MotionTier =>
  tier === "Full" ? "Balanced" : tier === "Balanced" ? "Reduced" : tier;
export const useMotionPreference = create(
  persist<{
    tier: MotionTier;
    setTier: (tier: MotionTier) => void;
    notice: string;
    stepDown: () => void;
  }>(
    (set) => ({
      tier: "Balanced",
      notice: "",
      setTier: (tier) => set({ tier }),
      stepDown: () =>
        set((state) => ({
          tier: nextTier(state.tier),
          notice: "Motion simplified to keep your console responsive.",
        })),
    }),
    { name: "eb-motion" },
  ),
);
export function useEffectiveTier() {
  const tier = useMotionPreference((s) => s.tier);
  return typeof matchMedia !== "undefined" &&
    matchMedia("(prefers-reduced-motion: reduce)").matches &&
    tier !== "Off"
    ? "Reduced"
    : tier;
}
