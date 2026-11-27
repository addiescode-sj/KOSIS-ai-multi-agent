import { z } from "zod";

// Runtime shapes of the admin metrics screen (T3-66): the filter in the address, and GET /admin/api/metrics of the
// operator app (admin/app/main.py api_metrics), which is kosis_store.log_metrics as the T3-57 metrics page reads it.
// Only the metrics the screen draws are listed; extra fields pass through (looseObject), a missing one fails the check.

export const Period = z.enum(["yesterday", "7d", "30d"]);
export type Period = z.infer<typeof Period>;
export const UserKind = z.enum(["all", "member", "guest"]);
export type UserKind = z.infer<typeof UserKind>;

export const AdminFilter = z.strictObject({ period: Period, user: UserKind, internal: z.boolean() });
export type AdminFilter = z.infer<typeof AdminFilter>;

const Num = z.number();
const N = z.looseObject({ n: Num });
const Day = z.looseObject({ day: z.string(), n: Num.optional() }); // a day without questions has only `day`

const Summary = z.looseObject({
  question_count: z.array(N),
  outcome_ratio: z.array(z.looseObject({ outcome: z.string(), n: Num, share: Num })),
  thumbs_up_ratio: z.array(z.looseObject({ up: Num, down: Num, ratio: Num.nullable() })),
  latency: z.array(z.looseObject({ p50: Num.nullable(), p95: Num.nullable() })), // whole answer, finished turns
});
export type Summary = z.infer<typeof Summary>;

export const AdminMetrics = z.looseObject({
  filter: z.looseObject({ start: z.string(), end: z.string(), user: UserKind, internal: z.boolean() }),
  notice: z.string().nullable(), // the asked range was moved into the allowed one
  read_at: z.string(), // when the numbers were read (ISO, UTC); the operator app caches them 5 minutes
  labels: z.record(z.string(), z.record(z.string(), z.string())), // code → Korean name, per kind (admin/app/labels.py)
  tabs: z.looseObject({
    summary: z.looseObject({ rows: Summary, daily: z.looseObject({ question_count: z.array(Day) }) }),
    quality: z.looseObject({
      rows: z.looseObject({
        refusal_reasons: z.array(z.looseObject({ reason_code: z.string().nullable(), n: Num })),
        up_reasons: z.array(z.looseObject({ choice: z.string().nullable(), n: Num })),
        down_reasons: z.array(z.looseObject({ choice: z.string().nullable(), n: Num })),
      }),
    }),
    usage: z.looseObject({
      rows: z.looseObject({
        top_tables: z.array(z.looseObject({ picked_key: z.string(), survey_name: z.string().nullable(), n: Num })),
        turns_per_conversation: z.array(z.looseObject({ turns: z.string(), n: Num })),
        citation_click_rate: z.array(z.looseObject({ clicked: Num, with_numbers: Num, rate: Num.nullable() })),
        abort_rate: z.array(z.looseObject({ aborted: Num, questions: Num, rate: Num.nullable() })),
      }),
    }),
    cost: z.looseObject({
      rows: z.looseObject({
        llm_usage: z.array(z.looseObject({ calls: Num.nullable(), input_tokens: Num.nullable(), output_tokens: Num.nullable() })),
      }),
    }),
  }),
  first_token: z.array(z.looseObject({ p50: Num.nullable(), p95: Num.nullable() })),
  previous: Summary.nullable(), // the range of the same length before; null past the 90-day limit
});
export type AdminMetrics = z.infer<typeof AdminMetrics>;

// One request's spans, start and end as % of the whole request (the Jaeger stand-in screen, sample data)
export const AdminTrace = z.strictObject({
  requestId: z.string(),
  spans: z.array(z.strictObject({ name: z.string(), start: z.number().min(0).max(100), end: z.number().min(0).max(100) })),
});
export type AdminTrace = z.infer<typeof AdminTrace>;
