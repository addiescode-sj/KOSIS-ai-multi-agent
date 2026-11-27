"use client";

import { useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";

import { COUNT_UP_SECONDS, countFrame, type CountTarget } from "@/lib/motion/count-up";

// 핵심 수치 counting up from 0 to the server's value (T3-62). Only on a turn that streamed in on this screen
// (live): a restored turn, the server render and reduced motion show the value at once. The final string holds
// the width (invisible) and is the text a screen reader or a drag-copy gets; the counting digits sit on top of it.
// It starts counting once the number is mostly on screen (the chat scrolls a new answer's number into view first).
export function CountUp({ target, live, duration = COUNT_UP_SECONDS }: { target: CountTarget; live: boolean; duration?: number }) {
  const reduce = useReducedMotion();
  const run = live && !reduce;
  const [frame, setFrame] = useState<string | null>(() => (live ? countFrame(target, 0) : null));
  const box = useRef<HTMLSpanElement>(null);
  const [seen, setSeen] = useState(false);

  useEffect(() => {
    if (!run || seen) return;
    const el = box.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      setSeen(true); // no way to tell: count now
      return;
    }
    const watch = new IntersectionObserver(([e]) => { if (e.isIntersecting) setSeen(true); }, { threshold: 0.6 });
    watch.observe(el);
    return () => watch.disconnect();
  }, [run, seen]);

  // primitives, not the target object: the card re-renders while the stream finishes and must not restart this
  const { value, raw, round } = target;
  // animate is loaded only now (import(): not in the first screen's JS). If it cannot load, show the value.
  useEffect(() => {
    if (!run || !seen) return;
    let cancelled = false;
    let stop = () => {};
    import("./animate")
      .then(({ animate }) => {
        if (cancelled) return;
        const controls = animate(0, 1, {
          duration,
          ease: "linear", // countFrame eases
          onUpdate: (p) => setFrame(countFrame({ value, raw, round }, p)),
          onComplete: () => setFrame(null),
        });
        stop = () => controls.stop();
      })
      .catch(() => { if (!cancelled) setFrame(null); });
    return () => { cancelled = true; stop(); };
  }, [run, seen, value, raw, round, duration]);

  const counting = run && frame !== null;
  return (
    <span ref={box} className="relative inline-block tabular-nums">
      <span className={counting ? "invisible" : undefined}>{value}</span>
      {counting && (
        <>
          <span className="sr-only">{value}</span>
          <span aria-hidden data-count-frame className="absolute inset-y-0 right-0 whitespace-nowrap select-none">
            {frame}
          </span>
        </>
      )}
    </span>
  );
}
