// Every TanStack Query key of the app, in one place: what the shared cache holds is read off this file.
export const queryKeys = {
  me: ["auth", "me"] as const, // GET /auth/me → AuthState
  chats: ["chats"] as const, // a member's conversations: the list and each one (cleared on logout)
  chatList: ["chats", "list"] as const, // GET /chat?cursor=, page by page
  chatDetails: ["chats", "detail"] as const, // every opened member conversation (feedback updates the matching turn)
  chat: (id: string) => ["chats", "detail", id] as const, // GET /chat/{id} → ChatRead
  tables: (q: string) => ["tables", q] as const, // POST /dev/tools/search_tables → TablesResult (search page)
  // operator screens (T3-66, T3-67): the operator app's /admin/api/*
  admin: ["admin"] as const, // everything below (a saved review refreshes the lists)
  adminDashboard: (f: { period: string; user: string; internal: boolean }) =>
    ["admin", "dashboard", f.period, f.user, f.internal] as const, // GET /admin/api/metrics
  adminHome: ["admin", "home"] as const, // GET /admin/api/home
  adminQuestions: (qs: string) => ["admin", "questions", qs] as const, // GET /admin/api/questions?…
  adminQuestion: (id: string) => ["admin", "question", id] as const, // GET /admin/api/questions/{id}
  adminInbox: (qs: string) => ["admin", "inbox", qs] as const, // GET /admin/api/feedback?…
};
