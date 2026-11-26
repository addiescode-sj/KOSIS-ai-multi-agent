import { expect, test } from "@jest/globals";

import { resultFrom } from "../backend/request.ts";
import { authFrom } from "./me.ts";
import { Me, MeUpdate } from "./schemas.ts";

const me = { user_id: "u1", nickname: "김통계", display_name: null, color: "blue", save_history: true, retention_days: 90 };
const read = (status: number, body: unknown) => authFrom(resultFrom(status, body === null ? "" : JSON.stringify(body), Me));

test("a logged-in member: user_id and nickname, email only when the server sends one", () => {
  expect(read(200, me)).toEqual({ status: "member", me });
});

test("asking never waits on login: 401, 404 (no /auth yet) and an unreachable server are a guest", () => {
  expect(read(401, { error: { code: "login_required", message: "로그인이 필요합니다." } })).toEqual({ status: "guest" });
  expect(read(404, null)).toEqual({ status: "guest" });
  expect(authFrom({ ok: false, status: null, code: "network", message: "x" })).toEqual({ status: "guest" });
});

test("a server error or a malformed body is an error, not a silent switch to guest", () => {
  expect(read(500, null)).toMatchObject({ status: "error", message: expect.stringContaining("500") });
  expect(read(200, { nickname: "id 없음" })).toEqual({ status: "error", message: "로그인 정보 형식이 다릅니다." });
});

test("Me and MeUpdate schemas follow openapi MeResponse / MeUpdate", () => {
  expect(Me.safeParse({ ...me, color: "purple" }).success).toBe(false);
  expect(Me.safeParse({ ...me, retention_days: 60 }).success).toBe(false);
  expect(MeUpdate.safeParse({ display_name: "통계왕", color: "rose" }).success).toBe(true);
  expect(MeUpdate.safeParse({ display_name: null }).success).toBe(true);
  expect(MeUpdate.safeParse({}).success).toBe(true);
  for (const bad of [{ display_name: "" }, { display_name: "가".repeat(21) }, { display_name: "a\u0007" }, { retention_days: 7 }, { nickname: "x" }]) {
    expect(MeUpdate.safeParse(bad).success).toBe(false);
  }
});
