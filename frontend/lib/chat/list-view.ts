import type { ChatList } from "./use-chat-list.ts";

// What the /chat list body shows, decided once from the list state (one branch per value, app/chat/components).
export const ListBody = {
  ERROR: "error", // nothing could be read
  LOADING: "loading", // the first page is on its way
  ITEMS: "items", // rows to show
  EMPTY: "empty", // nothing to show: see EmptyReason
} as const;
export type ListBody = (typeof ListBody)[keyof typeof ListBody];

// Why an empty list is empty, each with its sentence
export const EmptyReason = {
  NO_CHATS: "no_chats",
  NO_MATCH_SO_FAR: "no_match_so_far", // the pages read so far have no match, more pages on the server
  NO_MATCH: "no_match",
} as const;
export type EmptyReason = (typeof EmptyReason)[keyof typeof EmptyReason];

export const EMPTY_MESSAGE: Record<EmptyReason, string> = {
  [EmptyReason.NO_CHATS]: "아직 저장된 대화가 없어요. 새 대화에서 질문해 보세요.",
  [EmptyReason.NO_MATCH_SO_FAR]: "지금까지 불러온 대화에는 일치하는 것이 없어요.",
  [EmptyReason.NO_MATCH]: "일치하는 대화가 없어요.",
};

type ListState = Pick<ChatList, "error" | "loading" | "items" | "more" | "member">;

export function listBodyOf(list: ListState, shown: number): ListBody {
  if (list.error) return ListBody.ERROR;
  if (list.loading) return ListBody.LOADING;
  return shown > 0 ? ListBody.ITEMS : ListBody.EMPTY;
}

export function emptyReasonOf(list: ListState): EmptyReason {
  if (list.items.length === 0) return EmptyReason.NO_CHATS;
  return list.more ? EmptyReason.NO_MATCH_SO_FAR : EmptyReason.NO_MATCH;
}

// A guest sees the login prompt under the list once it is there (a member is already logged in)
export function showsLoginPrompt(list: ListState): boolean {
  return !list.member && !list.loading;
}

export const LIST_SUB = { member: "카카오 계정에 저장된 대화", guest: "이 탭에 저장된 비회원 대화" } as const;
// a guest's search reads every question and answer; the server list has titles only
export const SEARCH_PLACEHOLDER = { member: "대화 제목 검색", guest: "대화 제목·내용 검색" } as const;
