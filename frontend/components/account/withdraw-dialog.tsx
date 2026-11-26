"use client";

import { Check } from "lucide-react";
import { useRouter } from "next/navigation";
import { Checkbox } from "radix-ui";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { useWithdraw } from "@/lib/auth/use-account-actions";
import { useChatList } from "@/lib/chat/use-chat-list";

// Withdrawal calls DELETE /auth/me (lib/auth/use-account-actions). The screen moves on to "탈퇴 완료" only when the server says it is done;
// otherwise it says it could not. The count shown is the
// server's conversation list; the feedback count has no source and is not shown.
export function WithdrawDialog({ open, onOpenChange, provider }: { open: boolean; onOpenChange: (open: boolean) => void; provider: string }) {
  const router = useRouter();
  const list = useChatList();
  const [agree, setAgree] = useState(false);
  const action = useWithdraw(() => {
    onOpenChange(false);
    router.push("/withdrawn");
  });
  const error = action.data?.ok === false ? action.data.message : null;

  function change(o: boolean) {
    onOpenChange(o);
    if (!o) { setAgree(false); action.reset(); }
  }

  const count = list.loading ? "확인 중" : list.error ? "확인하지 못함" : `${list.items.length}${list.more ? "개 이상" : "개"}`;
  return (
    <Dialog.Root open={open} onOpenChange={change}>
      <Dialog.Content variant="destructive">
        <Dialog.Title>회원 탈퇴</Dialog.Title>
        <Dialog.Description>탈퇴하면 아래 정보가 즉시 삭제되며 복구할 수 없습니다.</Dialog.Description>
        <ul className="mt-4 space-y-2 rounded-xl bg-canvas p-4 text-sm text-sub">
          <li className="flex justify-between"><span>저장된 대화</span><span className="font-medium text-ink">{count}</span></li>
          <li className="flex justify-between"><span>답변 피드백 기록</span><span className="font-medium text-ink">모두 삭제</span></li>
          <li className="flex justify-between"><span>로그인 정보</span><span className="font-medium text-ink">{provider}</span></li>
        </ul>
        <p className="mt-3 text-xs leading-relaxed text-sub">카카오 쪽 앱 연결은 자동으로 끊기지 않습니다. 카카오 계정 관리의 ‘연결된 서비스’에서 해제할 수 있습니다.</p>
        <label className="mt-4 flex cursor-pointer items-start gap-2.5 text-sm">
          <Checkbox.Root
            checked={agree}
            onCheckedChange={(v) => setAgree(v === true)}
            className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded border border-line bg-white data-[state=checked]:border-red-600 data-[state=checked]:bg-red-600"
          >
            <Checkbox.Indicator className="text-white"><Check size={12} strokeWidth={3} /></Checkbox.Indicator>
          </Checkbox.Root>
          <span>안내 사항을 확인했으며, 모든 대화 기록 삭제에 동의합니다.</span>
        </label>
        {error && <p role="alert" className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-700">{error}</p>}
        <Dialog.Footer>
          <Dialog.Close asChild><Button variant="secondary">취소</Button></Dialog.Close>
          <Button variant="destructive" disabled={!agree || action.isPending} onClick={() => action.mutate()}>탈퇴하기</Button>
        </Dialog.Footer>
      </Dialog.Content>
    </Dialog.Root>
  );
}
