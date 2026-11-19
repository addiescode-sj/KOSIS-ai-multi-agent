import { cn } from "cn";

import { headline, STEPS } from "@/lib/chat/answer";
import type { ChatResult } from "@/lib/chat/schemas";

import { LazyStatChart } from "./lazy-stat-chart";
import { Label } from "./label";
import { Steps } from "./steps";

export function Answered({ result }: { result: ChatResult }) {
  const head = result.sub_results ? null : headline(result); // a multi-question turn has no single headline
  const caveats = result.stat_answer?.caveats ?? [];
  return (
    <>
      <Steps done={STEPS.length} />
      {(head || result.chart) && (
        <div className={cn("grid gap-6 border-t border-line pt-6", head && result.chart && "md:grid-cols-[220px_1fr]")}>
          {head && (
            <div>
              <Label>핵심 수치</Label>
              <p className="mt-1 text-[40px] leading-none font-bold tracking-tight">
                {head.value}
                {head.unit && <span className="ml-1 text-xl font-semibold text-sub">{head.unit}</span>}
              </p>
              {head.period && <p className="mt-3 text-xs text-mute">기준 기간: {head.period}</p>}
            </div>
          )}
          {result.chart && <LazyStatChart spec={result.chart} />}
        </div>
      )}
      <div className="space-y-1 border-t border-line pt-5">
        <Label>답변</Label>
        <p className="text-[15px] leading-relaxed whitespace-pre-wrap">{result.answer}</p>
      </div>
      {caveats.length > 0 && (
        <ul className="space-y-1 rounded-xl bg-amber-50/70 px-4 py-3 text-[13px] text-amber-900">
          {caveats.map((c) => <li key={c.kind + c.text}>· {c.text}</li>)}
        </ul>
      )}
    </>
  );
}
