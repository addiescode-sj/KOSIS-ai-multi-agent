import { LogOut } from "lucide-react";
import Link from "next/link";

import { AuthShell } from "@/components/account/auth-shell";
import { KakaoLoginButton } from "@/components/account/kakao-login-button";

export default function Page() {
  return (
    <AuthShell icon={LogOut} tone="bg-brand-50 text-brand-600">
      <h1 className="mt-5 text-xl font-semibold">로그아웃되었습니다</h1>
      <p className="mt-2 text-sm text-sub">계정에 저장된 대화는 다시 로그인하면 그대로 볼 수 있습니다.</p>
      <KakaoLoginButton size="lg" className="mt-7">다시 로그인</KakaoLoginButton>
      <Link href="/" className="mt-5 inline-block text-sm font-medium text-sub hover:text-ink">비회원으로 계속하기</Link>
    </AuthShell>
  );
}
