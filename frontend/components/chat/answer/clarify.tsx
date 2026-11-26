import { HelpCircle } from "lucide-react";

import { Chip } from "@/components/ui/chip";
import type { ChatResult } from "@/lib/chat/schemas";
import type { Ask } from "@/types/chat";

import { Notice } from "./notice";

export function Clarify({ result, onAsk }: { result: ChatResult; onAsk?: Ask }) {
  const options = result.stat_answer?.clarify_options ?? [];
  return (
    <>
      <Notice icon={HelpCircle} tone="brand" title={result.answer}>
        <p>질문하신 내용을 정확히 답하려면 조건을 하나 더 확인해야 합니다.</p>
      </Notice>
      {options.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {options.map((o) => <Chip key={o} onClick={onAsk && (() => onAsk(o, "clarify_option"))}>{o}</Chip>)}
        </div>
      )}
      <p className="text-xs text-mute">선택지에 없으면 아래 입력창에 조건을 직접 적어 주세요.</p>
    </>
  );
}
