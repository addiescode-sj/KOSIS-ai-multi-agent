"use client"; // error boundaries are Client Components (node_modules/next/dist/docs/.../file-conventions/error.md)

import Link from "next/link";

import { ErrorScreen } from "@/components/error-screen";
import { Button } from "@/components/ui/button";
import { isNewVersion, SCREEN_ERRORS } from "@/lib/chat/errors";

// A page that failed to render (T3-67 `page_error`), or an old page after a deploy asking for a file the new version
// no longer has (`new_version`): a reload fixes that one. The error's own text is never shown.
export default function ErrorPage({ error, retry }: { error: unknown; retry: () => void }) {
  if (isNewVersion(error)) {
    return (
      <ErrorScreen view={SCREEN_ERRORS.new_version}>
        <Button onClick={() => window.location.reload()}>새로고침</Button>
      </ErrorScreen>
    );
  }
  return (
    <ErrorScreen view={SCREEN_ERRORS.page_error}>
      <Button onClick={() => retry()}>다시 시도</Button>
      <Button variant="secondary" asChild><Link href="/">처음 화면으로</Link></Button>
    </ErrorScreen>
  );
}
