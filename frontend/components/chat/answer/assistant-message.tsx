import { Info } from "lucide-react";

import type { ChatResult } from "@/lib/chat/schemas";
import type { ChatMessage } from "@/types/chat";

import { Generating } from "./generating";
import { ResultCard } from "./result-card";

// One assistant turn: progress while the stream runs, then the card for the turn's outcome.
export function AssistantMessage({ message, running, onAsk }: {
  message: ChatMessage;
  running: boolean;
  onAsk: (text: string) => void;
}) {
  let result: ChatResult | undefined;
  let draft = "";
  let progressed = false;
  const notices: string[] = [];
  for (const p of message.parts) {
    if (p.type === "data-result") result = p.data;
    else if (p.type === "data-draft") draft = p.data.text;
    else if (p.type === "data-notice") notices.push(p.data.text);
    else progressed ||= p.type === "data-node" || p.type === "data-stat";
  }
  return (
    <div className="flex flex-col gap-3">
      {notices.map((n) => (
        <p key={n} className="flex items-center gap-2 rounded-xl bg-amber-50 px-4 py-2.5 text-[13px] text-amber-800">
          <Info size={15} />
          {n}
        </p>
      ))}
      {result ? (
        <ResultCard result={result} onAsk={onAsk} />
      ) : running || draft || progressed || notices.length === 0 ? (
        <Generating message={message} draft={draft} running={running} />
      ) : null /* a saved turn that could not be restored: the notice is the whole answer */}
    </div>
  );
}
