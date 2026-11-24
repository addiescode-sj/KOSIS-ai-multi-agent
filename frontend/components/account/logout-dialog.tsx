"use client";

import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { useRefreshAuth } from "@/lib/auth/use-me";

// POST /auth/logout ends this device's session only (contract 6장; 204 even when it was already gone)
export function LogoutDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const router = useRouter();
  const refresh = useRefreshAuth();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function logout() {
    setBusy(true);
    setError(null);
    const res = await fetch("/auth/logout", { method: "POST" }).catch(() => null);
    setBusy(false);
    if (!res || (!res.ok && res.status !== 401)) {
      setError(`로그아웃하지 못했습니다${res ? ` (${res.status})` : ""}. 잠시 뒤 다시 시도해 주세요.`);
      return;
    }
    onOpenChange(false);
    await refresh();
    router.push("/logged-out");
  }

  return (
    <Dialog.Root open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) setError(null); }}>
      <Dialog.Content>
        <Dialog.Title>로그아웃할까요?</Dialog.Title>
        <Dialog.Description>계정에 저장된 대화는 다시 로그인하면 그대로 볼 수 있습니다.</Dialog.Description>
        {error && <p role="alert" className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-700">{error}</p>}
        <Dialog.Footer>
          <Dialog.Close asChild><Button variant="secondary">취소</Button></Dialog.Close>
          <Button onClick={logout} disabled={busy}><LogOut />로그아웃</Button>
        </Dialog.Footer>
      </Dialog.Content>
    </Dialog.Root>
  );
}
