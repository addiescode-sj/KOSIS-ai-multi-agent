// Every TanStack Query key of the app, in one place: what the shared cache holds is read off this file.
export const queryKeys = {
  me: ["auth", "me"] as const, // GET /auth/me → AuthState
  chats: ["chats"] as const, // a member's conversations: the list and each one (cleared on logout)
  chatList: ["chats", "list"] as const, // GET /chat?cursor=, page by page
  chatDetails: ["chats", "detail"] as const, // every opened member conversation (feedback updates the matching turn)
  chat: (id: string) => ["chats", "detail", id] as const, // GET /chat/{id} → ChatRead
  tables: (q: string) => ["tables", q] as const, // POST /dev/tools/search_tables → TablesResult (search page)
  // admin preview (T3-63, sample data, next dev only)
  adminDashboard: (f: { period: string; user: string; internal: boolean }) =>
    ["admin", "dashboard", f.period, f.user, f.internal] as const, // GET /api/preview/admin/dashboard
  adminTrace: (id: string) => ["admin", "trace", id] as const, // GET /api/preview/admin/trace/{id}
};
