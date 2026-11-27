import { NextResponse, type NextRequest } from "next/server";

import { ADMIN_HEADERS, adminUrl, gateFor, LOGIN_COOKIES, loginLocation } from "@/lib/admin/gate";

// The /admin screens (T3-67) are for operators only, and that is settled here, before a page renders: a page streams
// behind the root <Suspense> (its 200 is sent first) and a layout does not run again on in-app navigation, so neither
// can hide a screen. This runs for full loads, in-app navigation and prefetches alike. /admin/api is the operator
// app's own (the ingress sends it there; it checks the login itself).
export async function proxy(req: NextRequest) {
  const back = req.nextUrl.pathname + req.nextUrl.search;
  if (!LOGIN_COOKIES.some((name) => req.cookies.has(name))) return withHeaders(NextResponse.redirect(new URL(loginLocation(back), req.url)));
  let status: number | null = null;
  try {
    const res = await fetch(`${adminUrl()}/admin/api/me`, {
      headers: { cookie: req.headers.get("cookie") ?? "" },
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(3000),
    });
    status = res.status;
  } catch {
    status = null; // the operator app cannot be reached: nothing can be shown
  }
  const gate = gateFor(status);
  if (gate.kind === "login") return withHeaders(NextResponse.redirect(new URL(loginLocation(back), req.url)));
  if (gate.kind === "block") return withHeaders(new NextResponse(null, { status: gate.status }));
  return withHeaders(NextResponse.next());
}

function withHeaders(res: NextResponse): NextResponse {
  for (const [k, v] of Object.entries(ADMIN_HEADERS)) res.headers.set(k, v);
  return res;
}

export const config = {
  // /admin and everything under it except the operator app's /admin/api
  matcher: ["/admin", "/admin/((?!api(?:/|$)).*)"],
};
