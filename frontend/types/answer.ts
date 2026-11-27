// View models the answer card reads (built by lib/chat/answer from a ChatResult).

export type AnswerKind = "answered" | "partial" | "clarify" | "refused" | "failed";

// value: the number as shown (formatValue). raw and round are the same fact, for the count-up (T3-64) to land on value.
export type Headline = { value: string; raw: number; round: number | null; unit: string | null; period: string | null };

export type Citation = { survey: string; table: string; url: string | null; text: string; key: string; rank: number | null };

export type Tone = "amber" | "red" | "gray";
