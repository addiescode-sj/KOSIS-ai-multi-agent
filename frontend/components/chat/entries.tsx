"use client";

import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { useAuth } from "@/lib/auth/use-me";

import { Chat } from "./chat";
import { GuestSavedChat } from "./saved/guest-saved-chat";
import { MemberSavedChat } from "./saved/member-saved-chat";

// `/`: a new conversation. `?q=` prefills
// the question box (e.g. "이 표로 질문하기" on the search page).
export function NewChat() {
  const prefill = useSearchParams().get("q") ?? undefined;
  // useSearchParams keeps this component out of the prerender, so the id is made in the browser
  const [id] = useState(() => crypto.randomUUID());
  return <Chat key={id} id={id} initial={null} prefill={prefill} />;
}

// `/chat/[id]`: a saved conversation, shown as it was (results, charts, sources) and continued. A member's comes
// from the server (GET /chat/{id}, prefetched by SavedChatHydration), a guest's from this browser.
export function SavedChat({ id }: { id: string }) {
  const auth = useAuth();
  return auth.status === "member" ? <MemberSavedChat id={id} /> : <GuestSavedChat id={id} />;
}
