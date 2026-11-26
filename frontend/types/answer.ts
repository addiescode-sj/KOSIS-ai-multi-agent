// View models the answer card reads (built by lib/chat/answer from a ChatResult).

export type AnswerKind = "answered" | "partial" | "clarify" | "refused" | "failed";

export type Headline = { value: string; unit: string | null; period: string | null };

export type Citation = { survey: string; table: string; url: string | null; text: string; key: string; rank: number | null };

export type Tone = "amber" | "red" | "gray";
