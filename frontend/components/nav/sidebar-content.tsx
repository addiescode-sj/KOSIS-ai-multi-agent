"use client";

import { ChartNoAxesColumn, History, MessageSquarePlus, Settings } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { cn } from "cn";

import { AccountCard } from "@/components/account/account-card";
import { AuthNotice } from "@/components/account/auth-notice";
import { LOGIN_PROMPT, LoginPrompt, LoginPromptLayout } from "@/components/account/login-prompt";
import { useAuth } from "@/lib/auth/use-me";

const NAV = [
  { href: "/", label: "새 대화", icon: MessageSquarePlus, active: (p: string) => p === "/" || p.startsWith("/chat/") },
  { href: "/chat", label: "대화 목록", icon: History, active: (p: string) => p === "/chat" },
];

const ITEM = "flex h-10 items-center gap-3 rounded-lg px-3 text-[14px] transition-colors";
const ON = "bg-brand-50 font-semibold text-brand-700";
const OFF = "text-sub hover:bg-gray-50 hover:text-ink";

// What the sidebar holds (prototype Layout): logo, menu, 설정, and the account card for a member or the Kakao login
// prompt for a guest. One component for both places it shows (T3-64): the desktop <aside> (768px and up) and the
// mobile drawer (below 768px), so the two never drift apart. `search` sits under the logo (the drawer passes one).
export function SidebarContent({ search }: { search?: ReactNode }) {
  const pathname = usePathname();
  const auth = useAuth();
  const member = auth.status === "member";
  return (
    <>
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- full load on purpose, see NAV below */}
      <a href="/" className="flex h-[72px] shrink-0 items-center gap-2.5 border-b border-line px-6">
        <span className="flex size-8 items-center justify-center rounded-full bg-brand-600 text-white">
          <ChartNoAxesColumn size={17} strokeWidth={2.5} />
        </span>
        <span className="text-[17px] font-bold tracking-tight">KOSIS 통계 AI</span>
      </a>
      {search && <div className="px-4 pt-4">{search}</div>}
      <nav aria-label="주 메뉴" className="flex-1 space-y-1 overflow-y-auto p-4">
        {NAV.map((n) => {
          // A plain link for "새 대화": the `/` page swaps its URL to /chat/<id> in place, so a client-side
          // navigation to `/` could land on the same, already-filled page.
          const Item = n.href === "/" ? "a" : Link;
          return (
            <Item key={n.href} href={n.href} className={cn(ITEM, n.active(pathname) ? ON : OFF)}>
              <n.icon size={18} />
              {n.label}
            </Item>
          );
        })}
        <p className="px-3 pt-6 pb-2 text-[11px] font-semibold tracking-wider text-mute uppercase">기타</p>
        <Link href={member ? "/settings" : "/login?next=%2Fsettings"} className={cn(ITEM, pathname === "/settings" ? ON : OFF)}>
          <Settings size={18} />설정
        </Link>
      </nav>
      <div className="border-t border-line p-4 pb-[calc(1rem+env(safe-area-inset-bottom))]">
        {auth.status === "member" ? <AccountCard /> : auth.status === "error" ? <AuthNotice message={auth.message} /> : <LoginPrompt layout={LoginPromptLayout.SIDEBAR} message={LOGIN_PROMPT.GUEST_TAB} />}
      </div>
    </>
  );
}
