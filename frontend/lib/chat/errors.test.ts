import { expect, test } from "@jest/globals";

import { ANSWER_ERRORS, errorView, isNewVersion, SCREEN_ERRORS, supportId, UNKNOWN_ERROR } from "./errors.ts";

// The backend's seven codes (backend/app/errors.py CODES): each needs its own card on the screen
const BACKEND_CODES = ["model_unavailable", "upstream_busy", "upstream_error", "timeout", "data_error", "config_error",
                       "internal_error"];

test("every backend code and every screen-told code has a title and a sentence", () => {
  for (const code of [...BACKEND_CODES, "connection_lost", "offline", "network_error", "backend_unreachable", "bad_response"]) {
    expect(ANSWER_ERRORS[code]?.title.trim()).toBeTruthy();
    expect(ANSWER_ERRORS[code]?.text.trim()).toBeTruthy();
  }
});

test("no sentence names a server, an API, a model or an exception (PM 문서 13 D7)", () => {
  const all = [...Object.values(ANSWER_ERRORS), ...Object.values(SCREEN_ERRORS)].flatMap((v) => [v.title, v.text]);
  for (const text of all) expect(text).not.toMatch(/Error|http|API|GPU|HCX|Qwen|vLLM/);
});

test("errorView: the list first, then the server's sentence, then the generic one", () => {
  expect(errorView("timeout", "서버 문구")).toBe(ANSWER_ERRORS.timeout);
  expect(errorView("brand_new", "서버 문구")).toEqual({ title: UNKNOWN_ERROR.title, text: "서버 문구" });
  expect(errorView(null, "  ")).toBe(UNKNOWN_ERROR);
});

test("supportId: the first 8 characters of a request_id", () => {
  expect(supportId("4bf92f3577b34da6a3ce929d0e0e4736")).toBe("4bf92f35");
  expect(supportId(null)).toBeNull();
  expect(supportId("xyz")).toBeNull();
});

test("isNewVersion: a chunk that the new deploy no longer has", () => {
  const chunk = new Error("Loading chunk 123 failed.");
  chunk.name = "ChunkLoadError";
  expect(isNewVersion(chunk)).toBe(true);
  expect(isNewVersion(new TypeError("Failed to fetch dynamically imported module: /_next/static/x.js"))).toBe(true);
  expect(isNewVersion(new Error("Cannot read properties of undefined"))).toBe(false);
  expect(isNewVersion("not an error")).toBe(false);
});
