"use client";

import { ChartNoAxesColumn, History, MessageSquarePlus, Search, Settings } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { cn } from "cn";

import { AccountCard } from "@/components/account/account-card";
import { AccountMenu } from "@/components/account/account-menu";
import { AuthNotice } from "@/components/account/auth-notice";
import { GuestLoginCard } from "@/components/account/guest-login-card";
import { KakaoLoginButton } from "@/components/account/kakao-login-button";
import { useAuth } from "@/lib/auth/use-me";

const NAV = [
  { href: "/", label: "새 대화", icon: MessageSquarePlus, active: (p: string) => p === "/" || p.startsWith("/chat/") },
  { href: "/chat", label: "대화 목록", icon: History, active: (p: string) => p === "/chat" },
];

// Sidebar + sticky header of every customer screen (prototype Layout): a member sees the account card and menu,
// a guest the Kakao login buttons (T3-11 Change 4). Notifications and help have no screen and are left out.
export function AppShell({ title, sub, actions, query, children }: {
  title: string;
  sub?: string;
  actions?: ReactNode;
  query?: string; // the search box value on the search page
  children: ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const auth = useAuth();
  const member = auth.status === "member";
  return (
    <div className="flex min-h-dvh">
      <aside className="sticky top-0 hidden h-dvh w-64 shrink-0 flex-col border-r border-line bg-white md:flex">
        {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- full load on purpose, see NAV below */}
        <a href="/" className="flex h-[72px] items-center gap-2.5 border-b border-line px-6">
          <span className="flex size-8 items-center justify-center rounded-full bg-brand-600 text-white">
            <ChartNoAxesColumn size={17} strokeWidth={2.5} />
          </span>
          <span className="text-[17px] font-bold tracking-tight">KOSIS 통계 AI</span>
        </a>
        <nav className="flex-1 space-y-1 p-4">
          {NAV.map((n) => {
            // A plain link for "새 대화": the `/` page swaps its URL to /chat/<id> in place, so a client-side
            // navigation to `/` could land on the same, already-filled page.
            const Item = n.href === "/" ? "a" : Link;
            return (
            <Item
              key={n.href}
              href={n.href}
              className={cn(
                "flex h-10 items-center gap-3 rounded-lg px-3 text-[14px] transition-colors",
                n.active(pathname) ? "bg-brand-50 font-semibold text-brand-700" : "text-sub hover:bg-gray-50 hover:text-ink",
              )}
            >
              <n.icon size={18} />
              {n.label}
            </Item>
            );
          })}
          <p className="px-3 pt-6 pb-2 text-[11px] font-semibold tracking-wider text-mute uppercase">기타</p>
          <Link
            href={member ? "/settings" : "/login"}
            className={cn(
              "flex h-10 items-center gap-3 rounded-lg px-3 text-[14px] transition-colors",
              pathname === "/settings" ? "bg-brand-50 font-semibold text-brand-700" : "text-sub hover:bg-gray-50 hover:text-ink",
            )}
          >
            <Settings size={18} />설정
          </Link>
        </nav>
        <div className="border-t border-line p-4">
          {auth.status === "member" ? <AccountCard /> : auth.status === "error" ? <AuthNotice message={auth.message} /> : auth.status === "guest" ? <GuestLoginCard /> : null}
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-[72px] items-center gap-4 border-b border-line bg-white/90 px-4 backdrop-blur md:px-8">
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-xl font-semibold tracking-tight">{title}</h1>
            {sub && <p className="truncate text-xs text-mute">{sub}</p>}
          </div>
          {actions}
          <form
            role="search"
            className="hidden h-10 w-72 items-center gap-2 rounded-lg border border-line px-3 text-sm text-mute focus-within:border-brand-500 lg:flex"
            onSubmit={(e) => {
              e.preventDefault();
              const q = new FormData(e.currentTarget).get("q")?.toString().trim();
              if (q) router.push(`/search?q=${encodeURIComponent(q)}`);
            }}
          >
            <Search size={16} />
            <input
              name="q"
              key={query}
              defaultValue={query}
              placeholder="대화·통계표 검색"
              aria-label="대화·통계표 검색"
              className="w-full bg-transparent text-ink outline-none placeholder:text-mute"
            />
          </form>
          {member ? <AccountMenu /> : auth.status === "guest" && <KakaoLoginButton size="sm">로그인</KakaoLoginButton>}
        </header>
        <main className="flex-1 p-4 md:p-8">{children}</main>
      </div>
    </div>
  );
}
