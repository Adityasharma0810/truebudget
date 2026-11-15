export const motionTokens = {
  duration: { instant: 0.09, fast: 0.18, base: 0.32, slow: 0.64, epic: 1.2 },
  spring: {
    soft: { type: "spring" as const, stiffness: 180, damping: 26 },
    snappy: { type: "spring" as const, stiffness: 380, damping: 30 },
    bouncy: { type: "spring" as const, stiffness: 240, damping: 17 },
  },
  stagger: 0.04,
  distance: { small: 8, base: 16, large: 32 },
};
