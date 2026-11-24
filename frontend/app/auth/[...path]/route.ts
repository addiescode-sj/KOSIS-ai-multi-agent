import { backendUrl, identity } from "@/lib/auth/backend.server";

// /auth/* (Kakao login, logout, me, withdrawal) passed to the backend on this origin, so the login cookie the
// Kakao callback sets lands on the address the browser uses (contract 6장; the Kakao Redirect URI is this
// address). Redirects and Set-Cookie go back as they are. A rewrite in next.config would fix BACKEND_URL at build.
async function forward(request: Request, ctx: RouteContext<"/auth/[...path]">) {
  const { path } = await ctx.params;
  const url = new URL(request.url);
  const target = `${backendUrl()}/auth/${path.map(encodeURIComponent).join("/")}${url.search}`;
  const res = await fetch(target, {
    method: request.method,
    headers: { ...identity(request), ...(request.headers.get("content-type") ? { "content-type": request.headers.get("content-type")! } : {}) },
    body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.text(),
    redirect: "manual",
    cache: "no-store",
  }).catch(() => null);
  if (!res) return Response.json({ detail: "auth server unreachable" }, { status: 502 });

  const headers = new Headers();
  for (const name of ["content-type", "location", "cache-control"]) {
    const v = res.headers.get(name);
    if (v) headers.set(name, v);
  }
  for (const c of res.headers.getSetCookie()) headers.append("set-cookie", c);
  return new Response(res.status === 204 || res.status === 304 ? null : res.body, { status: res.status, headers });
}

export { forward as GET, forward as POST, forward as DELETE };
