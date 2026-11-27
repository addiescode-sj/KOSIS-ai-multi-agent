import { CircleAlert, SearchX } from "lucide-react";
import type { ReactNode } from "react";

import type { ErrorView } from "@/lib/chat/errors";

// The whole-page notice of app/error.tsx, global-error.tsx and not-found.tsx (T3-68). No AppShell: the shell itself
// may be what failed, and these pages must draw even then.
export function ErrorScreen({ view, missing = false, children }: { view: ErrorView; missing?: boolean; children: ReactNode }) {
  const Icon = missing ? SearchX : CircleAlert;
  return (
    <main className="flex min-h-dvh items-center justify-center bg-canvas p-6">
      <div role={missing ? undefined : "alert"} className="w-full max-w-md rounded-2xl border border-line bg-white p-8 text-center">
        <span className="mx-auto flex size-12 items-center justify-center rounded-full bg-brand-50 text-brand-600"><Icon size={22} aria-hidden /></span>
        <h1 className="mt-5 text-xl font-semibold tracking-tight">{view.title}</h1>
        <p className="mt-2 text-sm text-sub">{view.text}</p>
        <div className="mt-6 flex justify-center gap-2">{children}</div>
      </div>
    </main>
  );
}
