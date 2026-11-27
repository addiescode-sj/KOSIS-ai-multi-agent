"use client"; // replaces the root layout when it fails: its own <html>, <body> and styles (file-conventions/error.md)

import "./globals.css";

import Link from "next/link";

import { ErrorScreen } from "@/components/error-screen";
import { Button } from "@/components/ui/button";
import { isNewVersion, SCREEN_ERRORS } from "@/lib/chat/errors";

// The root layout itself failed (T3-67 `page_error` / `new_version`). The app's providers are not here.
export default function GlobalError({ error, retry }: { error: unknown; retry: () => void }) {
  const fresh = isNewVersion(error);
  return (
    <html lang="ko">
      <body>
        <title>KOSIS 통계 AI</title>
        <ErrorScreen view={fresh ? SCREEN_ERRORS.new_version : SCREEN_ERRORS.page_error}>
          {fresh ? (
            <Button onClick={() => window.location.reload()}>새로고침</Button>
          ) : (
            <>
              <Button onClick={() => retry()}>다시 시도</Button>
              <Button variant="secondary" asChild><Link href="/">처음 화면으로</Link></Button>
            </>
          )}
        </ErrorScreen>
      </body>
    </html>
  );
}
