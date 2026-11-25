import { expect, test } from "@jest/globals";

import { buildEvent, createEventQueue, eventsBody, LOG_VERSION, MAX_BATCH, type Sender, type UiEvent } from "./events.ts";
import { EventsRequest, EventsResponse, UiEvent as UiEventSchema } from "./schemas.ts";

const RID = "4bf92f3577b34da6a3ce929d0e0e4736";
const NOW = new Date("2026-11-25T01:02:03.004Z");
const ID = "00000000-0000-4000-8000-000000000001";

test("an event carries every common field of api.md 5장, never claims internal traffic", () => {
  const e = buildEvent("citation_click", {
    request_id: RID, thread_id: "t1", turn: 0, feature_id: "F-XDXRJB",
    table_keys: ["101_DT_1B81A12"], props: { table_key: "101_DT_1B81A12", rank: 1 },
  }, { now: NOW, id: ID, sessionId: null });
  expect(e).toEqual({
    event_id: ID, event_type: "citation_click", ts: "2026-11-25T01:02:03.004Z",
    session_id: null, thread_id: "t1", turn: 0, request_id: RID, screen_id: null, feature_id: "F-XDXRJB",
    table_keys: ["101_DT_1B81A12"], latency_ms: null, is_internal: false,
    props: { table_key: "101_DT_1B81A12", rank: 1 }, log_version: LOG_VERSION,
  });
});

test("a request id that is not a trace id is left out; an event its schema rejects is null", () => {
  const e = buildEvent("copy_answer", { request_id: "not-a-trace" }, { sessionId: null });
  expect(e?.request_id).toBeNull();
  expect(e?.event_id).toMatch(/^[0-9a-f-]{36}$/);
  expect(buildEvent("copy_answer", { screen_id: "a screen id that is far too long" }, { sessionId: null })).toBeNull();
  expect(buildEvent("stream_abort", { props: { elapsed_ms: -1 } }, { sessionId: null })).toBeNull();
});

const valid = (over: Record<string, unknown> = {}) => ({
  event_id: ID, event_type: "stream_abort", ts: "2026-11-25T01:02:03Z", session_id: null, thread_id: null, turn: null,
  request_id: null, screen_id: null, feature_id: null, table_keys: [], latency_ms: null, is_internal: false,
  props: { elapsed_ms: 1200 }, log_version: LOG_VERSION, ...over,
});

test("UiEvent schema: the server's checks (type, props of that type, ids, zone, version, no free text)", () => {
  expect(UiEventSchema.safeParse(valid()).success).toBe(true);
  for (const over of [
    { event_type: "feedback" }, // feedback goes to POST /feedback only
    { props: { table_key: "x", rank: 1 } }, // props of another type
    { ts: "2026-11-25T01:02:03" }, // no zone
    { event_id: "nope" },
    { request_id: "ABC" },
    { log_version: 2 },
    { is_internal: true },
    { reason: "free text" },
  ]) expect(UiEventSchema.safeParse(valid(over)).success).toBe(false);
});

test("EventsRequest: 1..100 events in {events: [...]}; EventsResponse: accepted / rejected", () => {
  expect(EventsRequest.safeParse({ events: [valid()] }).success).toBe(true);
  expect(EventsRequest.safeParse({ events: [] }).success).toBe(false);
  expect(EventsRequest.safeParse([valid()]).success).toBe(false); // the old bare-list body
  expect(EventsRequest.safeParse({ events: Array.from({ length: MAX_BATCH + 1 }, valid) }).success).toBe(false);
  expect(EventsResponse.safeParse({ accepted: 1, rejected: 0 }).success).toBe(true);
  expect(EventsResponse.safeParse({ accepted: "1" }).success).toBe(false);
  expect(JSON.parse(eventsBody([valid() as UiEvent])!).events).toHaveLength(1);
  expect(eventsBody([])).toBeNull();
});

function fakeSender(ok: boolean | null = true) {
  const posts: UiEvent[][] = [];
  const beacons: string[] = [];
  const sender: Sender = {
    post: async (events) => { posts.push(events); if (ok === null) throw new Error("offline"); return ok; },
    beacon: (body) => { beacons.push(body); return ok === true; },
  };
  return { sender, posts, beacons };
}

function manualTimer() {
  let pending: (() => void) | null = null;
  return {
    schedule: (fn: () => void) => { pending = fn; return 1; },
    cancel: () => { pending = null; },
    fire: () => { const fn = pending; pending = null; fn?.(); },
  };
}

const ev = () => buildEvent("copy_answer", {}, { sessionId: null });
const tick = async () => { for (let i = 0; i < 3; i++) await Promise.resolve(); };

test("events wait for a quiet moment, then go as one batch", async () => {
  const { sender, posts } = fakeSender();
  const timer = manualTimer();
  const q = createEventQueue(sender, timer);
  q.queue(ev());
  q.queue(ev());
  expect(posts).toHaveLength(0);
  timer.fire();
  expect(posts).toHaveLength(1);
  expect(posts[0]).toHaveLength(2);
  await tick();
  expect(q.stats()).toEqual({ sent: 2, dropped: 0, pending: 0 });
});

test("a full batch goes at once; leaving the page sends the checked body by beacon", () => {
  const { sender, posts, beacons } = fakeSender();
  const q = createEventQueue(sender, manualTimer());
  for (let i = 0; i < MAX_BATCH; i++) q.queue(ev());
  expect(posts).toHaveLength(1);
  q.queue(ev());
  q.flush(true);
  expect(EventsRequest.safeParse(JSON.parse(beacons[0])).success).toBe(true);
  expect(q.stats().sent).toBe(1);
});

test("a failed send and a rejected event are dropped and counted, never thrown", async () => {
  for (const ok of [false, null]) {
    const { sender } = fakeSender(ok);
    const timer = manualTimer();
    const q = createEventQueue(sender, timer);
    q.queue(ev());
    expect(() => timer.fire()).not.toThrow();
    await tick();
    expect(q.stats()).toEqual({ sent: 0, dropped: 1, pending: 0 });
  }
  const q = createEventQueue(fakeSender(false).sender, manualTimer());
  q.queue(ev());
  q.queue(null);
  q.flush(true);
  expect(q.stats().dropped).toBe(2);
});
