import type { Headline } from "../../types/answer.ts";

// Frames of the 핵심 수치 count-up (T3-62). The last frame is the headline's own string (formatValue of the
// server's fact), never a number rebuilt here; the frames before it are only the animation on the way there.
// They keep the last frame's decimal places so the text does not change width while it counts.

export type CountTarget = Pick<Headline, "value" | "raw" | "round">;

// Decimal places of a ko-KR number string ("1,234.50" → 2).
export function fractionDigits(text: string): number {
  const dot = text.lastIndexOf(".");
  return dot < 0 ? 0 : text.length - dot - 1;
}

export function easeOutCubic(t: number): number {
  return 1 - (1 - t) ** 3;
}

// progress runs 0 → 1 (linear); the easing is applied here so the frames are the same in a test.
export function countFrame(target: CountTarget, progress: number): string {
  if (progress >= 1) return target.value;
  const digits = target.round ?? fractionDigits(target.value);
  const n = target.raw * easeOutCubic(Math.max(0, progress)) + 0; // + 0 turns -0 into 0 (no "-0" frame)
  return n.toLocaleString("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
