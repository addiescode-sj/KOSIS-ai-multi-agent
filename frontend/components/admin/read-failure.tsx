"use client";

import { usePathname } from "next/navigation";

import { Button } from "@/components/ui/button";
import { loginHref } from "@/lib/auth/login-href";
import { DashboardFailure } from "@/lib/admin/queries";

import { AdminNotice } from "./admin-notice";

// Why a screen's read came back empty: the login ended meanwhile, the operator was taken off the list, or the operator
// app or its database could not answer (the message says which).
export function ReadFailure({ failure, message }: { failure: DashboardFailure; message: string }) {
  const pathname = usePathname();
  if (failure === DashboardFailure.LOGIN) {
    return (
      <AdminNotice title="로그인이 끝났습니다" text="다시 로그인하면 이 화면으로 돌아옵니다.">
        <Button asChild size="sm"><a href={loginHref(pathname)}>로그인</a></Button>
      </AdminNotice>
    );
  }
  if (failure === DashboardFailure.FORBIDDEN) return <AdminNotice title="볼 수 없습니다" text={message} />;
  return <AdminNotice title="불러오지 못했습니다" text={`${message} 잠시 뒤 다시 시도해 주세요.`} alert />;
}
