import { afterEach, expect, jest, test } from "@jest/globals";
import { z } from "zod";

import { forward, readBody } from "./forward.ts";

const Body = z.strictObject({ question: z.string().min(1) });
const Out = z.object({ ok: z.literal(true) });
const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; });

const post = (body: string) => new Request("http://front/api/x", { method: "POST", body, headers: { "content-type": "application/json" } });

test("a body its schema rejects is a 422 invalid_request, before the backend is asked", async () => {
  const fn = jest.fn(async () => new Response("{}"));
  globalThis.fetch = fn as typeof fetch;
  for (const body of ['{"question":""}', "not json", '{"question":"q","x":1}']) {
    const res = await forward(post(body), "/x", { bodySchema: Body, responseSchema: Out });
    expect(res.status).toBe(422);
    expect(await res.json()).toMatchObject({ error: { code: "invalid_request" } });
  }
  expect(fn).not.toHaveBeenCalled();
  expect((await readBody(post('{"question":"q"}'), Body)).ok).toBe(true);
});

test("the backend's answer: passed on when it fits, 502 bad_backend_response when not, its error as it is", async () => {
  globalThis.fetch = (async () => new Response('{"ok":true}', { status: 202 })) as typeof fetch;
  let res = await forward(post('{"question":"q"}'), "/x", { bodySchema: Body, responseSchema: Out });
  expect([res.status, await res.json()]).toEqual([202, { ok: true }]);
  globalThis.fetch = (async () => new Response('{"ok":"maybe"}')) as typeof fetch;
  res = await forward(post('{"question":"q"}'), "/x", { bodySchema: Body, responseSchema: Out });
  expect(res.status).toBe(502);
  expect(await res.json()).toMatchObject({ error: { code: "bad_backend_response" } });
  globalThis.fetch = (async () => new Response('{"error":{"code":"rate_limited","message":"m"}}', { status: 429 })) as typeof fetch;
  res = await forward(post('{"question":"q"}'), "/x", { bodySchema: Body, responseSchema: Out });
  expect([res.status, await res.json()]).toEqual([429, { error: { code: "rate_limited", message: "m" } }]);
  globalThis.fetch = (async () => { throw new TypeError("down"); }) as typeof fetch;
  res = await forward(post('{"question":"q"}'), "/x", { bodySchema: Body, responseSchema: Out });
  expect(res.status).toBe(503);
});
