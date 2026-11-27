"use client";

import { LazyMotion, useMotionValue, useReducedMotion, useScroll, useTransform } from "motion/react";
import * as m from "motion/react-m";

import { loadedMotionFeatures } from "./features";
import { useRef, type ReactNode } from "react";

// Fades and slides a block in as it scrolls up into view (scroll-linked, T3-64). Progress is 0 when its top meets
// the bottom of the screen and 1 when its bottom does, so a block that already fits on screen is fully shown
// even where the page cannot scroll. Reduced motion: fixed motion values (shown as is). Always motion values,
// never plain numbers, so the style motion writes is always its own. Rendered only on the client after hydration
// (LazyScrollReveal in ./lazy), so there is no server render to match.
export function ScrollReveal({ children, className, distance = 24 }: { children: ReactNode; className?: string; distance?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start end", "end end"] });
  const opacity = useTransform(scrollYProgress, [0, 1], [0, 1]);
  const y = useTransform(scrollYProgress, [0, 1], [distance, 0]);
  const shown = useMotionValue(1);
  const still = useMotionValue(0);
  const reduce = useReducedMotion();
  const features = loadedMotionFeatures(); // loaded with this chunk (./lazy); null only if that failed: plain block
  if (!features) return <div ref={ref} className={className}>{children}</div>;
  return (
    <LazyMotion features={features} strict>
      <m.div ref={ref} data-scroll-reveal className={className} style={reduce ? { opacity: shown, y: still } : { opacity, y }}>
        {children}
      </m.div>
    </LazyMotion>
  );
}
