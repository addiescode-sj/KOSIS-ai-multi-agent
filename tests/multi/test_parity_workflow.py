"""뼈대 패리티: 기존 그래프 테스트(tests/test_workflow.py)를 멀티에이전트 그래프로 그대로 다시 돌린다.

test_workflow의 테스트 함수를 이 모듈로 가져와 모으고, 그 모듈의 build_graph만 kosis_multi 것으로 바꿔 끼운다.
모두 통과하면 경로·표 키·답·툴 호출(채점 결과)이 기존 그래프와 같다.
"""

import pytest

import tests.test_workflow as base
from kosis_multi.orchestrator import build_graph as multi_build_graph
from tests.test_workflow import *  # noqa: F401,F403  (테스트 함수를 이 모듈에서 다시 모은다)


@pytest.fixture(autouse=True)
def _multi_graph(monkeypatch):
    monkeypatch.setattr(base, "build_graph", multi_build_graph)
