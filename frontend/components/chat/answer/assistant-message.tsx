import { Info } from "lucide-react";
import { useEffect } from "react";

import { EnterCard } from "@/components/motion/enter-card";
import { preloadMotionFeatures } from "@/components/motion/features";
import { answerKind, copyText, requestIdOf } from "@/lib/chat/answer";
import type { ChatResult, TurnFeedback } from "@/lib/chat/schemas";
import type { Citation } from "@/types/answer";
import { citationClick, copyAnswer } from "@/lib/chat/screen-events";
import { useTrack } from "@/lib/events/use-track";
import type { Ask, ChatMessage } from "@/types/chat";

import { CopyButton } from "./copy-button";
import { FeedbackBar } from "./feedback-bar";
import { Generating } from "./generating";
import { ResultCard } from "./result-card";

// One assistant turn: progress while the stream runs, then the card for the turn's outcome with 복사 and 👍/👎
// (neither on a failed turn: there is no answer to copy or rate, api.md 10장 제안 5).
// live: the turn arrived on this screen (not part of the conversation it opened with). Only a live card fades in
// and counts up its 핵심 수치; a restored conversation shows its cards as they are (T3-62).
export function AssistantMessage({ message, running, live = false, onAsk, threadId = null, turn = null, feedback = null, onFeedback }: {
  message: ChatMessage;
  running: boolean;
  live?: boolean;
  onAsk: Ask;
  threadId?: string | null;
  turn?: number | null;
  feedback?: TurnFeedback | null; // the 👍/👎 kept for this answer (guest: this tab, member: GET /chat/{id})
  onFeedback?: (requestId: string, feedback: TurnFeedback) => void;
}) {
  const track = useTrack();
  // a turn on its way: fetch the entrance animation's code now so it is there when the card arrives (T3-62)
  useEffect(() => { if (live) void preloadMotionFeatures(); }, [live]);
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
  const requestId = requestIdOf(message);
  const ids = { requestId, threadId, turn };
  const rated = result && answerKind(result) !== "failed";

  function handleCitation(citation: Citation) {
    const fields = citationClick(citation, ids);
    if (fields) track("citation_click", fields);
  }

  function handleCopied() {
    track("copy_answer", copyAnswer(ids, result?.table_key ?? null));
  }

  function handleFeedbackSaved(saved: TurnFeedback) {
    if (requestId) onFeedback?.(requestId, saved);
  }

  return (
    <div className="flex flex-col gap-3">
      {notices.map((n) => (
        <p key={n} role="status" className="flex items-center gap-2 rounded-xl bg-amber-50 px-4 py-2.5 text-[13px] text-amber-800">
          <Info size={15} />
          {n}
        </p>
      ))}
      {result ? (
        <EnterCard live={live}>
          <ResultCard
            result={result}
            live={live}
            onAsk={onAsk}
            onCitation={handleCitation}
            actions={rated && <CopyButton text={copyText(result)} onCopied={handleCopied} />}
            footer={rated && requestId && (
              <FeedbackBar key={requestId} requestId={requestId} initial={feedback} onSaved={handleFeedbackSaved} />
            )}
          />
        </EnterCard>
      ) : running || draft || progressed || notices.length === 0 ? (
        <Generating message={message} draft={draft} running={running} />
      ) : null /* a saved turn that could not be restored: the notice is the whole answer */}
    </div>
  );
}
