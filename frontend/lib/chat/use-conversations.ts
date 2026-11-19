"use client";

import { useSyncExternalStore } from "react";

import type { Conversation } from "@/types/conversation";

import { conversations } from "./conversations";

const EMPTY: Conversation[] = [];

// This browser's conversations, newest first. The server render has no storage and sees none.
export function useConversations(): Conversation[] {
  return useSyncExternalStore(conversations.subscribe, conversations.list, () => EMPTY);
}

// One saved conversation: undefined while not read yet (server render), null when this browser does not have it.
export function useConversation(id: string): Conversation | null | undefined {
  return useSyncExternalStore(conversations.subscribe, () => conversations.get(id), () => undefined);
}
