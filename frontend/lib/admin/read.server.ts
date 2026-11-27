import "server-only";

import type { QueryClient } from "@tanstack/react-query";

import { traceQuery, type AdminResult } from "./queries.ts";
import { sampleTrace } from "./sample.ts";
import { AdminTrace } from "./schemas.ts";

// Server read of the trace stand-in screen (sample spans): the page prefetch and the /api/preview/admin/trace handler
// answer from the same place, checked with the schema the browser uses. The metrics dashboard reads real numbers
// from the operator app in the browser instead (lib/admin/queries dashboardQuery, T3-66).

export function readTrace(id: string): AdminResult<AdminTrace> {
  const parsed = AdminTrace.safeParse(sampleTrace(id));
  return parsed.success ? { ok: true, data: parsed.data } : { ok: false, message: "추적 형식이 다릅니다." };
}

// Not awaited by the caller: the pending query is dehydrated and streams in
export function prefetchTrace(client: QueryClient, id: string): void {
  void client.prefetchQuery({ ...traceQuery(id), queryFn: async () => readTrace(id) });
}
