import type { z } from "zod";

import { errorResponse, issuesText } from "./errors.ts";
import { callerHeaders } from "./proxy.ts";
import { request } from "./request.ts";

// Route handlers that read what they pass on (JSON endpoints): the browser's body is checked before the backend is
// asked (422 invalid_request), and the backend's answer after (502 bad_backend_response). Login redirects and
// Set-Cookie stay with lib/backend/proxy.ts, which passes the answer through untouched.

/** The request body checked with `schema`: the data, or the 422 answer to return. */
export async function readBody<T>(req: Request, schema: z.ZodType<T>): Promise<{ ok: true; data: T } | { ok: false; response: Response }> {
  const text = await req.text().catch(() => "");
  let json: unknown;
  try {
    json = text ? JSON.parse(text) : undefined;
  } catch {
    return { ok: false, response: errorResponse(422, "invalid_request", "요청 본문이 JSON이 아닙니다.") };
  }
  const parsed = schema.safeParse(json);
  return parsed.success
    ? { ok: true, data: parsed.data }
    : { ok: false, response: errorResponse(422, "invalid_request", issuesText(parsed.error)) };
}

type ForwardOptions<T> = { responseSchema: z.ZodType<T>; bodySchema?: z.ZodType };

/** Ask the backend `path` as the caller (cookie, X-Session-Id) and answer with its checked JSON. */
export async function forward<T>(req: Request, path: string | null, opts: ForwardOptions<T>): Promise<Response> {
  if (path === null) return errorResponse(404, "not_found", "없는 주소입니다.");
  let body: { body: unknown; bodySchema: z.ZodType } | Record<string, never> = {};
  if (opts.bodySchema) {
    const read = await readBody(req, opts.bodySchema);
    if (!read.ok) return read.response;
    body = { body: read.data, bodySchema: opts.bodySchema };
  }
  // Read per request: a module-level constant would be fixed at build time in the standalone image
  const backend = process.env.BACKEND_URL ?? "http://localhost:8000";
  const res = await request(`${backend}${path}${new URL(req.url).search}`, {
    method: req.method, headers: callerHeaders(req), responseSchema: opts.responseSchema, signal: req.signal, ...body,
  });
  const headers = { "cache-control": "no-store" };
  if (res.ok) {
    return res.data === null || res.status === 204
      ? new Response(null, { status: res.status, headers })
      : Response.json(res.data, { status: res.status, headers });
  }
  if (res.status === null) return errorResponse(503, "unavailable", "잠시 뒤 다시 시도해 주세요.");
  if (res.code === "bad_response") return errorResponse(502, "bad_backend_response", res.message);
  return errorResponse(res.status, res.code, res.message);
}
