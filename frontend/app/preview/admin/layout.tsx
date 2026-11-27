import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { adminPreviewOn } from "@/lib/admin/preview";

// Admin preview screens (T3-65): the design mockup's admin console with sample numbers. `next dev` only — a
// production build answers 404 here (the deployed /admin is the separate T3-57 app). Never indexed or linked.
export const metadata: Metadata = {
  title: "KOSIS 통계 AI · Admin (미리보기)",
  robots: { index: false, follow: false },
};

export default function AdminPreviewLayout({ children }: LayoutProps<"/preview/admin">) {
  if (!adminPreviewOn()) notFound();
  return children;
}
