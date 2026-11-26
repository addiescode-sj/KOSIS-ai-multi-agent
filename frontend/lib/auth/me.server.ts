import { backendGet } from "@/lib/backend/server-read";
import type { AuthState } from "@/types/account";

import { authFrom } from "./me";
import { Me } from "./schemas";

// GET /auth/me for a server prefetch. No login cookie: a guest, without asking the backend. With one, a backend that
// cannot be reached is an error (503, as lib/backend/proxy.ts answers): a member must not turn into a guest.
export async function authOnServer(cookie: string | null): Promise<AuthState> {
  if (!cookie) return { status: "guest" };
  const res = await backendGet("/auth/me", cookie, Me);
  return authFrom(!res.ok && res.status === null ? { ...res, status: 503 } : res);
}
