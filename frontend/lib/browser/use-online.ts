"use client";

import { useSyncExternalStore } from "react";

// Whether the browser says it is online (navigator.onLine + the online / offline events, T3-67). The server render
// and the first client render say online, so a page never starts with the offline notice by mistake.

function subscribe(onChange: () => void): () => void {
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}

export function useOnline(): boolean {
  return useSyncExternalStore(subscribe, () => navigator.onLine, () => true);
}
