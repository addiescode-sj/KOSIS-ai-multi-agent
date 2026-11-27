import { ChevronLeft, ChevronRight } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { count } from "@/lib/admin/format";

// Page links of a list: the address keeps its filter and only page_no changes
export function Pager({ path, params, page, size, total }: { path: string; params: URLSearchParams; page: number; size: number; total: number }) {
  const pages = Math.max(1, Math.ceil(total / size));
  const to = (n: number) => {
    const p = new URLSearchParams(params);
    p.set("page_no", String(n));
    return `${path}?${p}`;
  };
  return (
    <div className="mt-4 flex items-center justify-between text-sm text-sub">
      <span>모두 {count(total)}건 · {page} / {pages}쪽</span>
      <div className="flex gap-2">
        {page > 1 ? (
          <Button asChild variant="secondary" size="sm"><Link href={to(page - 1)}><ChevronLeft />이전</Link></Button>
        ) : (
          <Button variant="secondary" size="sm" disabled><ChevronLeft />이전</Button>
        )}
        {page < pages ? (
          <Button asChild variant="secondary" size="sm"><Link href={to(page + 1)}>다음<ChevronRight /></Link></Button>
        ) : (
          <Button variant="secondary" size="sm" disabled>다음<ChevronRight /></Button>
        )}
      </div>
    </div>
  );
}
