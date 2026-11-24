import { cn } from "cn";

import { KakaoMark } from "./kakao-mark";

const SIZES = {
  sm: "h-9 rounded-lg px-3.5 text-[13px]",
  md: "h-9 w-full rounded-lg text-[13px]",
  lg: "h-12 w-full rounded-xl text-[15px]",
};

// A plain link on purpose: GET /auth/kakao/login answers with a redirect to Kakao's consent screen (contract 6장)
export function KakaoLoginButton({ size = "md", children = "카카오로 로그인", className }: {
  size?: keyof typeof SIZES;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    // eslint-disable-next-line @next/next/no-html-link-for-pages -- a backend redirect (route handler), not a page
    <a
      href="/auth/kakao/login"
      className={cn("flex items-center justify-center gap-2 bg-[#FEE500] font-semibold text-ink hover:brightness-95", SIZES[size], className)}
    >
      <KakaoMark />
      {children}
    </a>
  );
}
