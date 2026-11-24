"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { ArrowUp, Calculator, CircleAlert, FileCheck2, MessageSquareText, RotateCw, Sparkles } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useAuth } from "@/lib/auth/use-me";
import { conversations, titleOf } from "@/lib/chat/conversations";
import { EXAMPLES } from "@/lib/chat/examples";
import { sessionHeaders } from "@/lib/chat/session";
import { textOf } from "@/lib/chat/text";
import { CHATS_KEY } from "@/lib/chat/use-chat-list";
import type { ChatMessage } from "@/types/chat";
import type { Conversation } from "@/types/conversation";

import { AssistantMessage } from "./answer/assistant-message";
import { Composer } from "./composer";
import { ConversationHistory } from "./history/conversation-history";

// One conversation. `/` starts a new one (empty state = the prototype home); the first question moves the URL to
// /chat/<id> without a remount. The backend thread_id from `meta` goes back with the next question so follow-ups
// carry the context (multi-turn). A guest's turns are saved to this browser (lib/conversations) when a reply ends;
// a member's are saved by the backend (the thread id is the conversation id, so the URL moves to it).
export function Chat({ id, initial, prefill }: { id: string; initial: Conversation | null; prefill?: string }) {
  const auth = useAuth();
  const member = auth.status === "member";
  const memberRef = useRef(member);
  useEffect(() => { memberRef.current = member; }, [member]);
  const client = useQueryClient();
  const threadId = useRef<string | null>(initial?.threadId ?? null);
  const [activeId, setActiveId] = useState(initial?.id ?? id);
  const [transport] = useState(() => new DefaultChatTransport<ChatMessage>({ api: "/api/chat" }));
  // the login cookie goes with every request; a guest also names its session (X-Session-Id, contract 1장)
  const headers = () => (member ? {} : sessionHeaders());
  const { messages, sendMessage, status, stop, error, regenerate } = useChat<ChatMessage>({
    id,
    messages: initial?.messages ?? [],
    transport,
    // One render per 50 ms instead of per token: the draft is the whole text so far, so a skipped update loses
    // nothing, and the smooth scroll below no longer restarts on every token.
    throttle: 50,
    onData: (part) => {
      if (part.type !== "data-meta") return;
      threadId.current = part.data.threadId;
      if (memberRef.current) {
        window.history.replaceState(null, "", `/chat/${part.data.threadId}`);
        setActiveId(part.data.threadId);
      }
    },
  });
  const busy = status === "submitted" || status === "streaming";
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Block body: Chrome's scrollIntoView returns a Promise, which React would call as the effect cleanup
    if (messages.length) bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, status]);

  useEffect(() => {
    if (busy || messages.length === 0) return; // save when a reply ends (done, stopped or failed)
    if (member) {
      void client.invalidateQueries({ queryKey: CHATS_KEY, exact: true }); // the backend saved the turn
      return;
    }
    conversations.save({ id, title: titleOf(messages), threadId: threadId.current, updatedAt: Date.now(), messages });
  }, [busy, id, messages, member, client]);

  function send(text: string) {
    if (!text.trim() || busy) return;
    if (messages.length === 0 && window.location.pathname !== `/chat/${id}`) {
      window.history.replaceState(null, "", `/chat/${id}`); // Next's router picks this up; no remount
    }
    sendMessage({ text: text.trim() }, { body: { threadId: threadId.current }, headers: headers() });
  }

  if (messages.length === 0) return <Home prefill={prefill} onSend={send} member={member} />;

  return (
    <AppShell title={titleOf(messages)} sub={member ? "대화 · 계정에 저장됨" : "대화 · 이 브라우저에 저장됨"}>
      <div className="flex gap-6">
      <ConversationHistory activeId={activeId} />
      <div className="mx-auto flex min-w-0 max-w-3xl flex-1 flex-col gap-5">
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={m.id} className="flex justify-end">
              <p className="max-w-[80%] rounded-2xl rounded-br-md bg-brand-600 px-4 py-2.5 text-[15px] whitespace-pre-wrap text-white">
                {textOf(m)}
              </p>
            </div>
          ) : (
            <AssistantMessage key={m.id} message={m} running={busy && i === messages.length - 1} onAsk={send} />
          ),
        )}
        {status === "submitted" && messages.at(-1)?.role === "user" && (
          <AssistantMessage message={{ id: "pending", role: "assistant", parts: [] }} running onAsk={send} />
        )}
        {error && (
          <Card className="flex items-start gap-4 p-6">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-red-50 text-red-500"><CircleAlert size={19} /></span>
            <div className="min-w-0 flex-1">
              <h2 className="text-[17px] font-semibold">응답을 마치지 못했습니다</h2>
              <p className="mt-1.5 text-sm break-words text-sub">{error.message}</p>
            </div>
            <Button onClick={() => regenerate({ body: { threadId: threadId.current }, headers: headers() })}><RotateCw />다시 시도</Button>
          </Card>
        )}
        <div ref={bottom} />
        <Composer busy={busy} onSend={send} onStop={stop} placeholder="이어서 질문하세요. 예: 같은 기간 서울은?" />
      </div>
      </div>
    </AppShell>
  );
}

