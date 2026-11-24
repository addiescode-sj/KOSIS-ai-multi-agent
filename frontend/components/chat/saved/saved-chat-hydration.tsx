import { dehydrate, HydrationBoundary } from "@tanstack/react-query";

import { backendGet, loginCookie } from "@/lib/backend/server-read";
import { chatFrom } from "@/lib/chat/read";
import { makeQueryClient } from "@/lib/query/client";
import { queryKeys } from "@/lib/query/keys";

import { SavedChat } from "../entries";

// `/chat/[id]` on the server: with a login, GET /chat/{id} starts here, beside /auth/me (root layout), instead of
// after it in the browser. Not awaited: the conversation streams in when the backend answers.
export async function SavedChatHydration({ params }: { params: Promise<{ id: string }> }) {
  const [{ id }, cookie] = await Promise.all([params, loginCookie()]);
  const client = makeQueryClient();
  if (cookie) {
    void client.prefetchQuery({
      queryKey: queryKeys.chat(id),
      queryFn: async () => chatFrom(await backendGet(`/chat/${encodeURIComponent(id)}`, cookie)),
    });
  }
  return (
    <HydrationBoundary state={dehydrate(client)}>
      <SavedChat id={id} />
    </HydrationBoundary>
  );
}
