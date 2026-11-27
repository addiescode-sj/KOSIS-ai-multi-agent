import { expect, test } from "@jest/globals";

import { formatValue } from "../chat/answer.ts";
import { countFrame, fractionDigits } from "./count-up.ts";

const target = (value: number, round: number | null) => ({ value: formatValue({ value, round }), raw: value, round });

test("the last frame is the server value's own string, whatever the rounding", () => {
  for (const [value, round] of [[230028, null], [0.72, null], [12.5, 1], [3, 2], [-3.2, 1], [0, null], [1234567.891, null]] as const) {
    const t = target(value, round);
    expect(countFrame(t, 1)).toBe(formatValue({ value, round }));
    expect(countFrame(t, 1.2)).toBe(t.value); // past the end stays on the value
  }
});

test("frames before the end keep the final decimal places", () => {
  const derived = target(12.5, 1); // round 1 → "12.5"
  expect(countFrame(derived, 0)).toBe("0.0");
  expect(fractionDigits(countFrame(derived, 0.5))).toBe(1);
  const cell = target(0.72, null); // a source cell: digits come from the shown string
  expect(countFrame(cell, 0)).toBe("0.00");
  expect(fractionDigits(countFrame(cell, 0.3))).toBe(2);
});

test("counting starts at 0 and moves toward the value, also for a negative one", () => {
  expect(countFrame(target(230028, null), 0)).toBe("0");
  const half = Number(countFrame(target(230028, null), 0.5).replaceAll(",", ""));
  expect(half).toBeGreaterThan(0);
  expect(half).toBeLessThan(230028);
  expect(countFrame(target(-3.2, 1), 0)).toBe("0.0"); // no "-0.0"
  expect(Number(countFrame(target(-3.2, 1), 0.5))).toBeLessThan(0);
});

test("fractionDigits reads a ko-KR number string", () => {
  expect(fractionDigits("1,234")).toBe(0);
  expect(fractionDigits("1,234.50")).toBe(2);
});
