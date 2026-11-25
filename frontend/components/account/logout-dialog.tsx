"use client";

import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { useLogout } from "@/lib/auth/use-account-actions";

// POST /auth/logout ends this device's session only (contract 6장; 204 even when it was already gone)
export function LogoutDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const router = useRouter();
  const action = useLogout(() => {
    onOpenChange(false);
    router.push("/logged-out");
  });
  const error = action.data?.ok === false ? action.data.message : null;

  return (
    <Dialog.Root open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) action.reset(); }}>
      <Dialog.Content>
        <Dialog.Title>로그아웃할까요?</Dialog.Title>
        <Dialog.Description>계정에 저장된 대화는 다시 로그인하면 그대로 볼 수 있습니다.</Dialog.Description>
        {error && <p role="alert" className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-700">{error}</p>}
        <Dialog.Footer>
          <Dialog.Close asChild><Button variant="secondary">취소</Button></Dialog.Close>
          <Button onClick={() => action.mutate()} disabled={action.isPending}><LogOut />로그아웃</Button>
        </Dialog.Footer>
      </Dialog.Content>
    </Dialog.Root>
  );
}
