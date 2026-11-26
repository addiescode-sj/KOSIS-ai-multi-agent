import { Card } from "@/components/ui/card";
import { EMPTY_MESSAGE, ListBody as Body, emptyReasonOf, listBodyOf } from "@/lib/chat/list-view";
import type { ChatList } from "@/lib/chat/use-chat-list";
import type { ConversationItem } from "@/types/account";

import { ConversationRow } from "./conversation-row";

const MESSAGE_CARD = "px-5 py-12 text-center text-sm";

// The /chat list body: one branch per state (lib/chat/list-view listBodyOf)
export function ListBody({ list, shown, onDelete }: {
  list: ChatList;
  shown: ConversationItem[];
  onDelete: (item: ConversationItem) => void;
}) {
  switch (listBodyOf(list, shown.length)) {
    case Body.ERROR:
      return <Card className={`${MESSAGE_CARD} text-red-600`}>{list.error}</Card>;
    case Body.LOADING:
      return <Card className={`${MESSAGE_CARD} text-sub`}>대화 목록을 불러오는 중입니다…</Card>;
    case Body.ITEMS:
      return (
        <Card className="divide-y divide-line">
          {shown.map((c) => <ConversationRow key={c.id} item={c} member={list.member} onDelete={onDelete} />)}
        </Card>
      );
    case Body.EMPTY:
      return <Card className={`${MESSAGE_CARD} text-sub`}>{EMPTY_MESSAGE[emptyReasonOf(list)]}</Card>;
  }
}
