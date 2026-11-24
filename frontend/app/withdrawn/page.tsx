import { UserX } from "lucide-react";
import Link from "next/link";

import { AuthShell } from "@/components/account/auth-shell";
import { Button } from "@/components/ui/button";

// Reached only after DELETE /auth/me succeeded (components/account/withdraw-dialog)
export default function Page() {
  return (
    <AuthShell icon={UserX} tone="bg-gray-100 text-sub">
      <h1 className="mt-5 text-xl font-semibold">회원 탈퇴가 완료되었습니다</h1>
      <p className="mt-2 text-sm leading-relaxed text-sub">
        계정과 저장된 대화, 피드백 기록이 모두 삭제되었습니다.<br />그동안 KOSIS 통계 AI를 이용해 주셔서 감사합니다.
      </p>
      <Button asChild size="lg" className="mt-7 w-full"><Link href="/">처음으로</Link></Button>
    </AuthShell>
  );
}
