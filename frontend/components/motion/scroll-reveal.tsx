"use client";

import { motion, useMotionValue, useReducedMotion, useScroll, useTransform } from "motion/react";
import { useRef, useSyncExternalStore, type ReactNode } from "react";

// Fades and slides a block in as it scrolls up into view (scroll-linked, T3-61). Progress is 0 when its top meets
// the bottom of the screen and 1 when its bottom does, so a block that already fits on screen is fully shown
// even where the page cannot scroll. Reduced motion: fixed motion values (shown as is). Always motion values,
// never plain numbers: motion writes them after hydration, so the server's starting style does not stay. The
// reduced-motion switch waits for hydration so the first client render matches the server's.
export function ScrollReveal({ children, className, distance = 24 }: { children: ReactNode; className?: string; distance?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start end", "end end"] });
  const opacity = useTransform(scrollYProgress, [0, 1], [0, 1]);
  const y = useTransform(scrollYProgress, [0, 1], [distance, 0]);
  const shown = useMotionValue(1);
  const still = useMotionValue(0);
  const hydrated = useSyncExternalStore(noSubscribe, () => true, () => false);
  const reduce = useReducedMotion() && hydrated;
  return (
    <motion.div ref={ref} data-scroll-reveal className={className} style={reduce ? { opacity: shown, y: still } : { opacity, y }}>
      {children}
    </motion.div>
  );
}

const noSubscribe = () => () => {};
