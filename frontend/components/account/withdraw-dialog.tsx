"use client";

import { Check } from "lucide-react";
import { useRouter } from "next/navigation";
import { Checkbox } from "radix-ui";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { accountSettings } from "@/lib/account/settings";
import { useRefreshAuth } from "@/lib/auth/use-me";
import { useChatList } from "@/lib/chat/use-chat-list";

// Withdrawal calls DELETE /auth/me. The contract does not have it yet (proposed to the backend), so the screen moves
// on to "탈퇴 완료" only when the server says it is done; otherwise it says it could not. The count shown is the
// server's conversation list; the feedback count has no source and is not shown.
export function WithdrawDialog({ open, onOpenChange, provider }: { open: boolean; onOpenChange: (open: boolean) => void; provider: string }) {
  const router = useRouter();
  const refresh = useRefreshAuth();
  const list = useChatList();
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function change(o: boolean) {
    onOpenChange(o);
    if (!o) { setAgree(false); setError(null); }
  }

  async function withdraw() {
    setBusy(true);
    setError(null);
    const res = await fetch("/auth/me", { method: "DELETE" }).catch(() => null);
    setBusy(false);
    if (!res?.ok) {
      setError(`탈퇴를 처리하지 못했습니다${res ? ` (${res.status})` : ""}. 계정과 대화는 그대로 있습니다.`);
      return;
    }
    accountSettings.reset();
    change(false);
    await refresh();
    router.push("/withdrawn");
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
          <li className="flex justify-between"><span>연결된 계정</span><span className="font-medium text-ink">{provider}</span></li>
        </ul>
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
          <Button variant="destructive" disabled={!agree || busy} onClick={withdraw}>탈퇴하기</Button>
        </Dialog.Footer>
      </Dialog.Content>
    </Dialog.Root>
  );
}
