import { dehydrate, HydrationBoundary } from "@tanstack/react-query";

import { loginCookie } from "@/lib/backend/server-read";
import { prefetchChat } from "@/lib/chat/chat-list.server";
import { makeQueryClient } from "@/lib/query/client";

import { SavedChat } from "../entries";

// `/chat/[id]` on the server: with a login, GET /chat/{id} starts here, beside /auth/me (root layout), instead of
// after it in the browser. Not awaited: the conversation streams in when the backend answers.
export async function SavedChatHydration({ params }: { params: Promise<{ id: string }> }) {
  const [{ id }, cookie] = await Promise.all([params, loginCookie()]);
  const client = makeQueryClient();
  if (cookie) prefetchChat(client, id, cookie);
  return (
    <HydrationBoundary state={dehydrate(client)}>
      <SavedChat id={id} />
    </HydrationBoundary>
  );
}
