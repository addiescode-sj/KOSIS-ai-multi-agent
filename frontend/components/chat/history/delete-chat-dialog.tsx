"use client";

import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { conversations } from "@/lib/chat/conversations";
import { useDeleteChat } from "@/lib/chat/use-chat-list";
import type { ConversationItem } from "@/types/account";

// Confirm, then delete (member: DELETE /chat/{id}; guest: this browser + its server thread). Deleting the open
// conversation leaves it for a new one.
export function DeleteChatDialog({ item, member, activeId, onClose, onDeleted }: {
  item: ConversationItem | null;
  member: boolean;
  activeId?: string;
  onClose: () => void;
  onDeleted?: () => void;
}) {
  const router = useRouter();
  const del = useDeleteChat();
  return (
    <Dialog.Root open={item !== null} onOpenChange={(open) => { if (!open) { del.reset(); onClose(); } }}>
      <Dialog.Content variant="destructive">
        <Dialog.Title>이 대화를 삭제하시겠습니까?</Dialog.Title>
        <Dialog.Description>
          ‘{item?.title}’ 대화를 {member ? "계정에서" : "이 탭에서"} 지웁니다. 삭제하면 되돌릴 수 없습니다.
        </Dialog.Description>
        {del.error && <p role="alert" className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-700">{del.error.message}</p>}
        <Dialog.Footer>
          <Dialog.Close asChild><Button variant="secondary">취소</Button></Dialog.Close>
          <Button
            variant="destructive"
            disabled={del.isPending}
            onClick={() => {
              if (!item) return;
              const threadId = member ? item.id : (conversations.get(item.id)?.threadId ?? null);
              del.mutate({ id: item.id, threadId }, {
                onSuccess: () => {
                  onClose();
                  onDeleted?.();
                  if (item.id === activeId) router.push("/");
                },
              });
            }}
          >
            삭제
          </Button>
        </Dialog.Footer>
      </Dialog.Content>
    </Dialog.Root>
  );
}
