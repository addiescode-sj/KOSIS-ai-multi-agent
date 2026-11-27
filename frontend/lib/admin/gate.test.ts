import { expect, test } from "@jest/globals";

import { addressQuery, INBOX_KEYS, QUESTION_KEYS } from "./address.ts";
import { cut, kstTime, sec } from "./format.ts";
import { ADMIN_HEADERS, gateFor, loginLocation } from "./gate.ts";

test("only an operator gets a screen; anything the operator app cannot confirm shows nothing (T3-67)", () => {
  expect(gateFor(200)).toEqual({ kind: "pass" });
  expect(gateFor(401)).toEqual({ kind: "login" });
  expect(gateFor(404)).toEqual({ kind: "block", status: 404 });
  for (const status of [503, 500, 302, 403, null]) expect(gateFor(status)).toEqual({ kind: "block", status: 503 });
  expect(loginLocation("/admin/feedback?status=all")).toBe("/auth/kakao/login?next=%2Fadmin%2Ffeedback%3Fstatus%3Dall");
  expect(ADMIN_HEADERS["cache-control"]).toBe("no-store");
});

test("a list's address goes on with its known keys only, one value each, in a fixed order", () => {
  expect(addressQuery({ page_no: "2", outcome: "error", evil: "1", start: "" }, QUESTION_KEYS)).toBe("outcome=error&page_no=2");
  expect(addressQuery({ status: ["all", "new"], assignee: "me" }, INBOX_KEYS)).toBe("status=all&assignee=me");
  expect(addressQuery({}, INBOX_KEYS)).toBe("");
});

test("times in Korea, seconds and cut texts", () => {
  expect(kstTime("2026-11-26T15:30:00Z")).toContain("00:30"); // 00:30 the next day in Korea
  expect(kstTime(null)).toBe("–");
  expect(sec(1234)).toBe("1.2초");
  expect(sec(null)).toBe("–");
  expect(cut("가나다라", 2)).toBe("가나…");
  expect(cut(null, 2)).toBe("(보관 기간 지남)");
});
