import { afterEach, expect, test } from "@jest/globals";

import { backendPath, callerHeaders, proxy } from "./proxy";

const realFetch = global.fetch;
afterEach(() => {
  global.fetch = realFetch;
});

test("segments cannot change the backend path", () => {
  expect(backendPath("/auth", ["kakao", "login"])).toBe("/auth/kakao/login");
  expect(backendPath("/chat")).toBe("/chat");
  expect(backendPath("/chat", ["a/../b?x"])).toBe("/chat/a%2F..%2Fb%3Fx");
  expect(backendPath("/auth", ["..", "dev"])).toBeNull();
});

test("only identity headers are passed on", () => {
  const headers = callerHeaders(
    new Request("http://x/", { headers: { cookie: "a=1", "x-session-id": "s", "x-internal": "true", authorization: "z" } }),
  );
  expect([...headers.keys()].sort()).toEqual(["cookie", "x-session-id"]);
});

test("a redirect with two cookies reaches the browser unchanged and uncached", async () => {
  const headers = new Headers({ location: "/chat" });
  headers.append("set-cookie", "kosis_session=t; HttpOnly");
  headers.append("set-cookie", "kosis_login_state=; Max-Age=0");
  const calls: [string, RequestInit][] = [];
  global.fetch = (async (url: string, init: RequestInit) => {
    calls.push([url, init]);
    return new Response(null, { status: 302, headers });
  }) as typeof fetch;
  const res = await proxy(new Request("http://localhost:3000/auth/kakao/callback?code=c&state=s"), "/auth/kakao/callback");
  expect(calls[0][0]).toMatch(/\/auth\/kakao\/callback\?code=c&state=s$/);
  expect(calls[0][1].redirect).toBe("manual");
  expect(res.status).toBe(302);
  expect(res.headers.get("location")).toBe("/chat");
  expect(res.headers.getSetCookie()).toHaveLength(2);
  expect(res.headers.get("cache-control")).toBe("no-store");
});

test("a backend that is down is a 503", async () => {
  global.fetch = (async () => {
    throw new TypeError("fetch failed");
  }) as typeof fetch;
  expect((await proxy(new Request("http://x/auth/me"), "/auth/me")).status).toBe(503);
});
