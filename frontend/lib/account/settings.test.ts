import { expect, test } from "@jest/globals";

import { createNotifyStore, DEFAULT_NOTIFY, nameOf, SETTINGS_KEY, validName } from "./settings.ts";

function memory() {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  };
}

test("notification switches survive a reload, reset clears them, broken JSON falls back", () => {
  const kv = memory();
  createNotifyStore(() => kv).update({ notifyNotice: true });
  const again = createNotifyStore(() => kv);
  expect(again.get()).toEqual({ ...DEFAULT_NOTIFY, notifyNotice: true });
  again.reset();
  expect(again.get()).toEqual(DEFAULT_NOTIFY);
  kv.setItem(SETTINGS_KEY, "{not json");
  expect(createNotifyStore(() => kv).get()).toEqual(DEFAULT_NOTIFY);
});

test("the shown name is the display name, else the Kakao nickname; names are 1..20 characters", () => {
  expect(nameOf({ display_name: null, nickname: "김통계" })).toBe("김통계");
  expect(nameOf({ display_name: "  ", nickname: "김통계" })).toBe("김통계");
  expect(nameOf({ display_name: "통계왕", nickname: "김통계" })).toBe("통계왕");
  expect([validName(" "), validName("가"), validName("가".repeat(21))]).toEqual([false, true, false]);
});
