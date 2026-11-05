"""T2-31 diverse L3 builder: facts from cells, sibling compare pairs, the rewrite check, oracle answers PASS."""

import random

import pandas as pd

from kosis_ai.schemas import FactPart, Refusal, Source, StatAnswer, StatResult

from evals.builders.diverse import cell, check_question, josa, make_items
from evals.evaluators.calculation import grade

DIMS = ["dim1"]


def frame() -> pd.DataFrame:
    rows = []
    for code, label in [("A01", "전체"), ("A0301", "개인사업체"), ("A0302", "회사 법인"), ("A0401", "5억원 미만")]:
        for period, value, raw, kind in [("2021", 10.0, "10", "ok"), ("2024", 15.5, "15.5", "ok")]:
            rows.append({"org_id": "358", "tbl_id": "DT_T", "source": "api", "prd_se": "Y", "period": period,
                         "item_id": "T1", "item_name": "사업체 수", "unit": "개", "dim1_name": "특성별",
                         "dim1_code": code, "dim1_label": label, "value_raw": raw, "value": value, "value_kind": kind})
    rows.append({**rows[-1], "period": "2023", "dim1_code": "A0302", "dim1_label": "회사 법인",
                 "value_raw": "X", "value": None, "value_kind": "x_secret"})
    df = pd.DataFrame(rows)
    for i in range(2, 9):
        for part in ("name", "code", "label"):
            df[f"dim{i}_{part}"] = None
    return df


def oracle(item) -> StatAnswer:
    src = Source(org_id="358", tbl_id="DT_T", survey_nm="s", tbl_nm="t")
    if item.kind == "answerable":
        return StatAnswer(outcome="answered", source=src, result=StatResult(facts=item.required_facts),
                          parts=[FactPart(fact_id=f.id) for f in item.required_facts])
    return StatAnswer(outcome="refused", source=src, refusal=Refusal(reason=item.allowed_reasons[0], message="x"))


def test_items_grade_pass_on_their_own_cells():
    df = frame()
    cells = {c.ref(): c for c in map(cell, (r for _, r in df.iterrows()))}
    for turn in range(3):  # missing_value, no_period, no_item
        items = make_items("358_DT_T", "sha", df, DIMS, random.Random(turn), turn)
        assert {i.qid.rsplit("-", 1)[1] for i in items} == {"VAL", "CMP", "TRD", "REF"}
        for item in items:
            assert grade(item, oracle(item), cells).verdict == "PASS", item.qid
        cmp = next(i for i in items if i.type == "compare")
        codes = {f.source_ref.split("|")[2] for f in cmp.required_facts if f.source_ref}
        assert codes == {"A0301", "A0302"}  # siblings, never 개인사업체 vs 5억원 미만
        trend = next(i for i in items if i.type == "trend")
        assert trend.required_facts[2].value == 55.0  # (15.5 / 10 - 1) * 100, by code


def test_check_question_keeps_year_label_item_and_no_stray_numbers():
    df = frame()
    item = make_items("358_DT_T", "sha", df, DIMS, random.Random(0), 1)[0]  # value item
    template = item.question
    label = item.required_facts[0].dims["특성별"]
    assert check_question(item, template, f"2024년에 {label} 사업체 수 알려줘", None, "사업체 수") == []
    assert check_question(item, template, f"2024년 {label} 몇 곳이야?", "곳", "사업체 수")  # item term shares nothing
    assert check_question(item, template, f"{label} 사업체 수 알려줘", None, "사업체 수")  # year lost
    assert check_question(item, template, f"2024년 {label} 사업체 수 30개 넘어?", None, "사업체 수")  # stray number


def test_josa():
    assert josa("입국자", "은는") == "입국자는" and josa("순이동", "은는") == "순이동은"
    assert josa("회사 법인", "과와") == "회사 법인과" and josa("영주(F-5)", "과와") == "영주(F-5)와"
