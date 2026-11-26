import { z } from "zod";

// Runtime mirrors of the phase 2 search contracts (vendor/phase2 kosis_rag.schemas, /dev/tools/search_tables).
// Only the fields the screens use are listed; unknown fields pass through (looseObject).

export const TableCandidate = z.looseObject({
  key: z.string(),
  org_id: z.string(),
  tbl_id: z.string(),
  table_name: z.string(),
  survey_name: z.string(),
  organization: z.string(),
  period: z.string(),
  url: z.string(),
  score: z.number().nullish(),
});
export type TableCandidate = z.infer<typeof TableCandidate>;

export const SearchOutput = z.looseObject({
  question: z.string(),
  route: z.string(),
  picked: z.array(TableCandidate).default([]),
  candidates: z.array(TableCandidate).default([]),
  refusal_text: z.string().nullish(),
});
export type SearchOutput = z.infer<typeof SearchOutput>;

// POST /dev/tools/search_tables (and the /api/tables proxy) request (openapi SearchRequest)
export const SearchRequest = z.strictObject({ question: z.string().trim().min(1).max(200) });
export type SearchRequest = z.infer<typeof SearchRequest>;
