import { motion, MotionConfig } from "motion/react";
import { useEffect, type ReactNode } from "react";
import { motionTokens as t } from "./tokens";
import { useEffectiveTier, useMotionPreference } from "./store";

export function MotionProvider({ children }: { children: ReactNode }) {
  const tier = useEffectiveTier();
  useEffect(() => {
    if (tier === "Reduced" || tier === "Off") return;
    let frame = 0,
      previous = performance.now(),
      started = previous;
    let samples: number[] = [];
    const sample = (now: number) => {
      if (document.hidden) {
        samples = [];
        started = now;
      } else {
        samples.push(now - previous);
        if (now - started > 3000) {
          const sorted = samples.slice().sort((a, b) => a - b);
          if ((sorted[Math.floor(sorted.length / 2)] ?? 0) > 20)
            useMotionPreference.getState().stepDown();
          samples = [];
          started = now;
        }
      }
      previous = now;
      frame = requestAnimationFrame(sample);
    };
    frame = requestAnimationFrame(sample);
    return () => cancelAnimationFrame(frame);
  }, [tier]);
  return (
    <MotionConfig
      reducedMotion={tier === "Reduced" || tier === "Off" ? "always" : "user"}
      transition={t.spring.soft}
    >
      <div data-motion={tier}>{children}</div>
    </MotionConfig>
  );
}
export function Reveal({
  children,
  className = "",
  delay = 0,
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
}) {
  const tier = useEffectiveTier();
  return (
    <motion.div
      className={className}
      initial={
        tier === "Off" ? false : { opacity: 0, y: tier === "Reduced" ? 0 : t.distance.small }
      }
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...t.spring.soft, delay: delay * t.stagger }}
    >
      {children}
    </motion.div>
  );
}
export function SpotlightCard({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`card spotlight ${className}`}
      onPointerMove={(e) => {
        if (useMotionPreference.getState().tier !== "Full") return;
        const box = e.currentTarget.getBoundingClientRect();
        e.currentTarget.style.setProperty("--mx", `${e.clientX - box.left}px`);
        e.currentTarget.style.setProperty("--my", `${e.clientY - box.top}px`);
      }}
    >
      {children}
    </div>
  );
}
export function PulseDot() {
  return <span className="pulse-dot" aria-hidden="true" />;
}
export function Shimmer() {
  return (
    <div className="shimmer" aria-label="Loading agent data">
      <div />
      <div />
      <div />
    </div>
  );
}
export function AuroraBackground() {
  return (
    <div className="aurora" aria-hidden="true">
      <i />
      <i />
      <i />
    </div>
  );
}
