"""멀티에이전트 실험 (min-lab-multi-agent, 정식 아님).

메인 오케스트레이터(코드)가 에이전트 다섯을 부른다: ① 판단 ② 검색 ③ 답변 가능성 ④ 계산 ⑤ 차트.
기존 kosis_agent의 부품(해석 규칙·툴 안쪽 함수·상태)은 import만 하고, 노드·그래프·툴 래핑은 이 폴더에서 새로 정의한다.
진입점: kosis_multi.build:build_agent (서비스와 같은 설정, 평가 하네스 --agent).
"""

import kosis_agent  # noqa: F401  (실전 1·2 모듈이 열리기 전에 KOSIS_WORKSPACE를 정한다)
