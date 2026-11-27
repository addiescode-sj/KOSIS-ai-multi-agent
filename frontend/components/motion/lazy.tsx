"use client";

import { lazy, Suspense, useSyncExternalStore, type ReactNode } from "react";

import { preloadMotionFeatures } from "./features";

// The scroll effects are decoration, so their code (motion's scroll hooks and spring) is not in the first screen's
// JS: each loads as its own chunk once the page has hydrated (T3-64). Until then the page looks as it would
// without them. Both wait for hydration (not next/dynamic ssr:false, which made the page's streamed content
// client-render instead of hydrate), so the server HTML and the first client render are the same.

// Each waits for motion's features too (`m.*` binds motion values only with them); if those fail to load, the
// component renders the plain version.
const Progress = lazy(() => Promise.all([import("./scroll-progress"), preloadMotionFeatures()]).then(([m]) => ({ default: m.ScrollProgress })));
const Reveal = lazy(() => Promise.all([import("./scroll-reveal"), preloadMotionFeatures()]).then(([m]) => ({ default: m.ScrollReveal })));

const noSubscribe = () => () => {};
const useHydrated = () => useSyncExternalStore(noSubscribe, () => true, () => false);

// The top progress bar: nothing until its chunk arrives (it starts at 0 anyway).
export function LazyScrollProgress() {
  const hydrated = useHydrated();
  if (!hydrated) return null;
  return (
    <Suspense fallback={null}>
      <Progress />
    </Suspense>
  );
}

// A block that fades in with the scroll: a plain, fully shown <div> on the server and until the chunk arrives
// (no hidden content without JS or before hydration), then the scroll-linked version.
export function LazyScrollReveal({ children, className }: { children: ReactNode; className?: string }) {
  const hydrated = useHydrated();
  const plain = <div className={className}>{children}</div>;
  if (!hydrated) return plain;
  return (
    <Suspense fallback={plain}>
      <Reveal className={className}>{children}</Reveal>
    </Suspense>
  );
}
