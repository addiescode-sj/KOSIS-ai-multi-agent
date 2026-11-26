"use client";

import { ExternalLink, LogOut, UserX } from "lucide-react";
import { useState } from "react";

import { AuthNotice } from "@/components/account/auth-notice";
import { KakaoMark } from "@/components/account/kakao-mark";
import { LOGIN_PROMPT, LoginPrompt, LoginPromptLayout } from "@/components/account/login-prompt";
import { LogoutDialog } from "@/components/account/logout-dialog";
import { useMember } from "@/components/account/use-member";
import { WithdrawDialog } from "@/components/account/withdraw-dialog";
import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { notifySettings, RETENTION_DAYS } from "@/lib/account/settings";
import { useNotifySettings } from "@/lib/account/use-settings";
import { useUpdateAccount } from "@/lib/account/use-update-account";
import { useAuth } from "@/lib/auth/use-me";
import type { Me } from "@/lib/auth/schemas";

import { LocalBadge } from "./local-badge";
import { ProfileSection } from "./profile-section";
import { ReadOnlyField } from "./read-only-field";
import { Row } from "./row";
import { Section } from "./section";
import { SettingSwitch } from "./setting-switch";

const setNotify = notifySettings.update;

// `/settings` (prototype Profile): the Kakao account (nickname, email only when sent) and the settings kept on the
// account; the notification switches stay in this browser and are marked so.
export function SettingsScreen() {
  const auth = useAuth();
  const member = useMember();
  const notify = useNotifySettings();
  const update = useUpdateAccount();
  const [modal, setModal] = useState<"logout" | "withdraw" | null>(null);

  if (!member) {
    return (
      <AppShell title="개인정보 수정">
        <LoginPrompt
          layout={LoginPromptLayout.CARD}
          title="로그인이 필요합니다"
          message={LOGIN_PROMPT.SETTINGS}
          notice={auth.status === "error" && <AuthNotice message={auth.message} />}
        />
      </AppShell>
    );
  }

  const { me } = member;
  return (
    <AppShell title="개인정보 수정" sub="카카오 로그인 계정의 프로필과 이용 설정을 관리합니다">
      <div className="mx-auto max-w-3xl space-y-5">
        <ProfileSection me={me} />

        <Section id="kakao" title="연결된 계정" desc="카카오에서 받아온 정보는 이곳에서 바꿀 수 없습니다. 카카오 계정 설정에서 변경해 주세요.">
          <div className="flex items-center gap-3 rounded-xl border border-line p-4">
            <span className="flex size-10 items-center justify-center rounded-full bg-[#FEE500]"><KakaoMark /></span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">카카오</p>
              <p className="text-xs text-mute">연결됨</p>
            </div>
            <a href="https://accounts.kakao.com" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[13px] font-medium text-brand-600 hover:underline">
              카카오 계정 관리<ExternalLink size={13} />
            </a>
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <ReadOnlyField label="카카오 닉네임" value={me.nickname} />
            <ReadOnlyField label="이메일" value={me.email ?? "카카오에서 받지 않음"} missing={!me.email} />
          </div>
        </Section>

        <Section id="notifications" title="알림" desc={<>알림 발송은 서버 기능이 생기면 적용됩니다.<LocalBadge /></>}>
          <Row label="답변 완료 알림" hint="오래 걸리는 답변이 준비되면 화면 상단 알림으로 알려 드립니다.">
            <SettingSwitch label="답변 완료 알림" checked={notify.notifyDone} onChange={(v) => setNotify({ notifyDone: v })} />
          </Row>
          <Row label="서비스 소식 이메일" hint="새 통계표 추가, 기능 업데이트 소식을 이메일로 보내 드립니다." last>
            <SettingSwitch label="서비스 소식 이메일" checked={notify.notifyNotice} onChange={(v) => setNotify({ notifyNotice: v })} />
          </Row>
        </Section>

        <Section id="history" title="대화 기록" desc="계정에 저장된 대화는 마지막으로 이어 물은 날부터 최대 90일 보관됩니다.">
          <Row label="대화 기록 저장" hint={me.save_history ? "대화가 계정에 저장되어 어느 기기에서나 볼 수 있습니다." : "새 대화는 저장되지 않습니다. 기존 대화는 유지됩니다."}>
            <SettingSwitch label="대화 기록 저장" checked={me.save_history} onChange={(v) => update.mutate({ save_history: v })} />
          </Row>
          <Row label="자동 삭제" hint="설정한 기간이 지난 대화를 하루 한 번 자동으로 삭제합니다." last>
            <select
              aria-label="자동 삭제"
              value={me.retention_days}
              disabled={update.isPending}
              onChange={(e) => update.mutate({ retention_days: Number(e.target.value) as Me["retention_days"] })}
              className="h-10 rounded-lg border border-line bg-white px-3 text-sm outline-none focus:border-brand-500 focus:ring-4 focus:ring-brand-100 disabled:bg-canvas disabled:text-mute"
            >
              {RETENTION_DAYS.map((d) => <option key={d} value={d}>{d}일 후 삭제</option>)}
            </select>
          </Row>
          {update.error && <p role="alert" className="mt-3 text-[13px] text-red-600">{update.error.message}</p>}
        </Section>

        <Section id="session" title="로그인">
          <Row label="이 기기에서 로그아웃" hint="다시 로그인하면 저장된 대화를 그대로 볼 수 있습니다." last>
            <Button variant="secondary" onClick={() => setModal("logout")}><LogOut />로그아웃</Button>
          </Row>
        </Section>

        <Section id="withdraw" title="회원 탈퇴" danger desc="계정과 저장된 대화, 피드백 기록이 즉시 삭제되며 복구할 수 없습니다.">
          <Button variant="destructive-outline" onClick={() => setModal("withdraw")}><UserX />회원 탈퇴</Button>
        </Section>
      </div>
      <LogoutDialog open={modal === "logout"} onOpenChange={(o) => setModal(o ? "logout" : null)} />
      <WithdrawDialog open={modal === "withdraw"} onOpenChange={(o) => setModal(o ? "withdraw" : null)} provider="카카오 계정" />
    </AppShell>
  );
}
