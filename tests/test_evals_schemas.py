"""L1 retrieval golden contract (T2-02): slot limits, unanswerable rules, split leak check, pooled label import."""

import json

import pytest

from evals.schemas import GoldenError, RetrievalItem, check_items, distribution, load_items
from evals.builders.pool import apply_labels, assign_splits

BASE = {"qid": "q1", "split": "test", "qtype": "concept", "source": "scenario", "question": "아기 몇 명 태어났어"}


def test_slots_limited_to_three():
    RetrievalItem(**{**BASE, "qtype": "multi_table", "required": [["a"], ["b"], ["c"]]})
    with pytest.raises(ValueError):
        RetrievalItem(**{**BASE, "qtype": "multi_table", "required": [["a"], ["b"], ["c"], ["d"]]})
    with pytest.raises(ValueError):  # one slot is not a multi-table question
        RetrievalItem(**{**BASE, "qtype": "multi_table", "required": [["a", "b"]]})


def test_unanswerable_needs_reason_and_no_slots():
    RetrievalItem(**{**BASE, "qtype": "no_answer", "answerable": False, "no_answer_reason": "out_of_scope"})
    with pytest.raises(ValueError):
        RetrievalItem(**{**BASE, "qtype": "no_answer", "answerable": False})
    with pytest.raises(ValueError):
        RetrievalItem(**{**BASE, "qtype": "no_answer", "answerable": False, "no_answer_reason": "no_period", "required": [["a"]]})
    with pytest.raises(ValueError):  # reasons are Phase 1 RefusalReason values only
        RetrievalItem(**{**BASE, "qtype": "no_answer", "answerable": False, "no_answer_reason": "cycle_mismatch"})


def test_check_items_flags_unknown_key_and_split_leak():
    dev = RetrievalItem(**{**BASE, "qid": "d1", "split": "dev", "required": [["a"]]})
    test = RetrievalItem(**{**BASE, "qid": "t1", "required": [["a", "x"]]})
    problems = check_items([dev, test], keys={"a"})
    assert "t1: unknown key x" in problems
    assert "primary key a is in both dev and test" in problems
    alt = RetrievalItem(**{**BASE, "qid": "t2", "required": [["b", "a"]]})  # a only as an alternative: allowed
    assert not check_items([dev, alt], keys={"a", "b"})


def test_load_items_collects_problems(tmp_path):
    path = tmp_path / "g.jsonl"
    path.write_text(json.dumps({**BASE, "required": [["a"]]}) + "\n" + json.dumps({**BASE, "required": []}) + "\n", encoding="utf-8")
    with pytest.raises(GoldenError, match="line 2"):
        load_items(path, keys={"a"})


def test_apply_labels_groups_slots_and_counts():
    drafts = [
        {**BASE, "qid": "m1", "qtype": "multi_table"},
        {**BASE, "qid": "n1", "qtype": "no_answer", "answerable": False, "no_answer_reason": "out_of_scope"},
    ]
    rows = [
        {"qid": "m1", "key": "a", "slot": "1"},
        {"qid": "m1", "key": "a2", "slot": "1"},
        {"qid": "m1", "key": "b", "slot": "2"},
        {"qid": "m1", "key": "c", "slot": ""},
        {"qid": "n1", "key": "z", "slot": ""},
    ]
    items = apply_labels(drafts, rows)
    assert items[0].required == [["a", "a2"], ["b"]]
    assert items[1].required == []
    report = distribution(items)
    assert "| multi_table | 0 | 1 | 1 |" in report and "1/2 (50%)" in report


def test_assign_splits_keeps_shared_keys_together():
    items = [RetrievalItem(**{**BASE, "qid": f"c{i}", "required": [[f"k{i // 2}"]]}) for i in range(20)]
    items += [RetrievalItem(**{**BASE, "qid": f"n{i}", "qtype": "no_answer", "answerable": False, "no_answer_reason": "out_of_scope"}) for i in range(10)]
    assign_splits(items)
    assert not check_items(items)  # no gold key in both splits
    for qtype, total in (("concept", 20), ("no_answer", 10)):
        dev = sum(i.split == "dev" for i in items if i.qtype == qtype)
        assert 0 < dev <= round(total * 0.36)
