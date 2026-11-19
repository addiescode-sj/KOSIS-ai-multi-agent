import { MessageSquare } from "lucide-react";
import Link from "next/link";

import { Card } from "@/components/ui/card";
import { textOf } from "@/lib/chat/text";
import type { Conversation } from "@/types/conversation";

import { Highlight } from "./highlight";

export function ConversationSection({ convs, q }: { convs: Conversation[]; q: string }) {
  return (
    <section>
      <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-sub"><MessageSquare size={16} />내 대화 <span className="text-mute">{convs.length}</span></h2>
      {convs.length ? (
        <Card className="divide-y divide-line">
          {convs.map((c) => {
            const question = c.messages.find((m) => m.role === "user");
            const answer = c.messages.findLast((m) => m.role === "assistant");
            return (
              <Link key={c.id} href={`/chat/${c.id}`} className="group block px-5 py-4 hover:bg-gray-50/70">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-[15px] font-semibold"><Highlight text={c.title} q={q} /></p>
                  <span className="shrink-0 text-xs text-mute">{new Date(c.updatedAt).toLocaleDateString("ko-KR")}</span>
                </div>
                {question && <p className="mt-2 text-[13px] text-sub"><span className="mr-1.5 font-semibold text-ink">Q</span><Highlight text={textOf(question)} q={q} /></p>}
                {answer && <p className="mt-1 line-clamp-2 text-[13px] leading-relaxed text-sub"><span className="mr-1.5 font-semibold text-brand-600">A</span><Highlight text={textOf(answer)} q={q} /></p>}
              </Link>
            );
          })}
        </Card>
      ) : (
        <Card className="px-5 py-8 text-center text-sm text-sub">일치하는 대화가 없어요.</Card>
      )}
    </section>
  );
}
