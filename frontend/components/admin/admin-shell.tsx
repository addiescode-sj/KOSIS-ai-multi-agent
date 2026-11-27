"use client";

import { Activity, ChartNoAxesColumn, ExternalLink, House, Inbox, LayoutDashboard, ListChecks, LogOut, ShieldCheck, SlidersHorizontal } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { cn } from "cn";

import { useLogout } from "@/lib/auth/use-account-actions";
import { queryKeys } from "@/lib/query/keys";

import { useOperator } from "./operator-context";

export const ADMIN_HOME = "/admin";
export const ADMIN_METRICS = "/admin/metrics";

const NAV = [
  { href: ADMIN_HOME, label: "홈", icon: House },
  { href: ADMIN_METRICS, label: "메트릭 대시보드", icon: LayoutDashboard },
  { href: "/admin/questions", label: "질문 검수", icon: ListChecks },
  { href: "/admin/feedback", label: "👎 처리함", icon: Inbox },
  { href: "/admin/filters", label: "필터 설정", icon: SlidersHorizontal },
] as const;

const current = (pathname: string, href: string) => (href === ADMIN_HOME ? pathname === href : pathname.startsWith(href));

// Dark sidebar + sticky header of every operator screen (design mockup AdminLayout, T3-65). Drawn only inside an
// operator page (AdminGate): the name and the links below come from the operator app's /admin/api/me.
export function AdminShell({ title, sub, actions, status, children }: {
  title: string;
  sub?: string;
  actions?: ReactNode;
  status?: ReactNode;
  children: ReactNode;
}) {
  const pathname = usePathname();
  const me = useOperator();
  const router = useRouter();
  const client = useQueryClient();
  const out = useLogout(() => {
    client.removeQueries({ queryKey: queryKeys.admin }); // nothing of the operator screens stays in this tab
    router.replace("/");
  });
  const leaving = out.isPending ? "로그아웃 중…" : out.data && !out.data.ok ? out.data.message : null;
  const side = "flex h-10 items-center gap-3 rounded-lg px-3 text-[14px] text-white/60 hover:bg-white/5 hover:text-white";
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
        <nav aria-label="운영자 메뉴" className="flex-1 space-y-1 overflow-y-auto p-4">
          <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-wider text-white/40">운영</p>
          {NAV.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              aria-current={current(pathname, n.href) ? "page" : undefined}
              className={cn(
                "flex h-10 w-full items-center gap-3 rounded-lg px-3 text-[14px] transition-colors",
                current(pathname, n.href) ? "bg-white/10 font-semibold text-white" : "text-white/60 hover:bg-white/5 hover:text-white",
              )}
            >
              <n.icon size={18} />
              <span className="flex-1 text-left">{n.label}</span>
            </Link>
          ))}
          <p className="px-3 pb-2 pt-6 text-[11px] font-semibold uppercase tracking-wider text-white/40">바로가기</p>
          {me.grafana && (
            <a href={me.grafana} target="_blank" rel="noreferrer" className={side}>
              <Activity size={18} />
              <span className="flex-1">Grafana 로그·추적</span>
              <ExternalLink size={13} className="opacity-50" />
            </a>
          )}
          <a href="/" target="_blank" rel="noreferrer" className={side}>
            <ExternalLink size={18} />
            고객 사이트 열기
          </a>
          <a href="/admin/classic" className={side} title="T3-57 화면. QA가 끝나면 지웁니다">
            <ExternalLink size={18} />
            이전 운영 화면
          </a>
        </nav>
        <div className="border-t border-white/10 p-4">
          <div className="flex items-center gap-3 rounded-xl bg-white/5 p-3">
            <span className="flex size-9 items-center justify-center rounded-full bg-brand-600 text-sm font-semibold">
              <ShieldCheck size={16} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{me.name ?? "운영자"}</p>
              <p className="truncate text-xs text-white/50">{leaving ?? "카카오 로그인 · 허용 목록"}</p>
            </div>
            <button type="button" onClick={() => out.mutate()} disabled={out.isPending} className="rounded-md p-1.5 text-white/50 hover:bg-white/10 hover:text-white" aria-label="로그아웃">
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
