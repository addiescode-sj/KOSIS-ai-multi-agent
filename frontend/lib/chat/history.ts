import type { ConversationItem } from "../../types/account.ts";

// Conversation lists grouped by day the way the prototype sidebar shows them (오늘 · 어제 · 지난 7일 · 이전).
// Days are Korean days (Asia/Seoul, contract 0장): the server's updated_at is UTC.
const DAY = 86_400_000;
const KST = 9 * 3_600_000;

const dayIndex = (t: number) => Math.floor((t + KST) / DAY);

export type HistoryGroup = { label: string; items: ConversationItem[] };

export function groupByDay(items: ConversationItem[], now: number): HistoryGroup[] {
  const today = dayIndex(now);
  const label = (t: number) => {
    const ago = today - dayIndex(t);
    return ago <= 0 ? "오늘" : ago === 1 ? "어제" : ago < 7 ? "지난 7일" : "이전";
  };
  const groups: HistoryGroup[] = [];
  for (const item of [...items].sort((a, b) => b.updatedAt - a.updatedAt)) {
    const l = label(item.updatedAt);
    const g = groups.at(-1);
    if (g?.label === l) g.items.push(item);
    else groups.push({ label: l, items: [item] });
  }
  return groups;
}

// 2025.01.15, in Korean time
export function formatDay(t: number): string {
  const d = new Date(t + KST);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}.${p(d.getUTCMonth() + 1)}.${p(d.getUTCDate())}`;
}
