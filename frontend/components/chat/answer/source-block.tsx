import { ExternalLink } from "lucide-react";

import { citation } from "@/lib/chat/answer";
import type { ChatResult } from "@/lib/chat/schemas";

import { Label } from "./label";

export function SourceBlock({ result }: { result: ChatResult }) {
  const c = citation(result);
  if (!c) return null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-canvas px-4 py-3">
      <div className="min-w-0">
        <Label>출처</Label>
        <p className="mt-0.5 text-[13px] text-ink">{c.text}</p>
      </div>
      {c.url && (
        <a href={c.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[13px] font-medium text-brand-600 hover:underline">
          원표 보기 <ExternalLink size={13} />
        </a>
      )}
    </div>
  );
}