const FEATURES = [
  { icon: Calculator, t: "정확한 수치", d: "숫자는 AI가 추측하지 않습니다. 코드가 통계표에서 직접 계산합니다." },
  { icon: FileCheck2, t: "명확한 출처", d: "모든 답변에 KOSIS 조사명, 통계표명, 원본 링크를 함께 제공합니다." },
  { icon: MessageSquareText, t: "자연어 검색", d: "표 이름을 몰라도 됩니다. 평소 말로 질문하면 맞는 표를 찾아 드립니다." },
];

function Home({ prefill, onSend, member }: { prefill?: string; onSend: (text: string) => void; member: boolean }) {
  const [text, setText] = useState(prefill ?? "");
  return (
    <AppShell title="새 대화">
      <div className="mx-auto flex max-w-3xl flex-col items-center pt-6 text-center md:pt-10">
        <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-3 py-1 text-xs font-semibold text-brand-700">
          <Sparkles size={13} />KOSIS 국가통계 기반
        </span>
        <h2 className="mt-5 text-[28px] leading-tight font-bold tracking-tight md:text-[34px]">
          표 이름을 몰라도 됩니다.<br />말로 물으면 <span className="text-brand-600">정확한 국가통계</span>를 찾아 드립니다.
        </h2>
        <p className="mt-4 text-[15px] text-sub">코드가 직접 계산한 수치와 출처를 함께 제공합니다.</p>

        <Card className="mt-10 w-full p-3 text-left shadow-[0_8px_30px_rgba(17,24,39,0.06)]">
          <form onSubmit={(e) => { e.preventDefault(); onSend(text); }}>
            <textarea
              rows={3}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  onSend(text);
                }
              }}
              placeholder="궁금한 통계를 평소 말로 입력하세요"
              aria-label="질문"
              className="w-full resize-none bg-transparent px-3 pt-2 text-[15px] outline-none placeholder:text-mute"
            />
            <div className="flex items-center justify-between border-t border-line px-2 pt-3">
              <span className="text-xs text-mute">{member ? "대화는 계정에 저장됩니다" : "대화는 이 브라우저에만 저장됩니다"}</span>
              <Button type="submit" disabled={!text.trim()}><ArrowUp />바로 질문하기</Button>
            </div>
          </form>
        </Card>
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          {EXAMPLES.map((e) => (
            <button key={e} type="button" onClick={() => onSend(e)}
              className="cursor-pointer rounded-full border border-line bg-white px-3.5 py-1.5 text-[13px] text-sub hover:border-brand-300 hover:text-brand-600">
              {e}
            </button>
          ))}
        </div>

        <div className="mt-14 grid w-full gap-4 text-left md:grid-cols-3">
          {FEATURES.map((f) => (
            <Card key={f.t} className="p-5">
              <span className="flex size-10 items-center justify-center rounded-full border border-brand-100 bg-brand-50 text-brand-600"><f.icon size={18} /></span>
              <h3 className="mt-4 text-[15px] font-semibold">{f.t}</h3>
              <p className="mt-1.5 text-[13px] leading-relaxed text-sub">{f.d}</p>
            </Card>
          ))}
        </div>
      </div>
    </AppShell>
  );
}
