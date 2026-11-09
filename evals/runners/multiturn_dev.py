"""Agent graph for the multi-turn dev scenarios (T3-27): `run_eval --layer agent --agent evals.runners.multiturn_dev:build`.

run_eval calls the function with no arguments and --llm does not reach the agent layer, so the settings come from
the shell: STAT_PROVIDER (mock | hcx, as backend/app/config.py) for phase 1, and everything else the graph takes.

Search is a golden lookup instead of phase 2, so context handling is measured apart from retrieval quality:
- a question that contains a calculation_golden question verbatim gets that question's table (`found`);
- any other question ("부산은?", a rewrite that lost the wording) gets one fixed unrelated table (`found`), which is
  what a live search often does with a context-free fragment. A turn that misses its context then shows as
  lost_state / wrong args, not as a refusal (plans/T3-27.md, review 4).
Run the same scenarios through live search (backend.app.main:build_agent) once Chroma is up.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from kosis_agent.graph.main_graph import build_graph
from kosis_agent.graph.state import new_checkpointer
from kosis_rag.schemas.io import SearchOutput, TableCandidate

from evals.schemas import DATASETS

ROOT = Path(__file__).resolve().parents[2]
CALC = DATASETS / "calculation_golden.jsonl"
# A small table no golden question uses: what the lookup "finds" for a question it does not know
DISTRACTOR = "111_DT_1B040A30"  # 국제결혼 안내프로그램 이수현황 (9 KB), unrelated to every golden question


def candidate(key: str) -> TableCandidate:
    org_id, _, tbl_id = key.partition("_")
    return TableCandidate(key=key, org_id=org_id, tbl_id=tbl_id, table_name="", survey_name="", organization="",
                          period="", url="")


class GoldenLookupSearch:
    """Stands in for kosis_rag.api.SearchModule: .stream(SearchInput) -> ("output", SearchOutput)."""

    def __init__(self, calc_path: Path = CALC, distractor: str = DISTRACTOR):
        rows = [json.loads(line) for line in calc_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        # longest first, so a question that contains a shorter golden question still gets its own table
        self.known = sorted(((r["question"], r["table_key"]) for r in rows), key=lambda qk: -len(qk[0]))
        self.distractor = distractor
        self.questions: list[str] = []

    def key_for(self, question: str) -> str:
        return next((key for q, key in self.known if q in question), self.distractor)

    def stream(self, inp):
        self.questions.append(inp.question)
        table = candidate(self.key_for(inp.question))
        yield "output", SearchOutput(question=inp.question, route="found", picked=[table], candidates=[table])


def build():
    """The graph under test with the golden lookup search, phase 1 from STAT_PROVIDER, and a checkpointer.

    INTERPRETER (rules | hybrid | llm) and INTERPRETER_MODEL (an HCX model for the LLM step, e.g. HCX-005) are read
    the way the service reads them (backend/app/config.py, backend.app.main.interpreter_llm)."""
    from backend.app.config import agent_config, stat_llm

    kwargs = {}
    params = inspect.signature(build_graph).parameters
    # T3-27 interpreter switch; the baseline graph (PR #9) has no such parameter
    if "interpreter" in params:
        from backend.app.main import interpreter_llm

        cfg = agent_config()
        kwargs.update(interpreter=cfg.interpreter, llm=interpreter_llm(cfg))
    return build_graph(GoldenLookupSearch(), stat_llm(), checkpointer=new_checkpointer(), **kwargs)
