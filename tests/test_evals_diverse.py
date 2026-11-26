"""T2-31 diverse L3 builder: facts from cells, sibling compare pairs, the rewrite check, oracle answers PASS."""

import random

import pandas as pd

from kosis_ai.schemas import FactPart, Refusal, Source, StatAnswer, StatResult

from evals.builders.diverse import cell, check_question, josa, label_ok, make_items, quotas
from evals.schemas import CalcItem
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
        return StatAnswer(outcome="answered", source=src, result=StatResult(facts=item.required_facts), text=src.citation(),
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


def test_compare_pair_never_mixes_units():
    # T3-46: 구성비(%) next to 평균(백만원) in one dim: the only sibling pair with one unit is A0303/A0304
    df = frame()
    extra = [{**df.iloc[0].to_dict(), "period": "2024", "dim1_code": code, "dim1_label": label, "unit": unit,
              "value": v, "value_raw": str(v)} for code, label, unit, v in
             [("A0303", "평균", "백만원", 30.0), ("A0304", "10억원 미만", "%", 2.0), ("A0305", "10억원 이상", "%", 1.0)]]
    df = pd.concat([df[~df.dim1_code.isin(["A0301", "A0302"])], pd.DataFrame(extra)], ignore_index=True)
    cmp = next(i for i in make_items("358_DT_T", "sha", df, DIMS, random.Random(0), 0) if i.type == "compare")
    assert {f.source_ref.split("|")[2] for f in cmp.required_facts if f.source_ref} == {"A0304", "A0305"}


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


def test_quotas_spread_evenly_and_cap_small_surveys():
    # T3-46: a survey with 3 tables gets at most 6 questions (2 per table); the rest is split evenly
    q = quotas({"tiny": 3, "mid": 40, "big": 1875}, 100, 2)
    assert q == {"tiny": 6, "mid": 47, "big": 47} and sum(q.values()) == 100


def test_check_question_requires_subtotals_and_rejects_count_for_percent():
    # T3-46: '아시아주계' is a subtotal (not the grand total), so a question without it asks a different cell
    item = CalcItem(qid="q", kind="answerable", type="value", question="2025년 단기체류외국인은?", table_key="k",
                    table_sha256="s", required_facts=[{"id": "f1", "value": 5.0, "unit": "%", "period": "2025",
                                                       "dims": {"국적별": "아시아주계"}, "source_ref": "T|A|2025"}])
    assert check_question(item, item.question, "2025년 단기체류외국인은 몇 %야?", None, None)
    assert check_question(item, item.question, "2025년 아시아주계 단기체류외국인은 몇 %야?", None, None) == []
    assert check_question(item, item.question, "2025년 아시아주계 단기체류외국인은 몇 명이야?", None, None)  # % cell
    assert label_ok("지사(점) 영업장 공장 출장소", "2024년 지사(점) 영업장 공장 출장소 비율은?")


def test_calc_builders_write_one_format(tmp_path, monkeypatch):
    # T3-46: every calculation golden file is CalcItem dumped with exclude_defaults (no [] / null, no extra fields)
    from evals.builders import multiturn
    item = CalcItem(qid="q", kind="answerable", type="value", question="2025년 x는?", table_key="k", table_sha256="s",
                    required_facts=[{"id": "f1", "value": 1, "unit": "명", "period": "2025", "source_ref": "T|A|2025"}])
    out = tmp_path / "calc.jsonl"
    monkeypatch.setattr(multiturn, "OUT_PARAPHRASE", tmp_path / "p.jsonl")  # never touch the real dataset
    multiturn.write([], [item], tmp_path / "a.jsonl", tmp_path / "b.jsonl", out)
    assert out.read_text(encoding="utf-8") == item.model_dump_json(exclude_defaults=True) + "\n"
    assert set(CalcItem.model_fields) == {"qid", "kind", "type", "question", "table_key", "table_keys", "table_sha256", "required_facts",
                                          "aux_facts", "allowed_reasons", "default_applied", "clarify_options", "eda_traps", "split"}
