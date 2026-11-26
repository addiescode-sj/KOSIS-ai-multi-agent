import { expect, test } from "@jest/globals";

import { NoContent, resultFrom } from "../backend/request.ts";
import { logoutFrom, withdrawFrom } from "./account-actions.ts";

const res = (status: number, text = "") => resultFrom(status, text, NoContent);

test("logout is done on 204 and on 401 (nothing left to end); otherwise a message", () => {
  expect(logoutFrom(res(204))).toEqual({ ok: true });
  expect(logoutFrom(res(401, '{"error":{"code":"login_required","message":"x"}}'))).toEqual({ ok: true });
  expect(logoutFrom(res(503))).toMatchObject({ ok: false, message: expect.stringContaining("503") });
  expect(logoutFrom({ ok: false, status: null, code: "network", message: "x" }).ok).toBe(false);
});

test("withdrawal is done only on a 2xx with no body", () => {
  expect(withdrawFrom(res(204))).toEqual({ ok: true });
  expect(withdrawFrom(res(401)).ok).toBe(false);
  expect(withdrawFrom(res(200, '{"surprise":true}')).ok).toBe(false); // not the contract's empty answer
  expect(withdrawFrom({ ok: false, status: null, code: "network", message: "x" })).toMatchObject({ ok: false, message: expect.stringContaining("그대로") });
});
