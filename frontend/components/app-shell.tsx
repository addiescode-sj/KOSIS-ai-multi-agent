"use client";

import type { ReactNode } from "react";

import { AccountMenu } from "@/components/account/account-menu";
import { KakaoLoginButton } from "@/components/account/kakao-login-button";
import { LazyScrollProgress } from "@/components/motion/lazy";
import { MobileNav } from "@/components/nav/mobile-nav";
import { SearchForm } from "@/components/nav/search-form";
import { SidebarContent } from "@/components/nav/sidebar-content";
import { useAuth } from "@/lib/auth/use-me";

// Sidebar + sticky header of every customer screen (prototype Layout): a member sees the account card and menu,
// a guest the Kakao login buttons (T3-11 Change 4). Notifications and help have no screen and are left out.
// One breakpoint, 768px (Tailwind md, T3-62): from there up the sidebar is shown; below it the header's ☰ opens
// the same sidebar as a drawer (MobileNav).
export function AppShell({ title, sub, actions, query, children }: {
  title: string;
  sub?: string;
  actions?: ReactNode;
  query?: string; // the search box value on the search page
  children: ReactNode;
}) {
  const auth = useAuth();
  const member = auth.status === "member";
  return (
    <div className="flex min-h-dvh">
      <LazyScrollProgress />
      <aside className="sticky top-0 hidden h-dvh w-64 shrink-0 flex-col border-r border-line bg-white md:flex">
        <SidebarContent />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-[72px] items-center gap-2 border-b border-line bg-white/90 px-4 backdrop-blur md:gap-4 md:px-8">
          <MobileNav query={query} />
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-lg font-semibold tracking-tight md:text-xl">{title}</h1>
            {sub && <p className="truncate text-xs text-mute">{sub}</p>}
          </div>
          {actions}
          <SearchForm query={query} className="hidden w-72 lg:flex" />
          {member ? <AccountMenu /> : auth.status === "guest" && <KakaoLoginButton size="sm">로그인</KakaoLoginButton>}
        </header>
        <main className="flex-1 p-4 md:p-8">{children}</main>
      </div>
    </div>
  );
}
