import { NoContent, request, type ApiResult } from "../backend/request.ts";

// Ending a login (POST /auth/logout) and the account (DELETE /auth/me), as values: the dialogs show the message of a
// failure and move on only when the server says it is done (api.md 6장). Both answer 204 with no body.

export type ActionResult = { ok: true } | { ok: false; message: string };

/** POST /auth/logout: 204 even when the session was already gone; 401 means there was nothing to end. */
export function logoutFrom(res: ApiResult<null>): ActionResult {
  if (res.ok || res.status === 401) return { ok: true };
  return { ok: false, message: `로그아웃하지 못했습니다${res.status ? ` (${res.status})` : ""}. 잠시 뒤 다시 시도해 주세요.` };
}

/** DELETE /auth/me: done only on a 2xx with no body; otherwise the account and its conversations are still there. */
export function withdrawFrom(res: ApiResult<null>): ActionResult {
  if (res.ok) return { ok: true };
  return { ok: false, message: `탈퇴를 처리하지 못했습니다${res.status ? ` (${res.status})` : ""}. 계정과 대화는 그대로 있습니다.` };
}

export async function logout(): Promise<ActionResult> {
  return logoutFrom(await request("/auth/logout", { method: "POST", responseSchema: NoContent }));
}

export async function withdraw(): Promise<ActionResult> {
  return withdrawFrom(await request("/auth/me", { method: "DELETE", responseSchema: NoContent }));
}
