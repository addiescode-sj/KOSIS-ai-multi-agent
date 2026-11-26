import { request } from "../backend/request.ts";
import { guestSessionId } from "../chat/session.ts";

import {
  EventsRequest, EventsResponse, LOG_VERSION, MAX_BATCH, UiEvent,
  type EventProps, type EventType,
} from "./schemas.ts";

// Screen events (api.md 5장, docs/log-schema.md 4·13.4): a few clicks the server cannot see, batched and sent to
// POST /events in the background. Never in the way of an answer: an event its schema rejects, and a failed send,
// are dropped and only counted; nothing here throws. Questions, first token and finished answers are logged by the
// server, not here.

export { LOG_VERSION, MAX_BATCH, type EventProps, type EventType, type UiEvent };

export type EventFields<T extends EventType = EventType> = {
  thread_id?: string | null;
  turn?: number | null;
  request_id?: string | null; // 32-hex trace id of the answer
  screen_id?: string | null; // S-xx / screen ids, up to 16 of [A-Za-z0-9_-]
  feature_id?: string | null; // F-xx
  table_keys?: string[];
  latency_ms?: number | null;
  props?: EventProps[T];
};

const TRACE_ID = /^[0-9a-f]{32}$/;

/** One event with every common field filled, checked with UiEvent; null when the schema rejects it. A request id
 * that is not a trace id is left out (the server would drop the whole event for it). */
export function buildEvent<T extends EventType>(
  type: T, fields: EventFields<T> = {},
  ctx: { now?: Date; id?: string; sessionId?: string | null } = {},
): UiEvent | null {
  const requestId = fields.request_id && TRACE_ID.test(fields.request_id) ? fields.request_id : null;
  const parsed = UiEvent.safeParse({
    event_id: ctx.id ?? crypto.randomUUID(),
    event_type: type,
    ts: (ctx.now ?? new Date()).toISOString(),
    session_id: ctx.sessionId === undefined ? guestSessionId() : ctx.sessionId,
    thread_id: fields.thread_id ?? null,
    turn: fields.turn ?? null,
    request_id: requestId,
    screen_id: fields.screen_id ?? null,
    feature_id: fields.feature_id ?? null,
    table_keys: fields.table_keys ?? [],
    latency_ms: fields.latency_ms ?? null,
    is_internal: false,
    props: fields.props ?? {},
    log_version: LOG_VERSION,
  });
  return parsed.success ? parsed.data : null;
}

/** The POST /events body as sent (fetch and beacon alike); null when the batch does not pass EventsRequest. */
export function eventsBody(events: UiEvent[]): string | null {
  const body = EventsRequest.safeParse({ events });
  return body.success ? JSON.stringify(body.data) : null;
}

export type Sender = {
  // a normal send (fetch keepalive); resolves true when the server took the batch
  post(events: UiEvent[]): Promise<boolean>;
  // the page is going away: navigator.sendBeacon with the checked body; false when the browser refused it
  beacon(body: string): boolean;
};

export type EventQueue = {
  queue(event: UiEvent | null): void;
  flush(leaving?: boolean): void;
  stats(): { sent: number; dropped: number; pending: number };
};

/** A batching queue: sends after `delayMs` of quiet, at `MAX_BATCH`, or at once (beacon) when the page is hidden. */
export function createEventQueue(sender: Sender, opts: { delayMs?: number; schedule?: (fn: () => void, ms: number) => unknown; cancel?: (t: unknown) => void } = {}): EventQueue {
  const delayMs = opts.delayMs ?? 2000;
  const schedule = opts.schedule ?? ((fn, ms) => setTimeout(fn, ms));
  const cancel = opts.cancel ?? ((t) => clearTimeout(t as ReturnType<typeof setTimeout>));
  let pending: UiEvent[] = [];
  let timer: unknown = null;
  let sent = 0;
  let dropped = 0;

  function flush(leaving = false) {
    if (timer !== null) { cancel(timer); timer = null; }
    while (pending.length) {
      const batch = pending.slice(0, MAX_BATCH);
      pending = pending.slice(MAX_BATCH);
      if (leaving) {
        const body = eventsBody(batch);
        let ok = false;
        try { ok = body !== null && sender.beacon(body); } catch { ok = false; }
        if (ok) sent += batch.length; else dropped += batch.length;
        continue;
      }
      void sender.post(batch)
        .catch(() => false)
        .then((ok) => { if (ok) sent += batch.length; else dropped += batch.length; });
    }
  }

  return {
    queue(event) {
      if (event === null) { dropped += 1; return; } // its schema rejected it
      pending.push(event);
      if (pending.length >= MAX_BATCH) flush();
      else if (timer === null) timer = schedule(() => { timer = null; flush(); }, delayMs);
    },
    flush,
    stats: () => ({ sent, dropped, pending: pending.length }),
  };
}

const URL_EVENTS = "/api/events";

const browserSender: Sender = {
  post: async (events) => (await request(URL_EVENTS, {
    method: "POST", body: { events }, bodySchema: EventsRequest, responseSchema: EventsResponse, init: { keepalive: true },
  })).ok,
  beacon: (body) =>
    typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function"
      ? navigator.sendBeacon(URL_EVENTS, new Blob([body], { type: "application/json" }))
      : false,
};

let shared: EventQueue | null = null;

function sharedQueue(): EventQueue {
  if (shared) return shared;
  const q = createEventQueue(browserSender);
  shared = q;
  if (typeof window !== "undefined") {
    window.addEventListener("pagehide", () => q.flush(true));
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") q.flush(true); });
  }
  return q;
}

/** Queue one screen event for the background send (null: one its schema rejected, counted as dropped). Does
 * nothing on the server; never throws. */
export function queueEvent(event: UiEvent | null): void {
  if (typeof window === "undefined") return;
  try {
    sharedQueue().queue(event);
  } catch {
    /* a log must never break the screen */
  }
}

/** How many events this tab sent and dropped (failed sends are only counted, api.md 5장). */
export function eventStats() {
  return shared ? shared.stats() : { sent: 0, dropped: 0, pending: 0 };
}
