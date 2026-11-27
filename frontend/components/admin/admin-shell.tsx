"use client";

import { Activity, ChartNoAxesColumn, ExternalLink, LayoutDashboard, LogOut, ShieldCheck, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { cn } from "cn";


export const ADMIN_HOME = "/preview/admin";

const NAV = [
  { href: ADMIN_HOME, label: "메트릭 대시보드", icon: LayoutDashboard },
  { href: `${ADMIN_HOME}/filters`, label: "필터 설정", icon: SlidersHorizontal },
  { href: `${ADMIN_HOME}/jaeger`, label: "Jaeger 분산 추적", icon: Activity, external: true },
] as const;

// Dark sidebar + sticky header of the admin preview screens (design mockup AdminLayout, T3-65). `status` says where
// the screen's numbers come from (the dashboard: real metrics and when they were read, T3-66).
export function AdminShell({ title, sub, actions, status, children }: {
  title: string;
  sub?: string;
  actions?: ReactNode;
  status?: ReactNode;
  children: ReactNode;
}) {
  const pathname = usePathname();
  return (
    <div className="flex min-h-dvh">
      <aside className="sticky top-0 flex h-dvh w-64 shrink-0 flex-col bg-ink text-white">
        <div className="flex h-[72px] items-center gap-2.5 border-b border-white/10 px-6">
          <span className="flex size-8 items-center justify-center rounded-full bg-brand-600">
            <ChartNoAxesColumn size={17} strokeWidth={2.5} />
          </span>
          <div className="leading-tight">
            <p className="text-[15px] font-bold tracking-tight">KOSIS 통계 AI</p>
            <p className="text-[11px] font-medium text-brand-300">Admin Console</p>
          </div>
        </div>
        <nav aria-label="운영자 메뉴" className="flex-1 space-y-1 p-4">
          <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-wider text-white/40">운영</p>
          {NAV.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              aria-current={pathname === n.href ? "page" : undefined}
              className={cn(
                "flex h-10 w-full items-center gap-3 rounded-lg px-3 text-[14px] transition-colors",
                pathname === n.href ? "bg-white/10 font-semibold text-white" : "text-white/60 hover:bg-white/5 hover:text-white",
              )}
            >
              <n.icon size={18} />
              <span className="flex-1 text-left">{n.label}</span>
              {"external" in n && <ExternalLink size={13} className="opacity-50" />}
            </Link>
          ))}
          <p className="px-3 pb-2 pt-6 text-[11px] font-semibold uppercase tracking-wider text-white/40">바로가기</p>
          <a href="/" target="_blank" rel="noreferrer" className="flex h-10 items-center gap-3 rounded-lg px-3 text-[14px] text-white/60 hover:bg-white/5 hover:text-white">
            <ExternalLink size={18} />
            고객 사이트 열기
          </a>
        </nav>
        <div className="border-t border-white/10 p-4">
          <div className="flex items-center gap-3 rounded-xl bg-white/5 p-3">
            <span className="flex size-9 items-center justify-center rounded-full bg-brand-600 text-sm font-semibold">
              <ShieldCheck size={16} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">운영자</p>
              <p className="text-xs text-white/50">카카오 로그인 · 허용 목록</p>
            </div>
            <button type="button" disabled className="rounded-md p-1.5 text-white/50" aria-label="로그아웃 (미리보기에서는 쓰지 않음)">
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-[72px] items-center gap-4 border-b border-line bg-white/90 px-8 backdrop-blur">
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-xl font-semibold tracking-tight">{title}</h1>
            {sub && <p className="text-xs text-mute">{sub}</p>}
          </div>
          {actions}
          {status}
        </header>
        <main className="flex-1 p-8">{children}</main>
      </div>
    </div>
  );
}
