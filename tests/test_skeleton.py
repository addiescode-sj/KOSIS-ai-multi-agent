"""T3-00: phase 1 / phase 2 import from the submodules, and question -> graph -> SSE runs offline."""

import json

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from backend.app.main import app, get_graph
from kosis_agent.graph import build_graph


def test_phase1_phase2_import():
    from kosis_ai.schemas import StatAnswer  # phase 1 shared contract
    from kosis_rag.schemas import TableCandidate  # phase 2 search contract

    assert StatAnswer and TableCandidate


def test_graph_appends_reply():
    out = build_graph(FakeListChatModel(responses=["안녕하세요"])).invoke({"messages": [("user", "hi")]})
    assert [m.content for m in out["messages"]] == ["hi", "안녕하세요"]


def test_chat_stream_events():
    app.dependency_overrides[get_graph] = lambda: build_graph(FakeListChatModel(responses=["출생아 수 표"]))
    try:
        res = TestClient(app).post("/chat/stream", json={"question": "출생아 수"})
    finally:
        app.dependency_overrides.clear()
    events = [line.removeprefix("event: ") for line in res.text.splitlines() if line.startswith("event: ")]
    data = [json.loads(line.removeprefix("data: ")) for line in res.text.splitlines() if line.startswith("data: ")]
    assert res.status_code == 200
    assert "token" in events and events[-2:] == ["agent", "result"]
    assert data[-1] == {"answer": "출생아 수 표"}


def test_health():
    assert TestClient(app).get("/health").json() == {"status": "ok"}
