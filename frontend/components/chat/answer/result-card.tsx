import { AlertTriangle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { answerKind } from "@/lib/chat/answer";
import type { ChatResult } from "@/lib/chat/schemas";

import { Answered } from "./answered";
import { Clarify } from "./clarify";
import { Notice } from "./notice";
import { Partial } from "./partial";
import { Refused } from "./refused";
import { SourceBlock } from "./source-block";

// The card for one finished turn, chosen by its outcome.
export function ResultCard({ result, onAsk }: { result: ChatResult; onAsk?: (text: string) => void }) {
  const kind = answerKind(result);
  const interpreted = result.resolved_question ?? result.question;
  return (
    <Card className="space-y-5 p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-mute">
          해석된 질문 · <span className="text-sub">{interpreted}</span>
        </p>
        <div className="flex gap-1.5">
          {result.reused_table && <Badge variant="blue">직전 표 이어서</Badge>}
          {result.degraded && <Badge variant="amber">간이 검색 순서</Badge>}
          {kind === "answered" && <Badge variant="green">계산 완료</Badge>}
          {kind === "partial" && <Badge variant="amber">일부만 답변</Badge>}
        </div>
      </div>
      {kind === "answered" && <Answered result={result} />}
      {kind === "partial" && <Partial result={result} />}
      {kind === "clarify" && <Clarify result={result} onAsk={onAsk} />}
      {kind === "refused" && <Refused result={result} onAsk={onAsk} />}
      {kind === "failed" && (
        <Notice icon={AlertTriangle} tone="red" title="답을 만들지 못했습니다">
          <p className="whitespace-pre-wrap">{result.answer || result.chart_note}</p>
        </Notice>
      )}
      {result.chart_note && kind !== "failed" && <p className="text-[13px] text-amber-700">{result.chart_note}</p>}
      <SourceBlock result={result} />
    </Card>
  );
}
