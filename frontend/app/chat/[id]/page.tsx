import { Suspense } from "react";

import { SavedChatHydration } from "@/components/chat/saved/saved-chat-hydration";

export default function Page({ params }: PageProps<"/chat/[id]">) {
  return (
    <Suspense>
      <SavedChatHydration params={params} />
    </Suspense>
  );
}
