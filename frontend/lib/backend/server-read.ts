import { cookies } from "next/headers";
import { connection } from "next/server";

// Reads made by a server component on the caller's behalf (prefetch for streaming SSR). Server only.

// backend/app/auth.py cookie_name(): "__Host-" when the site is served over https
const LOGIN_COOKIES = ["kosis_session", "__Host-kosis_session"];

/** The caller's Cookie header when it carries a login, else null (a guest: nothing to ask the backend). */
export async function loginCookie(): Promise<string | null> {
  // The callers prefetch after this: QueryClient and dehydrate() read the current time, at request time only
  await connection();
  const jar = await cookies();
  return LOGIN_COOKIES.some((name) => jar.has(name)) ? jar.toString() : null;
}

/** GET a backend path as the caller; null when the backend cannot be reached in time. */
export async function backendGet(path: string, cookie: string): Promise<{ status: number; body: unknown } | null> {
  const backend = process.env.BACKEND_URL ?? "http://localhost:8000"; // read per request, see lib/backend/proxy.ts
  try {
    const res = await fetch(`${backend}${path}`, { headers: { cookie }, cache: "no-store", signal: AbortSignal.timeout(5000) });
    return { status: res.status, body: await res.json().catch(() => null) };
  } catch {
    return null;
  }
}
