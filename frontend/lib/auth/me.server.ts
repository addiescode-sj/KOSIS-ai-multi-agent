import { backendGet } from "@/lib/backend/server-read";
import type { AuthState } from "@/types/account";

import { authFrom } from "./me";

// GET /auth/me for a server prefetch. No login cookie: a guest, without asking the backend.
export async function authOnServer(cookie: string | null): Promise<AuthState> {
  return cookie ? authFrom(await backendGet("/auth/me", cookie)) : { status: "guest" };
}
