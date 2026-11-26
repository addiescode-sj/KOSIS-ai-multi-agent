"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";

// 복사: puts the answer text on the clipboard. onCopied fires only when the browser took it (copy_answer event).
// "복사됨" stays until the next click (no timer: AGENTS.md).
export function CopyButton({ text, onCopied }: { text: string; onCopied?: () => void }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
      onCopied?.();
    } catch {
      setState("failed"); // no clipboard permission (e.g. an insecure origin)
    }
  }
  return (
    <Button variant="ghost" size="sm" onClick={copy} aria-label="답변 복사">
      {state === "copied" ? <Check /> : <Copy />}
      {state === "copied" ? "복사됨" : state === "failed" ? "복사하지 못했습니다" : "복사"}
    </Button>
  );
}
