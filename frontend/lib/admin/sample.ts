import type { AdminTrace } from "./schemas.ts";

// Sample spans of the trace stand-in screen (the design mockup's Jaeger page, T3-65). Not a real trace: the screen
// carries the "예시 데이터" badge; real traces are in Jaeger. The metrics dashboard has no samples (T3-66).

export function sampleTrace(requestId: string): AdminTrace {
  return {
    requestId,
    spans: [
      { name: "graph.run", start: 0, end: 100 }, { name: "understand_question", start: 0, end: 14 },
      { name: "search_tables", start: 14, end: 52 }, { name: "compute_value", start: 52, end: 68 },
      { name: "render_chart", start: 68, end: 92 }, { name: "stream_answer", start: 88, end: 100 },
    ],
  };
}

export const SAMPLE_TRACE_ID = "7f3a9c21e04b";
