import { z } from "zod";

// POST /feedback (api.md 3장, openapi FeedbackRequest / FeedbackResponse)

export const FeedbackValue = z.enum(["up", "down"]);
export type FeedbackValue = z.infer<typeof FeedbackValue>;

export const UP_CATEGORIES = ["accurate_number", "right_table", "easy", "chart_helpful", "other"] as const;
export const DOWN_CATEGORIES = ["wrong_number", "wrong_table", "hard", "other"] as const;
export const FeedbackCategory = z.enum([...UP_CATEGORIES, ...DOWN_CATEGORIES.filter((c) => c !== "other")]);
export type FeedbackCategory = z.infer<typeof FeedbackCategory>;

export const FEEDBACK_CATEGORIES: Record<FeedbackValue, readonly FeedbackCategory[]> = {
  up: UP_CATEGORIES,
  down: DOWN_CATEGORIES,
};

export const REASON_MAX = 200;

// A choice of the value's list (or none), a reason up to 200 characters, needed for "other"
export const FeedbackRequest = z.strictObject({
  request_id: z.string().min(1),
  value: FeedbackValue,
  category: FeedbackCategory.nullable(),
  reason: z.string().trim().min(1).max(REASON_MAX).optional(),
}).superRefine((body, ctx) => {
  if (body.category !== null && !FEEDBACK_CATEGORIES[body.value].includes(body.category)) {
    ctx.addIssue({ code: "custom", path: ["category"], message: `${body.value}에 없는 선택지입니다` });
  }
  if (body.category === "other" && !body.reason) {
    ctx.addIssue({ code: "custom", path: ["reason"], message: "기타를 고르면 이유를 입력해야 합니다" });
  }
});
export type FeedbackRequest = z.infer<typeof FeedbackRequest>;

export const FeedbackResponse = z.looseObject({ feedback_id: z.string(), updated: z.boolean() });
export type FeedbackResponse = z.infer<typeof FeedbackResponse>;
