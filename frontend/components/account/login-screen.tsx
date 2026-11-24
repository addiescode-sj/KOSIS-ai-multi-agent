"use client";

import { CircleAlert } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";

import { AuthShell } from "./auth-shell";
import { KakaoLoginButton } from "./kakao-login-button";

// `/login`. The Kakao callback sends the browser back here with `?error=` when consent was cancelled or Kakao
// failed (contract 6장: "로그인하지 못했습니다", continue as a guest).
export function LoginScreen() {
  const router = useRouter();
  const failed = useSearchParams().has("error");
  return (
    <AuthShell>
      <h1 className="mt-5 text-xl font-semibold">카카오 계정으로 로그인</h1>
      <p className="mt-2 text-sm text-sub">로그인하면 대화 기록을 어느 기기에서나 볼 수 있어요.</p>
      <KakaoLoginButton size="lg" className="mt-7" />
      <ul className="mt-6 space-y-1.5 rounded-lg bg-amber-50 px-3 py-2.5 text-left text-xs leading-relaxed text-amber-800">
        <li>비회원으로 나눈 대화는 로그인 후 계정으로 이전되지 않습니다.</li>
        <li>계정에 저장된 대화는 마지막으로 이어 물은 날부터 90일 동안 보관됩니다.</li>
      </ul>
      <Link href="/" className="mt-5 inline-block text-sm font-medium text-sub hover:text-ink">비회원으로 계속하기</Link>

      <Dialog.Root open={failed} onOpenChange={(o) => !o && router.replace("/login")}>
        <Dialog.Content>
          <Dialog.Title className="flex items-center gap-2"><CircleAlert size={20} className="text-red-500" />카카오 로그인에 실패했습니다</Dialog.Title>
          <Dialog.Description>로그인이 완료되지 않았거나 동의가 취소되었습니다. 다시 시도하거나 비회원으로 계속 이용할 수 있습니다.</Dialog.Description>
          <Dialog.Footer>
            <Button variant="secondary" asChild><Link href="/">비회원으로 계속</Link></Button>
            {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- a backend redirect (route handler), not a page */}
            <Button asChild><a href="/auth/kakao/login">다시 시도</a></Button>
          </Dialog.Footer>
        </Dialog.Content>
      </Dialog.Root>
    </AuthShell>
  );
}
