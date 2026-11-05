// Run: node --test lib/sse.test.ts (Node 24 strips the types)
import assert from "node:assert/strict";
import { test } from "node:test";

import { parseSSE } from "./sse.ts";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
}

test("events split across chunks, keep-alive comments and CRLF", async () => {
  const body = streamOf([
    ': ping\n\nevent: parse\ndata: {"seco',
    'nds": 0.1}\r\n\r\nevent: result\ndata: {"answer": "a: b"}\n\n',
    "data: tail-without-blank-line",
  ]);
  const events = [];
  for await (const e of parseSSE(body)) events.push(e);
  assert.deepEqual(events, [
    { event: "parse", data: '{"seconds": 0.1}' },
    { event: "result", data: '{"answer": "a: b"}' },
  ]);
});
