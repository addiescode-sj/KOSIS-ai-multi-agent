import type { ReactNode } from "react";

import { Card } from "@/components/ui/card";

import { KakaoLoginButton } from "./kakao-login-button";

// Where a login prompt sits: one look per place, the same message and button everywhere else.
export const LoginPromptLayout = {
  INLINE: "inline", // under a list, centered (the /chat list)
  SIDEBAR: "sidebar", // the sidebar footer for a guest
  CARD: "card", // a whole screen that needs a login (settings)
} as const;
export type LoginPromptLayout = (typeof LoginPromptLayout)[keyof typeof LoginPromptLayout];

// The sentences the screens use to ask a guest to log in
export const LOGIN_PROMPT = {
  ANY_DEVICE: "로그인하면 어느 기기에서나 대화를 볼 수 있어요.",
  GUEST_TAB: "지금 대화는 이 탭에만 저장됩니다. 로그인하면 어느 기기에서나 대화 이력을 볼 수 있어요.",
  SETTINGS: "개인정보와 이용 설정은 카카오로 로그인한 회원만 관리할 수 있어요.",
} as const;

// Any flow that asks a guest to log in: a message and the Kakao login button (GET /auth/kakao/login).
export function LoginPrompt({ layout, message, title, notice, next }: {
  layout: LoginPromptLayout;
  message: string;
  title?: string; // CARD only
  notice?: ReactNode; // CARD only: shown above the title (e.g. why /auth/me failed)
  next?: string | null; // the page to come back to; default: this one
}) {
  if (layout === LoginPromptLayout.INLINE) {
    return (
      <div className="flex flex-col items-center gap-3 pt-2 text-center text-[13px] text-sub">
        {message}
        <KakaoLoginButton size="sm" next={next} />
      </div>
    );
  }
  if (layout === LoginPromptLayout.SIDEBAR) {
    return (
      <div className="rounded-xl bg-canvas p-3.5">
        <p className="text-[13px] leading-relaxed text-sub">{message}</p>
        <KakaoLoginButton className="mt-3" next={next} />
      </div>
    );
  }
  return (
    <Card className="mx-auto max-w-md p-8 text-center">
      {notice && <div className="mb-5 text-left">{notice}</div>}
      {title && <h2 className="text-lg font-semibold">{title}</h2>}
      <p className="mt-1.5 text-sm text-sub">{message}</p>
      <KakaoLoginButton size="lg" className="mt-6" next={next} />
    </Card>
  );
}
