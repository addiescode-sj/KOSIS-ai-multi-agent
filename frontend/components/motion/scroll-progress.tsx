"use client";

import { motion, useReducedMotion, useScroll, useSpring, useTransform } from "motion/react";

// A thin bar across the top that grows with how far the page is scrolled (scroll-linked scaleX, T3-62).
// The window is the scroller (AppShell only has sticky parts). Hidden at the top, so a page that cannot scroll
// (progress reads 1 there) shows no bar. Reduced motion: the bar follows without the spring.
export function ScrollProgress() {
  const { scrollY, scrollYProgress } = useScroll();
  const opacity = useTransform(scrollY, [0, 1], [0, 1]);
  const smooth = useSpring(scrollYProgress, { stiffness: 200, damping: 30, restDelta: 0.001 });
  const reduce = useReducedMotion();
  return (
    <motion.div
      aria-hidden
      data-scroll-progress
      className="pointer-events-none fixed inset-x-0 top-0 z-30 h-[3px] origin-left bg-brand-600"
      style={{ scaleX: reduce ? scrollYProgress : smooth, opacity }}
    />
  );
}
