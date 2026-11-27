// Display strings of the admin numbers. The numbers come from the data; only their text is made here.

const int = new Intl.NumberFormat("ko-KR");

export const count = (n: number) => int.format(n);

/** 2841 "건" → "2,841건", 6.4 "%" → "6.4%" */
export const withUnit = (n: number, unit: string) => `${unit === "%" ? n.toFixed(1) : count(n)}${unit}`;

/** +128, -1.2%p: always signed */
export const signed = (n: number, unit: string) => `${n > 0 ? "+" : n < 0 ? "-" : "±"}${withUnit(Math.abs(n), unit === "건" ? "" : unit)}`;

/** share of a total in %, one decimal: 412 of 988 → "41.7%" */
export const share = (n: number, total: number) => (total ? `${((n / total) * 100).toFixed(1)}%` : "0.0%");

/** "2026-11-19" → "2026.11.19" */
export const dotDate = (day: string) => day.replaceAll("-", ".");

/** "2026-11-19" → "11/19" */
export const shortDate = (day: string) => `${Number(day.slice(5, 7))}/${day.slice(8, 10)}`;

export const ms = (n: number) => `${count(n)}ms`;

const KST_MINUTE = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
});
const KST_SECOND = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  second: "2-digit", hour12: false,
});

/** An ISO time in Korea: "11. 27. 14:05" (lists), with year and seconds for one question */
export const kstTime = (iso: string | null | undefined, seconds = false) =>
  iso ? (seconds ? KST_SECOND : KST_MINUTE).format(new Date(iso)) : "–";

/** 1234 ms → "1.2초"; none → "–" */
export const sec = (ms: number | null | undefined) => (ms == null ? "–" : `${(ms / 1000).toFixed(1)}초`);

/** Cut a long text for a list cell: "…" when cut, `empty` when there is none */
export const cut = (text: string | null | undefined, n: number, empty = "(보관 기간 지남)") =>
  !text ? empty : text.length <= n ? text : `${text.slice(0, n)}…`;
