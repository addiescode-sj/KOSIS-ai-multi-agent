import type { Metadata } from "next";
import { Suspense } from "react";
import "./globals.css";

import { AuthHydration } from "@/components/account/auth-hydration";

import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "KOSIS 통계 AI",
  description: "말로 물으면 KOSIS 통계표에서 코드가 계산한 수치와 출처를 함께 답합니다.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko" className="h-full">
      <head>
        {/* Pretendard (prototype font), served by jsDelivr */}
        <link
          rel="stylesheet"
          href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css"
        />
      </head>
      <body className="min-h-full">
        <Providers>
          {/* the login is read per request (cookies), so every page streams behind this boundary */}
          <Suspense>
            <AuthHydration>{children}</AuthHydration>
          </Suspense>
        </Providers>
      </body>
    </html>
  );
}
