import { afterEach, expect, jest, test } from "@jest/globals";
import { z } from "zod";

import { errorFrom } from "./errors.ts";
import { NoContent, request, resultFrom } from "./request.ts";

const Thing = z.object({ id: z.string() });
const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; });

function mockFetch(status: number, text: string) {
  const fn = jest.fn<(url: string | URL | Request, init?: RequestInit) => Promise<Response>>(
    async () => new Response(status === 204 ? null : text, { status }),
  );
  globalThis.fetch = fn as typeof fetch;
  return fn;
}

test("a valid answer is {ok: true, data}", async () => {
  mockFetch(200, '{"id":"a","extra":1}');
  expect(await request("/x", { responseSchema: Thing })).toEqual({ ok: true, status: 200, data: { id: "a" } });
});

test("an answer of another shape is a value (bad_response), not a throw", async () => {
  mockFetch(200, '{"id":1}');
  expect(await request("/x", { responseSchema: Thing })).toMatchObject({ ok: false, status: 200, code: "bad_response" });
  mockFetch(200, "<html>");
  expect(await request("/x", { responseSchema: Thing })).toMatchObject({ ok: false, code: "bad_response" });
});

test("a body its schema rejects is never sent", async () => {
  const fn = mockFetch(200, '{"id":"a"}');
  const res = await request("/x", { method: "POST", body: { id: 3 }, bodySchema: Thing, responseSchema: Thing });
  expect(res).toMatchObject({ ok: false, status: null, code: "invalid_request" });
  expect(fn).not.toHaveBeenCalled();
});

test("the body sent is what its schema gave back, as JSON", async () => {
  const fn = mockFetch(200, '{"id":"a"}');
  await request("/x", { method: "POST", body: { id: "a", dropped: true }, bodySchema: Thing, responseSchema: Thing });
  const init = fn.mock.calls[0][1]!;
  expect(init.body).toBe('{"id":"a"}');
  expect(new Headers(init.headers).get("content-type")).toBe("application/json");
});

test("an error body comes back as its code and message; 204 reads as null; network is status null", async () => {
  mockFetch(404, '{"error":{"code":"not_found","message":"없는 대화입니다."}}');
  expect(await request("/x", { responseSchema: Thing })).toEqual({ ok: false, status: 404, code: "not_found", message: "없는 대화입니다." });
  mockFetch(204, "");
  expect(await request("/x", { method: "DELETE", responseSchema: NoContent })).toEqual({ ok: true, status: 204, data: null });
  globalThis.fetch = (async () => { throw new TypeError("offline"); }) as typeof fetch;
  expect(await request("/x", { responseSchema: Thing })).toMatchObject({ ok: false, status: null, code: "network" });
});

test("errorFrom: the contract body, FastAPI's 422 detail list, an old detail string, anything else", () => {
  expect(errorFrom(422, { error: { code: "invalid_feedback", message: "m" } })).toEqual({ code: "invalid_feedback", message: "m" });
  expect(errorFrom(422, { detail: [{ loc: ["body", "question"], msg: "too long", type: "string_too_long" }, { loc: ["body"], msg: "x", type: "t" }] }))
    .toEqual({ code: "invalid_request", message: "too long, x" });
  expect(errorFrom(404, { detail: "not found" })).toEqual({ code: "http_404", message: "not found" });
  expect(errorFrom(500, null)).toEqual({ code: "http_500", message: "서버 오류 (500)" });
  expect(resultFrom(503, "Service Unavailable", Thing)).toMatchObject({ ok: false, status: 503, code: "http_503" });
});
