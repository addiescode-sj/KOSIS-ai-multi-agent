import { MessageSquare, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { formatDay } from "@/lib/chat/history";
import { useWarmChat } from "@/lib/chat/use-chat-list";
import type { ConversationItem } from "@/types/account";

// One conversation in the /chat list. A member row the pointer or keyboard is on warms its conversation
// (GET /chat/{id}) before the click. `member` is read on every render from the auth query (ConversationList ->
// ListView), not kept from the mount: the warm-up only runs while it is true, so a logout on another tab or an
// expired login stops it even if the row was hovered before.
export function ConversationRow({ item, member, onDelete }: {
  item: ConversationItem;
  member: boolean;
  onDelete: (item: ConversationItem) => void;
}) {
  const [hovered, setHovered] = useState(false);
  const warm = member && hovered;

  function handleIntent() {
    setHovered(true);
  }

  function handleDelete() {
    onDelete(item);
  }

  return (
    <div className="flex items-center gap-4 px-5 py-4 hover:bg-gray-50/70" onPointerEnter={handleIntent} onFocus={handleIntent}>
      {warm && <WarmChat id={item.id} />}
      <span className="flex size-10 shrink-0 items-center justify-center rounded-full border border-brand-100 bg-brand-50 text-brand-600">
        <MessageSquare size={17} />
      </span>
      <Link href={`/chat/${item.id}`} className="min-w-0 flex-1">
        <p className="truncate text-[15px] font-medium">{item.title}</p>
        <p className="mt-0.5 text-xs text-mute">{formatDay(item.updatedAt)} · 질문 {item.questions}개</p>
      </Link>
      <Button variant="ghost" size="sm" className="text-mute hover:text-red-600" onClick={handleDelete}>
        <Trash2 />삭제
      </Button>
    </div>
  );
}

function WarmChat({ id }: { id: string }) {
  useWarmChat(id);
  return null;
}
