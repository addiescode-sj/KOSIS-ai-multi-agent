import { z } from "zod";

// Runtime shapes of the admin preview screens (T3-63): the dashboard filter, the dashboard numbers and one trace.
// The numbers are sample data from app/api/preview/admin; when a real metrics API exists it answers in this shape.

export const Period = z.enum(["today", "7d", "30d"]);
export type Period = z.infer<typeof Period>;
export const UserKind = z.enum(["all", "member", "guest"]);
export type UserKind = z.infer<typeof UserKind>;

export const AdminFilter = z.strictObject({ period: Period, user: UserKind, internal: z.boolean() });
export type AdminFilter = z.infer<typeof AdminFilter>;

const Count = z.number().int().nonnegative();

// One headline number: value in its unit, change since the previous period, and whether that change is good
export const Stat = z.strictObject({
  key: z.enum(["questions", "refusal_rate", "accuracy", "thumbs_up_rate"]),
  label: z.string(),
  value: z.number(),
  unit: z.enum(["건", "%"]),
  delta: z.number(),
  deltaUnit: z.enum(["건", "%p"]),
  good: z.boolean(),
  note: z.string(),
});
export type Stat = z.infer<typeof Stat>;

const Labeled = z.strictObject({ label: z.string(), count: Count });
export type Labeled = z.infer<typeof Labeled>;

export const AdminDashboard = z.strictObject({
  filter: AdminFilter,
  batch: z.strictObject({ ok: z.boolean(), updatedAt: z.string() }), // HH:MM of the last hourly batch
  stats: z.array(Stat),
  answerTypes: z.array(Labeled),
  refusals: z.array(z.strictObject({ code: z.string(), label: z.string(), count: Count })),
  upReasons: z.array(Labeled),
  downReasons: z.array(Labeled),
  daily: z.array(z.strictObject({ day: z.string(), count: Count })), // day: YYYY-MM-DD
  hourly: z.array(z.array(z.number().int().min(0).max(5))), // 7 weekdays (Mon first) × 8 three-hour slots, levels 0-5
  rates: z.strictObject({
    citationClick: z.number(), // % of number answers whose source link was clicked
    turnsPerChat: z.number(),
    turnsPerChatPrev: z.number(), // the week before
    dropBeforeAnswer: z.number(), // % of questions left before the answer
  }),
  tables: z.array(z.strictObject({ name: z.string(), survey: z.string(), uses: Count, lastUsed: z.string() })),
  latency: z.array(z.strictObject({ stage: z.string(), p50: Count, p95: Count })), // ms
  ttft: z.strictObject({ seconds: z.array(z.number()), p50: Count, p95: Count }), // series in s, percentiles in ms
  llm: z.strictObject({ calls: z.number(), inputTokens: Count, outputTokens: Count, costUsd: z.number() }),
  logQuality: z.array(z.strictObject({ label: z.string(), value: z.number(), unit: z.enum(["건", "%"]), ok: z.boolean() })),
});
export type AdminDashboard = z.infer<typeof AdminDashboard>;

// One request's spans, start and end as % of the whole request
export const AdminTrace = z.strictObject({
  requestId: z.string(),
  spans: z.array(z.strictObject({ name: z.string(), start: z.number().min(0).max(100), end: z.number().min(0).max(100) })),
});
export type AdminTrace = z.infer<typeof AdminTrace>;
