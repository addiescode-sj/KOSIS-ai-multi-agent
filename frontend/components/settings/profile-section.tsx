"use client";

import { Check, CircleCheck, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "cn";

import { Avatar } from "@/components/account/avatar";
import { Button } from "@/components/ui/button";
import { accountSettings, AVATAR_COLORS, NAME_MAX, nameOf, validName } from "@/lib/account/settings";
import type { AccountSettings, AvatarColor } from "@/types/account";

import { LocalBadge } from "./local-badge";
import { Section } from "./section";

const COLOR_NAMES: Record<AvatarColor, string> = { blue: "파랑", green: "초록", amber: "노랑", rose: "분홍", slate: "회색" };

export function ProfileSection({ nickname, settings }: { nickname: string; settings: AccountSettings }) {
  const savedName = nameOf(settings, nickname);
  const [name, setName] = useState(savedName);
  const [color, setColor] = useState(settings.color);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (!saved) return;
    const t = setTimeout(() => setSaved(false), 2500);
    return () => clearTimeout(t);
  }, [saved]);

  const trimmed = name.trim();
  const dirty = trimmed !== savedName || color !== settings.color;
  const invalid = !validName(name);

  function save() {
    accountSettings.update({ displayName: trimmed === nickname ? null : trimmed, color });
    setName(trimmed);
    setSaved(true);
  }

  return (
    <Section
      id="profile"
      title="프로필"
      desc={<>서비스 안에서 보이는 이름과 프로필 색상입니다. 카카오 계정 정보에는 영향을 주지 않습니다.<LocalBadge /></>}
    >
      <div className="flex items-start gap-6">
        <Avatar name={trimmed || nickname} color={color} size="lg" />
        <div className="min-w-0 flex-1 space-y-5">
          <label className="block">
            <span className="mb-1.5 flex justify-between text-[13px] font-medium">
              <span>표시 이름</span>
              <span className="font-normal text-mute">{name.length}/{NAME_MAX}</span>
            </span>
            <input
              value={name}
              maxLength={NAME_MAX}
              onChange={(e) => setName(e.target.value)}
              aria-invalid={invalid}
              className={cn(
                "h-10 w-full rounded-lg border bg-white px-3 text-sm outline-none focus:ring-4",
                invalid ? "border-red-300 focus:ring-red-100" : "border-line focus:border-brand-500 focus:ring-brand-100",
              )}
            />
            {invalid ? (
              <span className="mt-1.5 block text-xs text-red-600">표시 이름을 입력해 주세요.</span>
            ) : (
              <button type="button" onClick={() => setName(nickname)} className="mt-1.5 inline-flex cursor-pointer items-center gap-1 text-xs text-sub hover:text-brand-600">
                <RotateCcw size={12} />카카오 닉네임({nickname})으로 되돌리기
              </button>
            )}
          </label>
          <div>
            <p className="mb-2 text-[13px] font-medium">프로필 색상</p>
            <div role="radiogroup" aria-label="프로필 색상" className="flex gap-2.5">
              {(Object.keys(AVATAR_COLORS) as AvatarColor[]).map((k) => (
                <button
                  key={k}
                  type="button"
                  role="radio"
                  aria-checked={color === k}
                  aria-label={`${COLOR_NAMES[k]} 색상`}
                  onClick={() => setColor(k)}
                  className={cn("flex size-8 cursor-pointer items-center justify-center rounded-full ring-offset-2", AVATAR_COLORS[k], color === k && "ring-2 ring-brand-600")}
                >
                  {color === k && <Check size={15} strokeWidth={3} />}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
      <div className="mt-6 flex items-center justify-end gap-3 border-t border-line pt-5">
        {saved && (
          <span role="status" className="mr-auto inline-flex items-center gap-1.5 text-[13px] text-emerald-700"><CircleCheck size={15} />저장했습니다</span>
        )}
        <Button variant="secondary" disabled={!dirty} onClick={() => { setName(savedName); setColor(settings.color); }}>취소</Button>
        <Button disabled={!dirty || invalid} onClick={save}>변경 사항 저장</Button>
      </div>
    </Section>
  );
}
