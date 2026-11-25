"use client";

import { ChevronRight, LogOut } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Avatar } from "./avatar";
import { LogoutDialog } from "./logout-dialog";
import { useMember } from "./use-member";

// Sidebar footer for a member: name → settings, and logout
export function AccountCard() {
  const member = useMember();
  const [logout, setLogout] = useState(false);
  if (!member) return null;
  return (
    <div className="rounded-xl bg-canvas p-3">
      <Link href="/settings" title="개인정보 수정" className="group flex items-center gap-3 rounded-lg p-1 hover:bg-white">
        <Avatar name={member.name} color={member.color} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium group-hover:text-brand-700">{member.name}</p>
          <p className="text-xs text-mute">개인정보 수정</p>
        </div>
        <ChevronRight size={16} className="text-mute group-hover:text-brand-600" />
      </Link>
      <button
        type="button"
        onClick={() => setLogout(true)}
        className="mt-2.5 flex h-8 w-full cursor-pointer items-center justify-center gap-1.5 rounded-lg border border-line bg-white text-[13px] font-medium text-ink hover:bg-gray-50"
      >
        <LogOut size={14} />로그아웃
      </button>
      <LogoutDialog open={logout} onOpenChange={setLogout} />
    </div>
  );
}
