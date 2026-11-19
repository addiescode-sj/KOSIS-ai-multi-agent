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

export const ChatResult = z.looseObject({
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
  chart: z.record(z.string(), z.unknown()).nullish(), // Vega-Lite v5 spec, drawn as is
  outcome: Outcome.nullish(), // None only on records older than T3-23
  reused_table: z.boolean().default(false),
  degraded: z.boolean().default(false),
  resolved_question: z.string().nullish(),
  chart_note: z.string().nullish(),
  sub_results: z.array(SubResult).nullish(),
});
export type ChatResult = z.infer<typeof ChatResult>;
