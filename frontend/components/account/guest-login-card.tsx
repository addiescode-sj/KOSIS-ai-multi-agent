import { KakaoLoginButton } from "./kakao-login-button";

// Sidebar footer for a guest
export function GuestLoginCard() {
  return (
    <div className="rounded-xl bg-canvas p-3.5">
      <p className="text-[13px] leading-relaxed text-sub">
        지금 대화는 이 브라우저에만 저장됩니다. 로그인하면 어느 기기에서나 대화 이력을 볼 수 있어요.
      </p>
      <KakaoLoginButton className="mt-3" />
    </div>
  );
}
