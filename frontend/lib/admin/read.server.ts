import "server-only";

import type { QueryClient } from "@tanstack/react-query";

import { dashboardQuery, traceQuery, type AdminResult } from "./queries.ts";
import { sampleDashboard, sampleTrace } from "./sample.ts";
import { AdminDashboard, AdminTrace, type AdminFilter } from "./schemas.ts";

// Server reads of the admin preview: the page prefetch and the /api/preview/admin handlers answer from the same
// place, checked with the same schema the browser uses. Swap the sample for the real metrics read here.

export function readDashboard(f: AdminFilter): AdminResult<AdminDashboard> {
  const parsed = AdminDashboard.safeParse(sampleDashboard(f));
  return parsed.success ? { ok: true, data: parsed.data } : { ok: false, message: "지표 형식이 다릅니다." };
}

export function readTrace(id: string): AdminResult<AdminTrace> {
  const parsed = AdminTrace.safeParse(sampleTrace(id));
  return parsed.success ? { ok: true, data: parsed.data } : { ok: false, message: "추적 형식이 다릅니다." };
}

// Not awaited by the caller: the pending query is dehydrated and streams in
export function prefetchDashboard(client: QueryClient, f: AdminFilter): void {
  void client.prefetchQuery({ ...dashboardQuery(f), queryFn: async () => readDashboard(f) });
}

export function prefetchTrace(client: QueryClient, id: string): void {
  void client.prefetchQuery({ ...traceQuery(id), queryFn: async () => readTrace(id) });
}
