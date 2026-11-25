import { AlertTriangle, Check, Loader2, RotateCw } from "lucide-react";

import { Card } from "@/components/ui/card";
import { NODE_LABELS, progress, turnState } from "@/lib/chat/answer";
import type { ChatMessage } from "@/types/chat";

import { Steps } from "./steps";

const HEAD = {
  working: { title: "답변을 생성하고 있습니다", text: "통계표를 검색하고 조건을 확인한 뒤, 코드가 수치를 계산합니다." },
  reconnecting: { title: "다시 연결하는 중", text: "답변 서버와 연결이 끊겨 한 번 더 시도하고 있습니다." },
  stopped: { title: "답변이 중단되었습니다", text: "통계표를 검색하고 조건을 확인한 뒤, 코드가 수치를 계산합니다." },
  incomplete: { title: "답변이 중단되었습니다", text: "답이 끝나기 전에 멈췄습니다." },
};

// The generating screen: search nodes done so far and the answer draft. A reconnect (api.md 2 error) replaces the
// steps with its own state; a draft left by a turn that never finished is kept, marked unfinished.
export function Generating({ message, draft, running }: { message: ChatMessage; draft: string; running: boolean }) {
  const state = turnState(message, running);
  const done = progress(message);
  const nodes = message.parts.flatMap((p) => (p.type === "data-node" ? [p.data] : []));
  const head = HEAD[state];
  return (
    <Card className="space-y-5 p-6" data-state={state}>
      <div className="flex items-center gap-3">
        {state === "working" && <Loader2 size={20} className="animate-spin text-brand-600" />}
        {state === "reconnecting" && <RotateCw size={20} className="animate-spin text-amber-600" />}
        {(state === "stopped" || state === "incomplete") && <AlertTriangle size={20} className="text-mute" />}
        <div role="status">
          <h2 className="font-semibold">{head.title}</h2>
          <p className="text-[13px] text-sub">{head.text}</p>
        </div>
      </div>
      {state === "reconnecting" ? (
        <p className="rounded-xl bg-amber-50 px-4 py-2.5 text-[13px] text-amber-800">자동으로 한 번 다시 연결합니다. 잠시만 기다려 주세요.</p>
      ) : (
        <Steps done={done} />
      )}
      {nodes.length > 0 && state !== "reconnecting" && (
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
      {state === "incomplete" && (
        <p role="alert" className="flex items-center gap-2 rounded-xl bg-amber-50 px-4 py-2.5 text-[13px] text-amber-800">
          <AlertTriangle size={15} />
          아래 문장은 완성되지 않은 답입니다. 숫자와 출처를 확인할 수 없으니 그대로 쓰지 마세요.
        </p>
      )}
      {draft && <p className="text-[15px] leading-relaxed whitespace-pre-wrap text-sub">{draft}</p>}
    </Card>
  );
}
