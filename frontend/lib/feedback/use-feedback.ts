"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import type { ChatRead } from "@/lib/chat/read";
import { queryKeys } from "@/lib/query/keys";

import { sendFeedback, type FeedbackInput, type FeedbackResult } from "./feedback";

/**
 * Send 👍/👎 (+ choice, reason) for one answer. The result is a value ({ok: false, ...} on failure), so read
 * `data` rather than `error`. A member conversation already in the cache gets the new value on its turn, so
 * reopening it shows what was sent. A guest keeps it with the turn (setTurnFeedback in lib/chat/conversations).
 */
export function useSendFeedback() {
  const client = useQueryClient();
  return useMutation<FeedbackResult, Error, FeedbackInput>({
    mutationFn: sendFeedback,
    onSuccess: (result, input) => {
      if (!result.ok) return;
      const feedback = { value: input.value, category: input.category };
      client.setQueriesData<ChatRead>({ queryKey: queryKeys.chatDetails }, (read) => {
        if (read?.status !== "ready") return read;
        const hit = read.chat.turns.some((t) => requestIdOf(t.result) === input.requestId);
        if (!hit) return read;
        return {
          ...read,
          chat: {
            ...read.chat,
            turns: read.chat.turns.map((t) => (requestIdOf(t.result) === input.requestId ? { ...t, feedback } : t)),
          },
        };
      });
    },
  });
}

function requestIdOf(result: unknown): unknown {
  return typeof result === "object" && result !== null ? (result as { request_id?: unknown }).request_id : undefined;
}
