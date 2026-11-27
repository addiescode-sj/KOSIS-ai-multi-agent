import { z } from "zod";

// Runtime shapes of the operator app's screen API (admin/app/main.py, T3-67): who is signed in, the home cards, the
// question list and one question, the thumbs-down inbox and a saved review. Only what the screens draw is listed;
// extra fields pass through (looseObject), a missing one fails the check. Times are ISO strings (UTC).

const Time = z.string();
const Code = z.string().nullable(); // a stored code (outcome, reason, stage, choice); shown through the labels
const Num = z.number();

export const Labels = z.record(z.string(), z.record(z.string(), z.string())); // kind → code → Korean name

export const Me = z.looseObject({
  user_id: z.string(),
  name: z.string().nullable(),
  form_token: z.string(), // sent back as X-Admin-Token on a save
  grafana: z.string().nullable(),
  labels: Labels,
  statuses: z.array(z.string()),
  outcomes: z.array(z.string()),
});
export type Me = z.infer<typeof Me>;

// One question as the list shows it (log_metrics review_turns)
export const QuestionRow = z.looseObject({
  request_id: z.string(),
  created_at: Time,
  question: z.string().nullable(), // masked; null after retention or for team/eval traffic
  outcome: z.string(),
  reason_code: Code,
  failed_stage: Code,
  picked_key: Code,
  survey_name: Code,
  feedback: z.enum(["up", "down"]).nullable(),
});
export type QuestionRow = z.infer<typeof QuestionRow>;

// One thumbs-down answer with its review state (admin/app/queries.feedback_inbox)
export const InboxRow = z.looseObject({
  request_id: z.string(),
  received_at: Time,
  question: z.string().nullable(),
  outcome: z.string(),
  survey_name: Code,
  choice: Code,
  feedback_reason: z.string().nullable(),
  status: z.string(),
  assignee_id: z.string().nullable(),
  assignee: z.string().nullable(),
  note: z.string().nullable(),
  issue_key: z.string().nullable(),
  review_updated_at: Time.nullable(),
});
export type InboxRow = z.infer<typeof InboxRow>;

export const Home = z.looseObject({
  db_ok: z.boolean(),
  questions: Num,
  errors: Num,
  aborted: Num,
  p95: Num.nullable(),
  new_down: Num,
  problems: z.array(z.looseObject({ request_id: z.string(), created_at: Time, outcome: z.string(), failed_stage: Code })),
  inbox: z.array(z.looseObject({ request_id: z.string(), received_at: Time, choice: Code })),
  read_at: Time,
});
export type Home = z.infer<typeof Home>;

const Page = { notice: z.string().nullable(), page_no: Num, page_size: Num, total: Num };

export const QuestionList = z.looseObject({
  filter: z.looseObject({ start: z.string(), end: z.string(), user: z.string(), internal: z.boolean(), outcome: z.string().nullable() }),
  ...Page,
  rows: z.array(QuestionRow),
});
export type QuestionList = z.infer<typeof QuestionList>;

export const Person = z.looseObject({ id: z.string(), name: z.string().nullable() });
export type Person = z.infer<typeof Person>;

export const Review = z.looseObject({
  status: z.string(),
  assignee_id: z.string().nullable(),
  assignee: z.string().nullable(),
  note: z.string().nullable(),
  issue_key: z.string().nullable(),
  updated_at: Time, // sent back as `seen`: the save is refused when someone saved after it
  updated_by: z.string().nullable(),
});
export type Review = z.infer<typeof Review>;

export const QuestionDetail = z.looseObject({
  row: z.looseObject({
    request_id: z.string(),
    created_at: Time,
    question: z.string().nullable(),
    is_internal: z.boolean(),
    is_member: z.boolean(),
    rewrite_kind: Code,
    interpreted_by: Code,
    outcome: z.string(),
    reason_code: Code,
    failed_stage: Code,
    survey_name: Code,
    picked_key: Code,
    has_numbers: z.boolean(),
    has_chart: z.boolean(),
    latency_ms: Num.nullable(),
    first_token_ms: Num.nullable(),
    llm_calls: Num,
    input_tokens: Num,
    output_tokens: Num,
  }),
  rewrites: z.array(z.looseObject({ seq: Num, kind: z.string(), text: z.string().nullable() })),
  feedback: z.looseObject({ value: z.enum(["up", "down"]), category: Code, reason: z.string().nullable(), updated_at: Time }).nullable(),
  review: Review.nullable(),
  conversation: z.array(z.looseObject({ request_id: z.string(), turn_index: Num, question: z.string().nullable(), outcome: z.string() })),
  people: z.array(Person),
});
export type QuestionDetail = z.infer<typeof QuestionDetail>;

export const Inbox = z.looseObject({
  filter: z.looseObject({ start: z.string(), end: z.string(), status: z.string(), category: z.string().nullable(), assignee: z.string().nullable() }),
  ...Page,
  people: z.array(Person),
  rows: z.array(InboxRow),
});
export type Inbox = z.infer<typeof Inbox>;

// What a save sends (POST /admin/api/feedback/{id})
export const ReviewInput = z.strictObject({
  status: z.string(),
  assignee: z.string(),
  note: z.string().max(200),
  issue_key: z.string(),
  seen: z.string(),
});
export type ReviewInput = z.infer<typeof ReviewInput>;

export const ReviewSaved = z.looseObject({ review: Review });
