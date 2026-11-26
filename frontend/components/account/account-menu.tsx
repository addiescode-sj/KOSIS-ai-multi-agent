"use client";

import { ChevronDown, History, LogOut, UserCog } from "lucide-react";
import Link from "next/link";
import { DropdownMenu } from "radix-ui";
import { useState } from "react";

import { Avatar } from "./avatar";
import { LogoutDialog } from "./logout-dialog";
import { useMember } from "./use-member";

const ITEM = "flex h-10 cursor-pointer items-center gap-2.5 rounded-lg px-3 text-sm text-ink outline-none data-[highlighted]:bg-gray-50";

// The header's account button for a member (Radix DropdownMenu: keyboard, focus and outside click)
export function AccountMenu() {
  const member = useMember();
  const [logout, setLogout] = useState(false);
  if (!member) return null;
  return (
    <>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger aria-label="계정 메뉴" className="flex cursor-pointer items-center gap-1.5 rounded-full p-0.5 pr-1.5 outline-none hover:bg-gray-100 focus-visible:ring-4 focus-visible:ring-brand-100">
          <Avatar name={member.name} color={member.color} />
          <ChevronDown size={15} className="text-mute" />
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content align="end" sideOffset={8} className="z-30 w-64 rounded-xl border border-line bg-white p-1.5 shadow-[0_12px_40px_rgba(17,24,39,0.12)]">
            <DropdownMenu.Item asChild className="flex cursor-pointer items-center gap-3 rounded-lg px-3 py-3 outline-none data-[highlighted]:bg-gray-50">
              <Link href="/settings">
                <Avatar name={member.name} color={member.color} />
                <div className="min-w-0">
                  <p className="text-sm font-semibold">{member.name}</p>
                  <p className="truncate text-xs text-mute">카카오 계정{member.me.email ? ` · ${member.me.email}` : ""}</p>
                </div>
              </Link>
            </DropdownMenu.Item>
            <DropdownMenu.Separator className="my-1 h-px bg-line" />
            <DropdownMenu.Item asChild className={ITEM}><Link href="/settings"><UserCog size={16} />개인정보 수정</Link></DropdownMenu.Item>
            <DropdownMenu.Item asChild className={ITEM}><Link href="/chat"><History size={16} />내 대화 목록</Link></DropdownMenu.Item>
            <DropdownMenu.Item className={ITEM} onSelect={() => setLogout(true)}><LogOut size={16} />로그아웃</DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
      <LogoutDialog open={logout} onOpenChange={setLogout} />
    </>
  );
}
