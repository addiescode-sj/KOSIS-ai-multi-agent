"use client";

import { useEffect, useRef, type RefObject } from "react";

import { cardHeadline, resultOf } from "@/lib/chat/answer";
import { COUNT_UP_SECONDS } from "@/lib/motion/count-up";
import type { ChatMessage } from "@/types/chat";

// How long a new answer's 핵심 수치 stays in view before the page moves on to the whole answer: the smooth scroll
// to it, the count-up, and a moment to read the number.
const SCROLL_MS = 500;
const READ_MS = 700;
export const HEADLINE_HOLD_MS = SCROLL_MS + COUNT_UP_SECONDS * 1000 + READ_MS;

// A reader scrolling or typing takes over: the page stays where they put it
const TAKE_OVER = ["wheel", "touchmove", "keydown"] as const;

// The conversation follows the newest message (the progress while it streams). When a new answer with a 핵심 수치
// arrives (T3-62), it first centers that number so its count-up is seen, then after HEADLINE_HOLD_MS goes on to the
// end of the answer. Restored turns and reduced motion (no count-up) go straight to the end.
export function useAnswerScroll(messages: ChatMessage[], status: string, openedWith: Set<string>, bottom: RefObject<HTMLDivElement | null>) {
  const focused = useRef<string | null>(null); // the answer whose number was shown first (once per answer)
  const hold = useRef<(() => void) | null>(null); // cancels the pending move on; set while the number is in view

  useEffect(() => () => hold.current?.(), []);

  useEffect(() => {
    if (!messages.length || hold.current) return; // while the number is held, later stream parts do not scroll
    const last = messages.at(-1)!;
    const result = last.role === "assistant" && !openedWith.has(last.id) ? resultOf(last) : null;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (result && cardHeadline(result) && focused.current !== last.id && !reduce) {
      focused.current = last.id;
      const number = document.querySelector(`[data-message-id="${CSS.escape(last.id)}"] [data-headline]`);
      if (number) {
        number.scrollIntoView({ behavior: "smooth", block: "center" });
        const cancel = () => {
          window.clearTimeout(timer);
          for (const t of TAKE_OVER) window.removeEventListener(t, cancel);
          hold.current = null;
        };
        const timer = window.setTimeout(() => {
          cancel();
          bottom.current?.scrollIntoView({ behavior: "smooth" });
        }, HEADLINE_HOLD_MS);
        for (const t of TAKE_OVER) window.addEventListener(t, cancel, { passive: true });
        hold.current = cancel;
        return;
      }
    }
    // Block body: Chrome's scrollIntoView returns a Promise, which React would call as the effect cleanup
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, status, openedWith, bottom]);
}
