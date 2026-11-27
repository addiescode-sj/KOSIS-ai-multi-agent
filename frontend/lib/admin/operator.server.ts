import "server-only";

import { cookies } from "next/headers";
import { notFound } from "next/navigation";
import { cache } from "react";

import { request } from "../backend/request.ts";

import { adminUrl, LOGIN_COOKIES } from "./gate.ts";
import { Me } from "./screens.ts";

// The second check of an /admin screen (proxy.ts is the first): the page itself asks the operator app who is signed
// in, so a screen never renders for anyone else even if the proxy's matcher stops covering it. Once per request
// (React cache). Anything but an operator is a 404 here: the proxy has already sent the signed-out to the login.
export const requireOperator = cache(async (): Promise<Me> => {
  const jar = await cookies();
  if (!LOGIN_COOKIES.some((name) => jar.has(name))) notFound();
  const res = await request(`${adminUrl()}/admin/api/me`, {
    responseSchema: Me, headers: { cookie: jar.toString() }, signal: AbortSignal.timeout(3000),
  });
  if (!res.ok) notFound();
  return res.data;
});
