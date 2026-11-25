import { z } from "zod";

// The error bodies of the backend's JSON endpoints (api.md 0장, openapi ErrorResponse / HTTPValidationError), in one
// place for every caller and route handler.

// {"error": {"code", "message"}}: every error the service code answers
export const ErrorBody = z.looseObject({
  error: z.looseObject({ code: z.string(), message: z.string() }),
});
export type ErrorBody = z.infer<typeof ErrorBody>;

// FastAPI's own 422 for a request that fails its Pydantic model: {"detail": [{loc, msg, type}, ...]}
export const ValidationErrorBody = z.looseObject({
  detail: z.array(z.looseObject({ loc: z.array(z.union([z.string(), z.number()])), msg: z.string(), type: z.string() })),
});
export type ValidationErrorBody = z.infer<typeof ValidationErrorBody>;

// An older body some routes still answered with ({"detail": "..."})
const DetailText = z.looseObject({ detail: z.string() });

export type ApiError = { code: string; message: string };

/** The code and message of a backend error body; a fixed one when the body is neither shape. */
export function errorFrom(status: number, body: unknown): ApiError {
  const error = ErrorBody.safeParse(body);
  if (error.success) return { code: error.data.error.code, message: error.data.error.message };
  const invalid = ValidationErrorBody.safeParse(body);
  if (invalid.success) return { code: "invalid_request", message: invalid.data.detail.map((d) => d.msg).join(", ") };
  const text = DetailText.safeParse(body);
  if (text.success) return { code: `http_${status}`, message: text.data.detail };
  return { code: `http_${status}`, message: `서버 오류 (${status})` };
}

/** A route handler's own error answer, in the backend's shape. */
export function errorResponse(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message } } satisfies ErrorBody, { status });
}

/** zod issues as one short line for an error message. */
export function issuesText(error: z.ZodError): string {
  return error.issues.map((i) => `${i.path.join(".") || "(body)"}: ${i.message}`).join("; ").slice(0, 300);
}
