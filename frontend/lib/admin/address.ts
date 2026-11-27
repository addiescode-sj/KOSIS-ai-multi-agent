// A list screen's filter lives in its address and goes to the operator app as it is (the app checks and corrects it,
// and says so in `notice`). Only the keys the screen knows are passed on, one value each.
type Params = Record<string, string | string[] | undefined>;

export const QUESTION_KEYS = ["start", "end", "outcome", "user", "internal", "page_no"] as const;
export const INBOX_KEYS = ["status", "start", "end", "category", "assignee", "page_no"] as const;

/** The known, non-empty keys of an address query as a query string, in a fixed order (one cache key per filter). */
export function addressQuery(params: Params, keys: readonly string[]): string {
  const out = new URLSearchParams();
  for (const k of keys) {
    const v = params[k];
    const one = Array.isArray(v) ? v[0] : v;
    if (one) out.set(k, one);
  }
  return out.toString();
}
