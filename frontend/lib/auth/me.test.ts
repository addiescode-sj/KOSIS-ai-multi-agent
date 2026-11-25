import { expect, test } from "@jest/globals";

import { authFrom } from "./me.ts";

test("a logged-in member: user_id and nickname, email only when the server sends one", () => {
  const me = { user_id: "u1", nickname: "김통계", display_name: null, color: "blue", save_history: true, retention_days: 90 };
  expect(authFrom({ status: 200, body: me })).toEqual({ status: "member", me });
});

test("asking never waits on login: 401, 404 (no /auth yet) and an unreachable server are a guest", () => {
  for (const res of [{ status: 401, body: { detail: "login required" } }, { status: 404, body: null }, null]) {
    expect(authFrom(res)).toEqual({ status: "guest" });
  }
});

test("a server error or a malformed body is an error, not a silent switch to guest", () => {
  expect(authFrom({ status: 500, body: null })).toMatchObject({ status: "error", message: expect.stringContaining("500") });
  expect(authFrom({ status: 200, body: { nickname: "id 없음" } }).status).toBe("error");
});
