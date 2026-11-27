import type { Metadata } from "next";

// The operator screens (T3-67). Who may see them is settled before any of this renders (proxy.ts) and again by every
// page (AdminGate); never indexed or linked from the service.
export const metadata: Metadata = {
  title: "KOSIS 통계 AI · 운영",
  robots: { index: false, follow: false },
};

export default function AdminLayout({ children }: LayoutProps<"/admin">) {
  return children;
}
