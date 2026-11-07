"""scripts/stat_oracle.py: the oracle search hands the golden key to the agent graph; rows have phase 1's run shape."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location("stat_oracle", ROOT / "scripts" / "stat_oracle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_oracle_search_is_found_with_the_given_key():
    from kosis_rag.schemas.io import SearchInput

    search = _script().OracleSearch()
    search.key = "101_DT_1B8000H"
    (name, out), = list(search.stream(SearchInput(question="q")))
    assert name == "output" and out.route == "found"
    assert out.picked[0].key == "101_DT_1B8000H" and (out.picked[0].org_id, out.picked[0].tbl_id) == ("101", "DT_1B8000H")
