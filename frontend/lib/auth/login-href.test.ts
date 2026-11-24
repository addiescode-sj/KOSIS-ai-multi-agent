import { expect, test } from "@jest/globals";

import { loginHref } from "./login-href.ts";

test("login comes back to the list and the settings, else to the start page", () => {
  expect(loginHref("/settings")).toBe("/auth/kakao/login?next=%2Fsettings");
  for (const page of ["/", "/login", "/chat/guest-conversation", "https://evil.example", null]) {
    expect(loginHref(page)).toBe("/auth/kakao/login");
  }
});
