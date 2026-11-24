"use client";

import { usePathname } from "next/navigation";
import { cn } from "cn";

import { loginHref } from "@/lib/auth/login-href";

import { KakaoMark } from "./kakao-mark";

const SIZES = {
  sm: "h-9 rounded-lg px-3.5 text-[13px]",
  md: "h-9 w-full rounded-lg text-[13px]",
  lg: "h-12 w-full rounded-xl text-[15px]",
};

// A plain link on purpose: GET /auth/kakao/login answers with a redirect to Kakao's consent screen (contract 6장)
export function KakaoLoginButton({ size = "md", next, children = "카카오로 로그인", className }: {
  size?: keyof typeof SIZES;
  next?: string | null; // the page to come back to; default: this one
  children?: React.ReactNode;
  className?: string;
}) {
  const pathname = usePathname();
  return (
    <a
      href={loginHref(next ?? pathname)}
      className={cn("flex items-center justify-center gap-2 bg-[#FEE500] font-semibold text-ink hover:brightness-95", SIZES[size], className)}
    >
      <KakaoMark />
      {children}
    </a>
  );
}
