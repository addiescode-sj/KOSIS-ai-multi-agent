"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { CheckCircle2, CircleAlert, ExternalLink, Loader2, Send, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { type ChatMessage, NODE_LABELS, type NodeStep, type SearchResult, type TableCandidate } from "@/lib/chat-types";

const EXAMPLES = ["2023년 시도별 출생아 수", "시군구별 인구 이동 통계", "65세 이상 인구 비율", "비트코인 가격"];

export function Chat() {
  const { messages, sendMessage, status, stop, error, regenerate } = useChat<ChatMessage>({
    transport: new DefaultChatTransport({ api: "/api/chat" }),
  });
  const [input, setInput] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const busy = status === "submitted" || status === "streaming";

  useEffect(() => {
    // Block body: Chrome's scrollIntoView now returns a Promise, which React would call as the effect cleanup
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, status]);

  function send(text: string) {
    if (!text.trim() || busy) return;
    sendMessage({ text: text.trim() });
    setInput("");
  }

  return (
    <div className="mx-auto flex h-dvh w-full max-w-3xl flex-col px-4">
      <header className="border-b py-4">
        <h1 className="text-lg font-semibold">KOSIS 통계표 안내</h1>
        <p className="text-sm text-muted-foreground">질문에 맞는 국가통계포털 통계표를 찾아 출처와 함께 안내합니다.</p>
      </header>

      <ScrollArea className="min-h-0 flex-1">
        {/* pr-4: a lane for the overlay scrollbar (w-2.5) so it never covers the messages */}
        <div className="flex flex-col gap-4 py-4 pr-4">
          {messages.length === 0 && (
            <div className="flex flex-wrap gap-2 pt-8">
              {EXAMPLES.map((q) => (
                <Button key={q} variant="outline" size="sm" onClick={() => send(q)}>
                  {q}
                </Button>
              ))}
            </div>
          )}
          {messages.map((m, i) =>
            m.role === "user" ? (
              <div key={m.id} className="self-end rounded-2xl bg-primary px-4 py-2 text-primary-foreground">
                {m.parts.map((p) => (p.type === "text" ? p.text : "")).join("")}
              </div>
            ) : (
              <AssistantMessage key={m.id} message={m} running={busy && i === messages.length - 1} />
            ),
          )}
          {status === "submitted" && messages.at(-1)?.role === "user" && <Pending label="파이프라인 시작 중" />}
          {error && (
            <Card className="border-destructive/50">
              <CardContent className="flex items-start gap-2 text-sm text-destructive">
                <CircleAlert className="mt-0.5 size-4" />
                <div className="flex-1">{error.message}</div>
                <Button variant="outline" size="xs" onClick={() => regenerate()}>
                  다시 시도
                </Button>
              </CardContent>
            </Card>
          )}
          <div ref={bottom} />
        </div>
      </ScrollArea>

      <form
        className="flex gap-2 border-t py-4"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <Input value={input} onChange={(e) => setInput(e.target.value)} placeholder="예: 2023년 시도별 출생아 수" />
        {busy ? (
          <Button type="button" variant="outline" onClick={() => stop()} aria-label="중단">
            <Square />
          </Button>
        ) : (
          <Button type="submit" disabled={!input.trim()} aria-label="보내기">
            <Send />
          </Button>
        )}
      </form>
    </div>
  );
}

function AssistantMessage({ message, running }: { message: ChatMessage; running: boolean }) {
  const steps: NodeStep[] = [];
  let result: SearchResult | undefined;
  let answer = "";
  let draft = "";
  for (const p of message.parts) {
    if (p.type === "data-node") steps.push(p.data);
    else if (p.type === "data-draft") draft = p.data.text;
    else if (p.type === "data-result") result = p.data;
    else if (p.type === "text") answer += p.text;
  }
  const pickedKeys = new Set(result?.picked.map((c) => c.key));
  const others = result ? result.candidates.filter((c) => !pickedKeys.has(c.key)) : [];

  return (
    <div className="flex flex-col gap-3">
      <ol className="flex flex-col gap-1 text-sm text-muted-foreground">
        {steps.map((s) => (
          <li key={s.name} className="flex items-center gap-2">
            <CheckCircle2 className="size-4 text-emerald-600" />
            <span>{NODE_LABELS[s.name] ?? s.name}</span>
            <span className="tabular-nums">{(s.seconds * 1000).toFixed(1)} ms</span>
          </li>
        ))}
        {running && !result && !draft && <Pending label="다음 단계 진행 중" />}
      </ol>

      {draft && !answer && (
        <Card>
          <CardContent className="flex flex-col gap-2">
            <Pending label="안내 문장 작성 중 (검증된 부분만 표시)" />
            <p className="text-sm leading-6 whitespace-pre-wrap wrap-anywhere text-muted-foreground">{draft}</p>
          </CardContent>
        </Card>
      )}

      {answer && (
        <Card className={result?.no_answer ? "border-amber-400 bg-amber-50 dark:bg-amber-950/30" : undefined}>
          <CardContent className="flex flex-col gap-2">
            <div className="flex gap-2">
              {result?.no_answer && <Badge variant="outline">맞는 표 없음</Badge>}
              {result && <Badge variant="secondary">{ORIGIN[result.answer_origin] ?? result.answer_origin}</Badge>}
              {draft && result?.answer_origin === "template" && (
                <Badge variant="outline">LLM 문장이 검증을 통과하지 못해 코드 안내로 대체</Badge>
              )}
            </div>
            <p className="text-sm leading-6 whitespace-pre-wrap wrap-anywhere">{answer}</p>
          </CardContent>
        </Card>
      )}

      {/* The tables the answer recommends and cites (T2-39); the rest of what the LLM saw folds away */}
      {result && result.picked.length > 0 && <TableCards tables={result.picked} />}
      {result && others.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">
            {result.picked.length > 0 ? `다른 후보 ${others.length}개` : `검색된 후보 ${others.length}개`}
          </summary>
          <div className="pt-2">
            <TableCards tables={others} />
          </div>
        </details>
      )}
    </div>
  );
}

function TableCards({ tables }: { tables: TableCandidate[] }) {
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {tables.map((c, i) => (
        <Card key={c.key} className="gap-2 py-4">
          <CardHeader className="px-4">
            <CardTitle className="text-sm">
              <a href={c.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:underline">
                {i + 1}. {c.table_name} <ExternalLink className="size-3" />
              </a>
            </CardTitle>
            <CardDescription className="text-xs">
              KOSIS / {c.survey_name} / {c.organization}
            </CardDescription>
          </CardHeader>
          <CardContent className="px-4">
            <Badge variant="outline">수록기간 {c.period}</Badge>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

const ORIGIN: Record<string, string> = { llm: "LLM 안내", template: "코드 템플릿", refusal: "거절" };

function Pending({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" />
      {label}
    </div>
  );
}
