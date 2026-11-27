// What the screen says for a failed answer or request (T3-68, PM 문서 13 4·5·8장). The list here is the reference:
// the screen picks the sentence by `code` and shows the server's `message` only for a code it does not know.
// Exception names, exception text and internal addresses are never shown (they stay in the backend logs).

export type ErrorView = { title: string; text: string };

// A failed answer: the backend's codes (backend/app/errors.py) and the ones the screen tells on its own
export const ANSWER_ERRORS: Record<string, ErrorView> = {
  model_unavailable: { title: "답변 준비 중이에요", text: "답변에 필요한 AI 서버가 잠시 멈춰 있어요. 1~2분 뒤 다시 시도해 주세요." },
  upstream_busy: { title: "요청이 많아요", text: "지금 요청이 많아 답하지 못했어요. 잠시 뒤 다시 시도해 주세요." },
  upstream_error: { title: "응답을 마치지 못했어요", text: "답을 만드는 중 문제가 생겼어요. 다시 시도해 주세요." },
  timeout: { title: "시간이 오래 걸려요", text: "답을 만드는 데 시간이 너무 오래 걸렸어요. 질문을 나눠 다시 물어봐 주세요." },
  data_error: { title: "자료를 불러오지 못했어요", text: "이 통계표 자료를 불러오지 못했어요. 다른 질문을 해 주시면 확인해 볼게요." },
  config_error: { title: "서비스 점검이 필요해요", text: "서비스 설정에 문제가 있어 지금은 답할 수 없어요. 불편을 드려 죄송합니다." },
  internal_error: { title: "응답을 마치지 못했어요", text: "잠시 뒤 다시 시도해 주세요." },
  // told by the screen (the backend cannot send these)
  connection_lost: { title: "연결이 끊겼어요", text: "답을 받는 중 연결이 끊겼어요. 다시 시도해 주세요." },
  offline: { title: "인터넷 연결이 끊겼어요", text: "인터넷에 다시 연결되면 다시 시도해 주세요." },
  network_error: { title: "서비스에 연결하지 못했어요", text: "잠시 뒤 다시 시도해 주세요." },
  backend_unreachable: { title: "서비스를 잠시 사용할 수 없어요", text: "잠시 뒤 다시 시도해 주세요." },
  bad_response: { title: "서버 응답을 읽지 못했어요", text: "새로고침한 뒤 다시 시도해 주세요." },
};

export const UNKNOWN_ERROR = ANSWER_ERRORS.internal_error;

/** The card's title and text for an error code; a code this list does not know shows the server's sentence. */
export function errorView(code: string | null | undefined, serverMessage?: string | null): ErrorView {
  const known = code ? ANSWER_ERRORS[code] : undefined;
  if (known) return known;
  const text = serverMessage?.trim();
  return text ? { title: UNKNOWN_ERROR.title, text } : UNKNOWN_ERROR;
}

// A whole screen that could not be drawn (app/error.tsx, global-error.tsx, not-found.tsx)
export const SCREEN_ERRORS = {
  page_error: { title: "화면을 불러오는 중 문제가 생겼어요", text: "새로고침해 주세요. 계속되면 처음 화면에서 다시 시작해 주세요." },
  page_not_found: { title: "찾는 페이지가 없어요", text: "주소를 확인하거나 처음 화면으로 가 주세요." },
  new_version: { title: "서비스가 새 버전으로 바뀌었어요", text: "새로고침하면 바로 이어서 쓸 수 있어요." },
} satisfies Record<string, ErrorView>;

/** Whether a render error is an old page asking for a file the new version no longer has (after a deploy). */
export function isNewVersion(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  return error.name === "ChunkLoadError" || /Loading chunk|dynamically imported module|Importing a module script failed/i.test(error.message);
}

// A failed request outside the answer (api.md 0장 codes) needs no list here: those screens already write their own
// sentence from the status (lib/chat/read.ts, lib/feedback/feedback.ts, lib/auth/account-actions.ts, ...) and show
// the server's text only for input errors it words for people (invalid_feedback). T3-68 checked them, 11/27.

/** The short support number on an error card: the first 8 characters of the request_id (32 hex), or null. */
export function supportId(requestId: string | null | undefined): string | null {
  return requestId && /^[0-9a-f]{8}/i.test(requestId) ? requestId.slice(0, 8) : null;
}
