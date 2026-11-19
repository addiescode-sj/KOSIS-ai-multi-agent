import { SearchOutput } from "@/lib/schemas";

// Proxy to POST /tools/search_tables (phase 2 search): the KOSIS tables for a query on the search page.
export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  const question = typeof body?.question === "string" ? body.question.trim() : "";
  if (!question) return Response.json({ error: "empty question" }, { status: 400 });
  const backend = process.env.BACKEND_URL ?? "http://localhost:8000";
  const res = await fetch(`${backend}/tools/search_tables`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question }),
    signal: request.signal,
  }).catch(() => null);
  if (!res?.ok) return Response.json({ error: `검색 서버 오류 (${res?.status ?? "연결 실패"})` }, { status: 502 });
  const parsed = SearchOutput.safeParse(await res.json());
  if (!parsed.success) return Response.json({ error: "검색 응답 형식 오류" }, { status: 502 });
  return Response.json(parsed.data);
}
