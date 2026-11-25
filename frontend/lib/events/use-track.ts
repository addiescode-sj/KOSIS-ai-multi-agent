"use client";

import { useCallback } from "react";

import { buildEvent, queueEvent, type EventFields, type EventType } from "./events";

/** track("citation_click", { request_id, thread_id, turn, props: { table_key, rank } }): queued, sent in the background. */
export function useTrack() {
  return useCallback(<T extends EventType>(type: T, fields: EventFields<T> = {}) => {
    try {
      queueEvent(buildEvent(type, fields));
    } catch {
      /* never in the way of the screen */
    }
  }, []);
}
