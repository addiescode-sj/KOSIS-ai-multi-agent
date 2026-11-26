import { z } from "zod";

import { errorFrom, issuesText } from "./errors.ts";

// The one typed fetch of the app. The outgoing body is checked with its zod schema before anything is sent, the
// answer with its schema after; the outcome is a value, never a throw (AGENTS.md: "없음"·"오류" are screens).

export type ApiResult<T> =
  | { ok: true; status: number; data: T }
  // status: the backend's status for an error it answered; null when nothing came back (not sent, network, aborted)
  | { ok: false; status: number | null; code: string; message: string };

// A body is sent only through its schema: what the schema gives back is what goes out
type Body = { body: unknown; bodySchema: z.ZodType } | { body?: undefined; bodySchema?: undefined };

export type RequestOptions<T> = Body & {
  method?: string;
  responseSchema: z.ZodType<T>;
  headers?: HeadersInit;
  signal?: AbortSignal;
  init?: Omit<RequestInit, "method" | "body" | "headers" | "signal">; // cache, keepalive, ...
};

// An answer with no body (204, or a 200 with nothing in it) reads as null
export const NoContent = z.null();

/** Read a received answer with its schema (exported for tests and for handlers holding a Response already). */
export function resultFrom<T>(status: number, text: string, schema: z.ZodType<T>): ApiResult<T> {
  let body: unknown = null;
  if (text.trim()) {
    try {
      body = JSON.parse(text);
    } catch {
      if (status >= 200 && status < 300) return { ok: false, status, code: "bad_response", message: "서버 응답 형식이 다릅니다 (JSON 아님)." };
      return { ok: false, status, ...errorFrom(status, text) };
    }
  }
  if (status < 200 || status >= 300) return { ok: false, status, ...errorFrom(status, body) };
  const parsed = schema.safeParse(body);
  return parsed.success
    ? { ok: true, status, data: parsed.data }
    : { ok: false, status, code: "bad_response", message: `서버 응답 형식이 다릅니다: ${issuesText(parsed.error)}` };
}

export async function request<T>(url: string, opts: RequestOptions<T>): Promise<ApiResult<T>> {
  let payload: string | undefined;
  if (opts.bodySchema) {
    const body = opts.bodySchema.safeParse(opts.body);
    if (!body.success) return { ok: false, status: null, code: "invalid_request", message: issuesText(body.error) };
    payload = JSON.stringify(body.data);
  }
  const headers = new Headers(opts.headers);
  if (payload !== undefined && !headers.has("content-type")) headers.set("content-type", "application/json");
  let res: Response;
  try {
    res = await fetch(url, { cache: "no-store", ...opts.init, method: opts.method ?? "GET", headers, body: payload, signal: opts.signal });
  } catch {
    if (opts.signal?.aborted) return { ok: false, status: null, code: "aborted", message: "요청을 취소했습니다." };
    return { ok: false, status: null, code: "network", message: "서버에 연결하지 못했습니다." };
  }
  const text = await res.text().catch(() => "");
  return resultFrom(res.status, text, opts.responseSchema);
}
