import Link from "next/link";

import { ErrorScreen } from "@/components/error-screen";
import { Button } from "@/components/ui/button";
import { SCREEN_ERRORS } from "@/lib/chat/errors";

// An address no page answers (T3-67 `page_not_found`). Rendered inside the root layout (file-conventions/not-found.md).
export default function NotFound() {
  return (
    <ErrorScreen view={SCREEN_ERRORS.page_not_found} missing>
      <Button asChild><Link href="/">처음 화면으로</Link></Button>
    </ErrorScreen>
  );
}
