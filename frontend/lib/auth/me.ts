import { request, type ApiResult } from "../backend/request.ts";
import type { AuthState } from "../../types/account.ts";

import { Me } from "./schemas.ts";

// GET /auth/me's answer as an AuthState. A login failure must never block asking (contract 6장): 401, 404 (the
// backend has no /auth yet) and an unreachable server mean guest. A server error is not turned into a guest, so a
// member's conversations do not silently go to this browser during an outage.
export function authFrom(res: ApiResult<Me>): AuthState {
  if (res.ok) return { status: "member", me: res.data };
  if (res.status === null || res.status === 401 || res.status === 404) return { status: "guest" };
  if (res.code === "bad_response") return { status: "error", message: "로그인 정보 형식이 다릅니다." };
  return { status: "error", message: `로그인 정보를 확인하지 못했습니다 (${res.status}).` };
}

export async function fetchAuth(): Promise<AuthState> {
  return authFrom(await request("/auth/me", { responseSchema: Me }));
}
