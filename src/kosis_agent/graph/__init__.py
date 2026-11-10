"""graph/: 실행 순서와 분기 조건 (연구 문서 6장). 알고리즘은 retrieval/·services/에 있고, 노드는 그 툴을 부른다.

    main_graph.py   메인 워크플로 조립 (build_graph): context → search → statistics → chart → finalize
    state.py        AgentState · ChatResult · chat_result · new_checkpointer
    context.py      지시어("그 표" 등) 규칙: 직전 표를 다시 쓸지 판단
"""
