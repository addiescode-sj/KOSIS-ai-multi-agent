import { Suspense } from "react";

import { LoginScreen } from "@/components/account/login-screen";

export default function Page() {
  return (
    <Suspense>
      <LoginScreen />
    </Suspense>
  );
}
