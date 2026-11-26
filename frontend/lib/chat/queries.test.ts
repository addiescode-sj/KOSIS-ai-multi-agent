import { expect, test } from "@jest/globals";
import { QueryClient } from "@tanstack/react-query";

import { queryKeys } from "../query/keys.ts";

import { ChatListChange, chatListChanged, chatListQuery } from "./queries.ts";

test("chat list: kept until a change (no refetch by age, not dropped while unused)", () => {
  expect(chatListQuery.staleTime).toBe(Infinity);
  expect(chatListQuery.gcTime).toBe(Infinity);
});

test("chat list: each change invalidates the cached list at once and nothing else", async () => {
  for (const change of Object.values(ChatListChange)) {
    const client = new QueryClient();
    client.setQueryData(queryKeys.chatList, { pages: [], pageParams: [] });
    client.setQueryData(queryKeys.chat("c1"), { ok: true });
    expect(await chatListChanged(client, change)).toBe(change);
    expect(client.getQueryState(queryKeys.chatList)?.isInvalidated).toBe(true);
    expect(client.getQueryState(queryKeys.chat("c1"))?.isInvalidated).toBe(false);
    client.clear();
  }
});
