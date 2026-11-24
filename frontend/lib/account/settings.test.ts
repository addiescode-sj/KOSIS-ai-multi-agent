import { expect, test } from "@jest/globals";

import { createSettingsStore, DEFAULT_SETTINGS, nameOf, SETTINGS_KEY, validName } from "./settings.ts";

function memory() {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    data,
  };
}

test("settings survive a reload and reset clears them", () => {
  const kv = memory();
  createSettingsStore(() => kv).update({ displayName: "통계왕", color: "rose" });
  const again = createSettingsStore(() => kv);
  expect(again.get()).toMatchObject({ displayName: "통계왕", color: "rose", retention: "90일 후 삭제" });
  again.reset();
  expect(again.get()).toEqual(DEFAULT_SETTINGS);
});

test("values the contract does not allow fall back: retention past 90 days, unknown colors, broken JSON", () => {
  const kv = memory();
  kv.setItem(SETTINGS_KEY, JSON.stringify({ retention: "보관 기간 없음", color: "purple" }));
  expect(createSettingsStore(() => kv).get()).toMatchObject({ retention: "90일 후 삭제", color: "blue" });
  kv.setItem(SETTINGS_KEY, "{not json");
  expect(createSettingsStore(() => kv).get()).toEqual(DEFAULT_SETTINGS);
});

test("the shown name is the display name, else the Kakao nickname; names are 1..20 characters", () => {
  expect(nameOf({ ...DEFAULT_SETTINGS, displayName: null }, "김통계")).toBe("김통계");
  expect(nameOf({ ...DEFAULT_SETTINGS, displayName: "  " }, "김통계")).toBe("김통계");
  expect(nameOf({ ...DEFAULT_SETTINGS, displayName: "통계왕" }, "김통계")).toBe("통계왕");
  expect([validName(" "), validName("가"), validName("가".repeat(21))]).toEqual([false, true, false]);
});
