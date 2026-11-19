import { AlertTriangle, Check, Loader2 } from "lucide-react";

import { Card } from "@/components/ui/card";
import { NODE_LABELS, progress } from "@/lib/chat/answer";
import type { ChatMessage } from "@/types/chat";

import { Steps } from "./steps";

// The generating screen: search nodes done so far and the answer draft.
export function Generating({ message, draft, running }: { message: ChatMessage; draft: string; running: boolean }) {
  const done = progress(message);
  const nodes = message.parts.flatMap((p) => (p.type === "data-node" ? [p.data] : []));
  return (
    <Card className="space-y-5 p-6">
      <div className="flex items-center gap-3">
        {running ? <Loader2 size={20} className="animate-spin text-brand-600" /> : <AlertTriangle size={20} className="text-mute" />}
        <div>
          <h2 className="font-semibold">{running ? "답변을 생성하고 있습니다" : "답변이 중단되었습니다"}</h2>
          <p className="text-[13px] text-sub">통계표를 검색하고 조건을 확인한 뒤, 코드가 수치를 계산합니다.</p>
        </div>
      </div>
      <Steps done={done} />
      {nodes.length > 0 && (
        <ol className="space-y-1 text-[13px] text-sub">
          {nodes.map((n) => (
            <li key={n.name} className="flex items-center gap-2">
              <Check size={14} className="text-brand-600" />
              {NODE_LABELS[n.name] ?? n.name}
              <span className="tabular-nums text-mute">{(n.seconds * 1000).toFixed(0)} ms</span>
            </li>
          ))}
        </ol>
      )}
      {draft && <p className="text-[15px] leading-relaxed whitespace-pre-wrap text-sub">{draft}</p>}
    </Card>
  );
}
