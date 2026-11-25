import { ExternalLink } from "lucide-react";

import { citation } from "@/lib/chat/answer";
import type { ChatResult } from "@/lib/chat/schemas";
import type { Citation } from "@/types/answer";

import { Label } from "./label";

// onOpen: the 원표 보기 link was clicked (citation_click event); the link opens as usual either way.
export function SourceBlock({ result, onOpen }: { result: ChatResult; onOpen?: (c: Citation) => void }) {
  const c = citation(result);
  if (!c) return null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-canvas px-4 py-3">
      <div className="min-w-0">
        <Label>출처</Label>
        <p className="mt-0.5 text-[13px] text-ink">{c.text}</p>
      </div>
      {c.url && (
        <a href={c.url} target="_blank" rel="noreferrer" onClick={() => onOpen?.(c)} className="inline-flex items-center gap-1 text-[13px] font-medium text-brand-600 hover:underline">
          원표 보기 <ExternalLink size={13} />
        </a>
      )}
    </div>
  );
}
