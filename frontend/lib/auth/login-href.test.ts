import { expect, test } from "@jest/globals";

import { loginHref } from "./login-href.ts";

test("login comes back to the list and the settings, else to the start page", () => {
  expect(loginHref("/settings")).toBe("/auth/kakao/login?next=%2Fsettings");
  for (const page of ["/", "/login", "/chat/guest-conversation", "https://evil.example", null]) {
    expect(loginHref(page)).toBe("/auth/kakao/login");
  }
});

test("login comes back to an operator screen with its filter, never to the operator app's API (T3-67)", () => {
  expect(loginHref("/admin")).toBe("/auth/kakao/login?next=%2Fadmin");
  expect(loginHref("/admin/feedback?status=all&page_no=2")).toBe("/auth/kakao/login?next=%2Fadmin%2Ffeedback%3Fstatus%3Dall%26page_no%3D2");
  for (const page of ["/admin/api/me", "/admin/api", "/admin//evil.example", "/admin/x?a=<b>", "/administrator"]) {
    expect(loginHref(page)).toBe("/auth/kakao/login");
  }
});
