import { queryOptions } from "@tanstack/react-query";

import { request } from "../backend/request.ts";
import { queryKeys } from "../query/keys.ts";

import { apiQuery, kstToday } from "./filter.ts";
import { AdminMetrics, AdminTrace, type AdminFilter } from "./schemas.ts";
import { toView, type DashboardView } from "./view.ts";

// The admin screens' queries. A failure is a value, not a throw (AGENTS.md).
export type AdminResult<T> = { ok: true; data: T } | { ok: false; message: string };

// Why the dashboard has no numbers: not signed in, signed in but not an operator, or anything else
export const DashboardFailure = { LOGIN: "login", FORBIDDEN: "forbidden", ERROR: "error" } as const;
export type DashboardFailure = (typeof DashboardFailure)[keyof typeof DashboardFailure];
export type DashboardResult = { ok: true; data: DashboardView } | { ok: false; failure: DashboardFailure; message: string };

export const tracePath = (id: string) => `/api/preview/admin/trace/${encodeURIComponent(id)}`;

// GET /admin/api/metrics of the operator app, from the browser (T3-66 option B): the same origin, so the login cookie
// goes along and this app never handles it (deployed: the ingress sends /admin/* to the operator app; next dev:
// next.config.ts rewrites it). Read in the browser only — no server prefetch — and kept for a minute (the operator
// app itself caches 5 minutes). 401 and 404 are screens, not errors to retry.
export function dashboardQuery(f: AdminFilter) {
  return queryOptions({
    queryKey: queryKeys.adminDashboard(f),
    queryFn: ({ signal }) => readDashboard(f, signal),
    staleTime: 60_000,
    retry: false,
  });
}

export async function readDashboard(f: AdminFilter, signal?: AbortSignal): Promise<DashboardResult> {
  const res = await request(`/admin/api/metrics?${apiQuery(f, kstToday())}`, {
    responseSchema: AdminMetrics, signal, init: { redirect: "manual", credentials: "same-origin" },
  });
  if (res.ok) return { ok: true, data: toView(res.data) };
  if (res.code === "aborted") throw new Error(res.message); // a cancelled query is not a failed read
  if (res.status === 401) return { ok: false, failure: DashboardFailure.LOGIN, message: res.message };
  if (res.status === 404) return { ok: false, failure: DashboardFailure.FORBIDDEN, message: "운영자만 볼 수 있습니다." };
  return { ok: false, failure: DashboardFailure.ERROR, message: res.message };
}

export function traceQuery(id: string) {
  return queryOptions({
    queryKey: queryKeys.adminTrace(id),
    queryFn: async ({ signal }): Promise<AdminResult<AdminTrace>> => {
      const res = await request(tracePath(id), { responseSchema: AdminTrace, signal });
      if (res.ok) return { ok: true, data: res.data };
      if (res.code === "aborted") throw new Error(res.message);
      return { ok: false, message: res.message };
    },
    staleTime: Infinity, // a finished request's spans do not change
  });
}
