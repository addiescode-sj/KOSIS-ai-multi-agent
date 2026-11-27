"use client";

import { LazyMotion } from "motion/react";
import * as m from "motion/react-m";
import { useState, type ReactNode } from "react";

import { loadedMotionFeatures } from "./features";

// A new answer card fades and slides in (T3-64, the note's fadeIn + slideIn). Only a live card (it arrived on this
// screen) and only if the motion features are already loaded when it mounts; otherwise, and for restored cards,
// it is shown at once, so a card is never left hidden waiting for a chunk.
export function EnterCard({ live, children }: { live: boolean; children: ReactNode }) {
  const [features] = useState(() => (live ? loadedMotionFeatures() : null));
  if (!features) return <div>{children}</div>;
  return (
    <LazyMotion features={features} strict>
      <m.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35, ease: "easeOut" }}>
        {children}
      </m.div>
    </LazyMotion>
  );
}
