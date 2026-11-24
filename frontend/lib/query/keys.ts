// Every TanStack Query key of the app, in one place: what the shared cache holds is read off this file.
export const queryKeys = {
  me: ["auth", "me"] as const, // GET /auth/me → AuthState
  chats: ["chats"] as const, // a member's conversations: the list and each one (cleared on logout)
  chatList: ["chats", "list"] as const, // GET /chat?cursor=, page by page
  chat: (id: string) => ["chats", "detail", id] as const, // GET /chat/{id} → ChatRead
};
