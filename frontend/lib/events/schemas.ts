import { z } from "zod";

// POST /events (api.md 5장, docs/log-schema.md 4·13.4, openapi EventsRequest / EventsResponse). The same checks the
// server makes, so an event it would reject is not sent at all. Free text is never part of a screen event.

export const LOG_VERSION = 3; // kosis_store.log_models.LOG_VERSION
export const MAX_BATCH = 100; // the server's MAX_EVENTS_PER_BATCH
const PROP_TEXT_MAX = 64;

const TraceId = z.string().regex(/^[0-9a-f]{32}$/);
const ShortId = z.string().regex(/^[A-Za-z0-9_-]{1,16}$/); // screen S-xx, feature F-xx

export const EventPropsSchemas = {
  citation_click: z.strictObject({ table_key: z.string().max(PROP_TEXT_MAX), rank: z.number().int() }),
  copy_answer: z.strictObject({}),
  stream_abort: z.strictObject({ elapsed_ms: z.number().int().nonnegative() }),
};

const common = {
  event_id: z.uuid(),
  ts: z.iso.datetime({ offset: true }), // with its zone
  session_id: z.uuid().nullable(),
  thread_id: z.string().nullable(),
  turn: z.number().int().nullable(),
  request_id: TraceId.nullable(),
  screen_id: ShortId.nullable(),
  feature_id: ShortId.nullable(),
  table_keys: z.array(z.string()),
  latency_ms: z.number().int().nonnegative().nullable(),
  is_internal: z.literal(false), // the server decides from X-Internal; the browser never claims it
  log_version: z.literal(LOG_VERSION),
};

export const UiEvent = z.discriminatedUnion("event_type", [
  z.strictObject({ ...common, event_type: z.literal("citation_click"), props: EventPropsSchemas.citation_click }),
  z.strictObject({ ...common, event_type: z.literal("copy_answer"), props: EventPropsSchemas.copy_answer }),
  z.strictObject({ ...common, event_type: z.literal("stream_abort"), props: EventPropsSchemas.stream_abort }),
]);
export type UiEvent = z.infer<typeof UiEvent>;
export type EventType = UiEvent["event_type"];
export type EventProps = { [K in EventType]: z.infer<(typeof EventPropsSchemas)[K]> };

export const EventsRequest = z.strictObject({ events: z.array(UiEvent).min(1).max(MAX_BATCH) });
export type EventsRequest = z.infer<typeof EventsRequest>;

export const EventsResponse = z.looseObject({ accepted: z.number().int().nonnegative(), rejected: z.number().int().nonnegative() });
export type EventsResponse = z.infer<typeof EventsResponse>;
