// Pass a browser request on to FastAPI and its answer back unchanged (T3-54): login redirects and Set-Cookie must reach
// the browser as they are, and nothing of a signed-in user may be cached. Route handlers only (not next.config rewrites:
// their destination is fixed at build time, BACKEND_URL is set at run time).

const FORWARDED = ["cookie", "origin", "x-session-id", "content-type"];

/** The caller's identity headers, for a request made on its behalf. */
export function callerHeaders(request: Request): Headers {
  const headers = new Headers();
  for (const name of FORWARDED) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  return headers;
}

/** "/auth" + ["kakao", "login"] -> "/auth/kakao/login"; null when a segment could change the path. */
export function backendPath(prefix: string, segments: string[] = []): string | null {
  if (segments.some((s) => s === "" || s === "." || s === "..")) return null;
  return [prefix, ...segments.map(encodeURIComponent)].join("/");
}

export async function proxy(request: Request, path: string | null): Promise<Response> {
  if (path === null) return new Response(null, { status: 404 });
  // Read per request: a module-level constant would be fixed at build time in the standalone image
  const backend = process.env.BACKEND_URL ?? "http://localhost:8000";
  const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer();
  let res: Response;
  try {
    res = await fetch(`${backend}${path}${new URL(request.url).search}`, {
      method: request.method,
      headers: callerHeaders(request),
      body: body?.byteLength ? body : undefined,
      redirect: "manual", // Kakao's login page is for the browser, not for this server
      cache: "no-store",
      signal: request.signal,
    });
  } catch {
    return Response.json({ error: { code: "unavailable", message: "잠시 뒤 다시 시도해 주세요." } }, { status: 503 });
  }
  const headers = new Headers({ "cache-control": "no-store" });
  for (const name of ["content-type", "location", "referrer-policy"]) {
    const value = res.headers.get(name);
    if (value) headers.set(name, value);
  }
  for (const cookie of res.headers.getSetCookie()) headers.append("set-cookie", cookie);
  return new Response(res.status === 204 ? null : res.body, { status: res.status, headers });
}
