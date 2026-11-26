<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## 상태는 어디에 두는가 (PR #46 리뷰)

| 종류 | 위치 | 예 |
|---|---|---|
| 서버 상태 | TanStack Query 캐시 하나(`lib/query/client.ts`). 키는 `lib/query/keys.ts`에만 둔다 | `/auth/me`, 회원 대화 목록·한 대화 |
| 이 브라우저에만 있는 값 | 외부 store + `useSyncExternalStore` | 비회원 대화(`lib/chat/conversations.ts`), 알림 스위치(`lib/account/settings.ts`) |
| 화면 하나의 값 | 컴포넌트 `useState` | 입력값, 열린 Dialog |

- 로그인처럼 서버에서 알 수 있는 값은 서버 컴포넌트가 미리 읽는다. `prefetchQuery`를 await하지 않고 `HydrationBoundary`로 넘기며, 대기 중인 쿼리도 dehydrate한다.
  - 클라이언트는 `useSuspenseQuery`로 읽는다.
  - 미리 읽는 곳은 두 군데다: 루트 layout의 `AuthHydration`, `/chat/[id]`의 `SavedChatHydration`.
- `useSuspenseQuery`로 읽는 쿼리는 서버에서 미리 읽어 둔다. 그렇지 않으면 서버 렌더 중에 상대 주소 fetch가 실패한다.
- 쿼리 함수는 throw하지 않고 상태 값을 돌려준다. 예: `authFrom`, `chatFrom`. "없음"·"오류"는 error boundary가 아니라 화면 하나로 보여 준다.
- 잠깐 보이는 알림은 타이머로 지우지 않는다. mutation 상태에서 정하고, 다음 입력 때 `reset()`으로 지운다.
