"""뼈대 패리티: 멀티쿼리 테스트(tests/test_multiquery.py)를 멀티에이전트 그래프로 그대로 다시 돌린다."""

import pytest

import tests.test_multiquery as base
from kosis_multi.orchestrator import build_graph as multi_build_graph
from tests.test_multiquery import *  # noqa: F401,F403


@pytest.fixture(autouse=True)
def _multi_graph(monkeypatch):
    monkeypatch.setattr(base, "build_graph", multi_build_graph)
