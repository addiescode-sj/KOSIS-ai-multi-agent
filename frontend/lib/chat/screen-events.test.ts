import { expect, test } from "@jest/globals";

import { citationClick, copyAnswer, streamAbort } from "./screen-events.ts";

const ids = { requestId: "b".repeat(32), threadId: "t1", turn: 2 };
const cite = { survey: "조사", table: "표", url: "https://kosis.kr", text: "KOSIS / 조사 / 표", key: "101_DT_A", rank: 1 };

test("citation_click carries the table key and its rank (api.md 5장)", () => {
  expect(citationClick(cite, ids)).toEqual({
    request_id: ids.requestId, thread_id: "t1", turn: 2, table_keys: ["101_DT_A"], props: { table_key: "101_DT_A", rank: 1 },
  });
  expect(citationClick({ ...cite, rank: null }, ids)).toBeNull(); // no rank to log
});

test("copy_answer sends no answer text, only the common fields", () => {
  const fields = copyAnswer(ids, "101_DT_A");
  expect(fields).toEqual({ request_id: ids.requestId, thread_id: "t1", turn: 2, table_keys: ["101_DT_A"] });
  expect(JSON.stringify(fields)).not.toContain("KOSIS");
  expect(copyAnswer(ids, null).table_keys).toEqual([]);
});

test("stream_abort: elapsed ms since the question, only while a stream ran", () => {
  expect(streamAbort(ids, 1000, 3500.4)?.props).toEqual({ elapsed_ms: 2500 });
  expect(streamAbort(ids, null, 3500)).toBeNull();
  expect(streamAbort(ids, 5000, 4000)?.props).toEqual({ elapsed_ms: 0 }); // clock skew never goes negative
});
