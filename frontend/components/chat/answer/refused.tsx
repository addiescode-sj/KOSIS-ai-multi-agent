import {
  Ban, CalendarX2, CircleSlash, ListX, Scale, SearchX, Sigma, type LucideIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Chip } from "@/components/ui/chip";
import { refusalReason, refusalTitle } from "@/lib/chat/answer";
import { EXAMPLES } from "@/lib/chat/examples";
import type { ChatResult } from "@/lib/chat/schemas";
import type { Ask } from "@/types/chat";

import { Notice } from "./notice";

const REASON_ICONS: Record<string, LucideIcon> = {
  no_period: CalendarX2, no_item: SearchX, class_mismatch: ListX, unit_mismatch: Scale,
  not_aggregatable: Sigma, causal: Ban, missing_value: CircleSlash, out_of_scope: SearchX,
};

const SHOW_REASON_CODE = process.env.NODE_ENV !== "production";

export function Refused({ result, onAsk }: { result: ChatResult; onAsk?: Ask }) {
  const reason = refusalReason(result);
  const { title, tone } = refusalTitle(reason);
  const available = result.stat_answer?.refusal?.available;
  const suggest = reason === "causal" || reason === "out_of_scope" || result.route === "refuse";
  return (
    <>
      <Notice icon={(reason && REASON_ICONS[reason]) || SearchX} tone={tone} title={title}>
        <p className="whitespace-pre-wrap">{result.answer}</p>
        {available && <p>대신 확인할 수 있는 범위: <b className="text-ink">{available}</b></p>}
      </Notice>
      {/* the reason code is for developers: not on the deployed screen (T3-68) */}
      {reason && SHOW_REASON_CODE && <div className="flex gap-2"><Badge variant="gray">{reason}</Badge></div>}
      {suggest && onAsk && (
        <div className="rounded-xl bg-canvas p-4">
          <p className="text-[13px] font-medium">대신 이런 숫자 질문은 확인할 수 있어요</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {EXAMPLES.slice(0, 2).map((e) => <Chip key={e} onClick={() => onAsk(e)}>{e}</Chip>)}
          </div>
        </div>
      )}
    </>
  );
}
