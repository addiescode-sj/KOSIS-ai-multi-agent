import "server-only";

import { cacheLife } from "next/cache";

import { SearchOutput } from "./schemas.ts";
import type { TablesResult } from "./tables.ts";

// POST /tools/search_tables (phase 2 search) from the server: the search page prefetch and the /api/tables proxy.
// Cached per question for a few minutes, so a repeat search does not run the reranker LLM again.
// A failure throws inside the cache (never cached) and comes out here as a value.
export async function searchTables(question: string): Promise<TablesResult> {
  try {
    return { ok: true, out: await cachedSearch(question) };
  } catch (e) {
    return { ok: false, message: e instanceof Error && e.message ? e.message : "통계표 검색에 실패했습니다." };
  }
}

async function cachedSearch(question: string): Promise<SearchOutput> {
  "use cache";
  cacheLife("minutes");
  // Read per call: a module-level constant would be fixed at build time in the standalone image
  const backend = process.env.BACKEND_URL ?? "http://localhost:8000";
  const res = await fetch(`${backend}/tools/search_tables`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question }),
  }).catch(() => null);
  if (!res?.ok) throw new Error(`검색 서버 오류 (${res?.status ?? "연결 실패"})`);
  const parsed = SearchOutput.safeParse(await res.json());
  if (!parsed.success) throw new Error("검색 응답 형식 오류");
  return parsed.data;
}
