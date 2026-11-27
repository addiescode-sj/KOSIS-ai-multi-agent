"use client";

import { useSuspenseQuery } from "@tanstack/react-query";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { traceQuery } from "@/lib/admin/queries";

import { ADMIN_HOME } from "./admin-shell";

// `/preview/admin/jaeger`: one request's spans as a waterfall (mockup Jaeger, standing in for the Jaeger UI)
export function TraceScreen({ requestId }: { requestId: string }) {
  const { data } = useSuspenseQuery(traceQuery(requestId));
  return (
    <div className="min-h-dvh bg-canvas">
      <div className="flex h-12 items-center gap-3 border-b border-line bg-white px-6 text-sm text-sub">
        <Badge variant="blue">외부 · Jaeger</Badge>
        <Badge variant="amber">예시 데이터</Badge>
        <Link href={ADMIN_HOME} className="ml-auto text-brand-600">← 메트릭으로 돌아가기</Link>
      </div>
      <div className="mx-auto max-w-5xl p-10">
        <h1 className="text-2xl font-semibold">Jaeger 분산 추적</h1>
        <p className="mt-1 font-mono text-sm text-sub">요청 ID · {requestId}</p>
        {data.ok ? (
          <Card className="mt-6 space-y-3 p-6">
            {data.data.spans.map((s) => (
              <div key={s.name} className="grid grid-cols-[180px_1fr] items-center gap-4 text-[13px]">
                <span className="font-mono text-sub">{s.name}</span>
                <div className="relative h-5 rounded bg-canvas" title={`${s.start}% → ${s.end}%`}>
                  <div className="absolute h-5 rounded bg-brand-500" style={{ left: `${s.start}%`, width: `${Math.max(0, s.end - s.start)}%` }} />
                </div>
              </div>
            ))}
          </Card>
        ) : (
          <p role="alert" className="mt-6 rounded-2xl border border-red-200 bg-red-50 p-6 text-sm text-red-600">추적을 불러오지 못했습니다. {data.message}</p>
        )}
      </div>
    </div>
  );
}
