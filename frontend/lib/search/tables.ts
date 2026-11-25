import { queryOptions } from "@tanstack/react-query";

import { queryKeys } from "../query/keys.ts";

import { request } from "../backend/request.ts";

import { SearchOutput, SearchRequest, type TableCandidate } from "./schemas.ts";

// The search page's table query, shared by the server prefetch (app/search/page.tsx) and the browser
// (useSuspenseQuery) so both use one key. An expected failure is a value, not a throw: a thrown error crossing
// from the server render to the browser loses its message in production.
export type TablesResult = { ok: true; out: SearchOutput } | { ok: false; message: string };

const FRESH_MS = 5 * 60_000;

export function tablesQuery(q: string) {
  return queryOptions({
    queryKey: queryKeys.tables(q),
    queryFn: ({ signal }) => fetchTables(q, signal),
    // A found result stays fresh for 5 minutes; a failure is retried on the next visit
    staleTime: (query) => (query.state.data?.ok ? FRESH_MS : 0),
  });
}

async function fetchTables(q: string, signal: AbortSignal): Promise<TablesResult> {
  const res = await request("/api/tables", {
    method: "POST", body: { question: q }, bodySchema: SearchRequest, responseSchema: SearchOutput, signal,
  });
  if (res.ok) return { ok: true, out: res.data };
  if (res.code === "aborted") throw new Error(res.message); // a cancelled query is not a failed search
  return { ok: false, message: res.status === null ? `통계표 검색에 실패했습니다 (${res.message})` : res.message };
}

// Picked tables first, then the other candidates without repeating a picked one.
export function mergeTables(out: SearchOutput): TableCandidate[] {
  const picked = new Set(out.picked.map((t) => t.key));
  return [...out.picked, ...out.candidates.filter((c) => !picked.has(c.key))];
}
