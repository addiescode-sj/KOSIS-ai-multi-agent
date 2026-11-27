import { z } from "zod";

import { TableCandidate } from "../search/schemas.ts";

// Runtime mirrors of the backend Pydantic contracts the chat screens read. Only the fields the screens use are
// listed; unknown fields pass through (looseObject) so a backend addition never breaks the page.
// Sources: ChatResult (src/kosis_agent/graph/state.py), StatAnswer and parts (vendor/phase1 kosis_ai.schemas).

export const Fact = z.looseObject({
  id: z.string(),
  value: z.number().nullish(),
  unit: z.string().nullish(),
  period: z.string().nullish(),
  raw: z.string().nullish(),
  round: z.number().int().nullish(),
});
export type Fact = z.infer<typeof Fact>;

const AnswerPart = z.discriminatedUnion("kind", [
  z.looseObject({ kind: z.literal("fact"), fact_id: z.string() }),
  z.looseObject({ kind: z.literal("text"), text: z.string() }),
]);

export const Source = z.looseObject({
  org_id: z.string(),
  tbl_id: z.string(),
  survey_nm: z.string(),
  tbl_nm: z.string(),
  url: z.string().nullish(),
});
export type Source = z.infer<typeof Source>;

export const StatAnswer = z.looseObject({
  outcome: z.enum(["answered", "refused", "clarify"]),
  result: z.looseObject({ facts: z.array(Fact).default([]) }).default({ facts: [] }),
  parts: z.array(AnswerPart).default([]),
  text: z.string().default(""),
  refusal: z
    .looseObject({ reason: z.string(), message: z.string(), available: z.string().nullish() })
    .nullish(),
  clarify_options: z.array(z.string()).default([]),
  source: Source.nullish(),
  caveats: z.array(z.looseObject({ kind: z.string(), text: z.string() })).default([]),
});
export type StatAnswer = z.infer<typeof StatAnswer>;

export const Outcome = z.enum(["answered", "refused", "clarify", "failed"]);
export type Outcome = z.infer<typeof Outcome>;

// One sub-answer of a multi-question turn (T3-28, graph AgentState.sub_results)
export const SubResult = z.looseObject({
  question: z.string(),
  table_key: z.string().nullish(),
  outcome: z.string(),
  reason: z.string().nullish(),
  text: z.string(),
});
export type SubResult = z.infer<typeof SubResult>;

// The drawn chart as data (ChatResult.chart, T3-02 / KOS-202: phase 1 ChartSpec JSON from kosis_chat/chart/module.py
// chart_data, no Vega-Lite). lib/chat/chart.ts draws it with Recharts; numbers and display strings are the backend's.
const ChartPoint = z.looseObject({
  label: z.string(),
  value: z.number().nullable(),
  display: z.string(),
  period: z.string().nullish(),
  highlight: z.boolean().default(false),
  share_pct: z.number().nullish(),
});
export const ChartSpec = z.looseObject({
  kind: z.string(),
  title: z.string(),
  unit: z.string().nullish(),
  series: z.array(z.looseObject({ name: z.string(), points: z.array(ChartPoint) })).min(1),
  orientation: z.enum(["horizontal", "vertical"]).nullish(),
  sort: z.union([z.array(z.string()), z.string()]).nullish(),
});
export type ChartSpec = z.infer<typeof ChartSpec>;

export const ChatResult = z.looseObject({
  request_id: z.string().nullish(), // = meta.trace_id; feedback and screen events are sent with it (api.md 2장)
  question: z.string(),
  route: z.string(),
  candidates: z.array(TableCandidate).default([]),
  picked: z.array(TableCandidate).default([]),
  no_answer: z.boolean(),
  no_answer_reason: z.string().nullish(),
  answer: z.string(),
  answer_origin: z.string(),
  table_key: z.string().nullish(),
  stat_answer: StatAnswer.nullish(),
  chart: z.record(z.string(), z.unknown()).nullish(), // the drawn chart as data (ChartSpec above); lib/chat/chart.ts toChartView checks it
  outcome: Outcome.nullish(), // None only on records older than T3-23
  reused_table: z.boolean().default(false),
  degraded: z.boolean().default(false),
  resolved_question: z.string().nullish(),
  chart_note: z.string().nullish(),
  sub_results: z.array(SubResult).nullish(),
});
export type ChatResult = z.infer<typeof ChatResult>;

// The first SSE event of a turn (api.md 2장 meta). expired: the guest thread sent was past its hour, so the backend
// started a new one (thread_id is the new id); shown once as "이전 대화가 만료되었습니다".
export const Meta = z.looseObject({
  thread_id: z.string(),
  trace_id: z.string().nullish(),
  expired: z.boolean().default(false),
});
export type Meta = z.infer<typeof Meta>;

// The SSE `error` event (api.md 2장 error). code: one of the backend's error codes (T3-68, backend/app/errors.py) and
// message its fixed sentence; type / detail (exception name and text) are for logs, never shown. retryable: worth one
// automatic reconnect; an input error (422) is not.
export const ErrorEvent = z.looseObject({
  code: z.string().nullish(),
  message: z.string().nullish(),
  type: z.string().nullish(),
  detail: z.string().nullish(),
  retryable: z.boolean().default(false),
  request_id: z.string().nullish(),
});
export type ErrorEvent = z.infer<typeof ErrorEvent>;

// A 👍/👎 the user gave one answer (api.md 3장): shown again on the turn (guest: this tab's storage, member: GET /chat/{id}).
export const TurnFeedback = z.object({
  value: z.enum(["up", "down"]),
  category: z.string().nullable(),
});
export type TurnFeedback = z.infer<typeof TurnFeedback>;

// The error body of a non-SSE backend endpoint (api.md 0장): one schema for the app, in lib/backend/errors
export { ErrorBody } from "../backend/errors.ts";

// POST /chat/stream request (openapi ChatRequest). The session goes as the X-Session-Id header, not in the body.
export const QUESTION_MAX = 200;
export const ChatStreamRequest = z.strictObject({
  question: z.string().trim().min(1).max(QUESTION_MAX),
  thread_id: z.string().min(1).nullable(),
  retry_of: z.string().min(1).nullable(),
  input_method: z.enum(["direct", "clarify_option"]),
});
export type ChatStreamRequest = z.infer<typeof ChatStreamRequest>;

// The other SSE events of /chat/stream (api.md 2장): a finished search node (event name = node name; the payload is
// the node's own output, of which only the seconds are read), a phase 1 run on one table, a piece of the answer.
// A node payload of another shape still shows the node (0 s): its content is not ours to check
export const NodeEvent = z.looseObject({ seconds: z.number().nonnegative().catch(0).default(0) }).catch({ seconds: 0 });
export type NodeEvent = z.infer<typeof NodeEvent>;
export const StatEvent = z.looseObject({ key: z.string(), seconds: z.number().nonnegative() });
export type StatEvent = z.infer<typeof StatEvent>;
export const TokenEvent = z.looseObject({ delta: z.string() });
export type TokenEvent = z.infer<typeof TokenEvent>;
