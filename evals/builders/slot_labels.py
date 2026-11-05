"""L2 slot labels for the query golden set (T2-28, KOS-99): years and measures by the evaluation side, plus a blind
human review of 20 test items.

    python -m evals.builders.slot_labels apply      # write LABELS into evals/datasets/query_golden.jsonl
    python -m evals.builders.slot_labels review     # blank sheet for the 20 reviewed items -> slot_review.csv
    python -m evals.builders.slot_labels compare    # count the reviewer/label mismatches in the filled sheet

The labels live here, not only in the gitignored dataset, so every copy of the dataset can be brought to the same
labels. Rules (docs/research/langchain_eval_design_kosis.md §5), by the question's meaning, not the parser's words:
- years: single data years written in the question ("2005년과 2025년" -> 2005, 2025). A range, closed, open or a
  decade ("2015년부터 2020년까지", "1995년부터", "2000년대", "현재 … 2050년"), is none: it is in period_start/end, as
  ParsedQuery.to_filter reads it. Relative times (최근, 지난달, 올해) and cohorts (1985년생) are no year.
- measures: what is counted, one stem each, as table measures name it. A rate or density goes to what it counts
  (합계출산율 -> 출생, 순이동률 -> 이동); 출생 covers 출산 (the parser expands it the same way). A condition on who is
  counted is a breakdown, not the measure (고령인구, 성인 자녀 -> 인구); an average age is its event (평균 초혼연령 -> 초혼).
- dimensions: breakdowns the answer table must have, as kosis_rag.vocab keys (KOS-97). An explicit "X별" and a
  condition on who is counted both count (20대 -> 연령, 미혼 -> 혼인상태, 30대 엄마 -> 모연령, 1인 가구 -> 가구원수,
  중위 추계 -> 시나리오, a named 시군구 -> 시군구). A named 시도 is the region slot and 월별·연도별 the cycle slot, not
  breakdowns. A breakdown with no vocab key keeps its table spelling and is listed in OUT_OF_VOCAB.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter

from kosis_rag.vocab import DIMENSION_KEYS

from evals.schemas import DATASETS, QUERY_GOLDEN, QueryItem

REVIEW = DATASETS / "slot_review.csv"
REVIEW_N = 20
# Who labelled what (§5: nobody reviews their own labels): the period/cycle/region draft is the parser author's
# (data side), years/measures are the evaluation side's.
REVIEWERS = {"data": ("years", "measures"), "eval": ("period_start", "period_end", "cycle", "regions")}

LABELS: dict[str, tuple[list[int], list[str]]] = {
    "S2-P01": ([2025], ["출생"]), "S2-P02": ([2020], ["인구"]), "S2-P03": ([2023], ["가구"]),
    "S2-P04": ([], ["기혼"]), "S2-P05": ([], ["출생"]), "S2-P06": ([2024], ["혼인"]),
    "S2-P07": ([2030, 2050], ["인구"]), "S2-P08": ([2030], ["이동"]), "S2-P09": ([2024], ["외국인"]),
    "S2-P10": ([2024], ["외국인"]),
    "S2-M01": ([], ["인구"]), "S2-M02": ([], ["출생"]),
    "S2-E01": ([], ["출생"]), "S2-E02": ([], ["사망"]), "S2-E03": ([], ["초혼"]), "S2-E04": ([], ["이혼"]),
    "S2-E05": ([], ["외국인"]), "S2-E06": ([], ["가구"]), "S2-E07": ([], ["이동"]), "S2-E08": ([], ["혼인"]),
    "S2-E09": ([], ["동포"]),
    "S2-C01": ([], ["출생"]), "S2-C02": ([], ["가구"]), "S2-C03": ([], ["인구"]), "S2-C04": ([], ["혼인"]),
    "S2-C05": ([], ["이동"]), "S2-C06": ([], ["외국인"]), "S2-C07": ([], ["인구"]), "S2-C08": ([], ["출생"]),
    "S2-C09": ([], ["이동"]), "S2-C10": ([], ["출생"]), "S2-C11": ([], ["인구"]), "S2-C12": ([], ["가구"]),
    "S2-C13": ([], ["가구"]), "S2-C14": ([], ["사망"]), "S2-C15": ([], ["결혼이민"]),
    "S2-C16": ([], ["통근", "통학"]), "S2-C17": ([], ["인구"]), "S2-C18": ([], ["이동"]),
    "S2-S01": ([], ["인구"]), "S2-S02": ([2015], ["인구"]), "S2-S03": ([2040], ["인구"]), "S2-S04": ([], ["인구"]),
    "S2-S05": ([], ["출생"]), "S2-S06": ([], ["출생"]), "S2-S07": ([2010], ["가구"]), "S2-S08": ([2010], ["외국인"]),
    "S2-S09": ([2020], ["외국인"]), "S2-S10": ([], ["혼인", "출생"]), "S2-S11": ([2024], ["인구"]),
    "S2-S12": ([], ["이동"]), "S2-S13": ([], ["가구"]), "S2-S14": ([2020], ["인구"]),
    "S2-Q01": ([2020], ["인구"]), "S2-Q02": ([2023], ["인구"]), "S2-Q03": ([2015], ["인구"]),
    "S2-Q04": ([2024], ["출생"]), "S2-Q05": ([2020], ["가구"]), "S2-Q06": ([2024], ["이동"]),
    "S2-Q07": ([2020], ["외국인"]), "S2-Q08": ([2023], ["이혼"]), "S2-Q09": ([2015], ["가구"]),
    "S2-Q10": ([2020], ["가구"]), "S2-Q11": ([2024], ["결혼이민"]),
    "S2-T01": ([2025], ["사망"]), "S2-T02": ([], ["혼인"]), "S2-T03": ([], ["인구"]),
    "S2-T04": ([], ["출생"]), "S2-T05": ([2026], ["이동"]), "S2-T06": ([1970], ["인구"]),
    "S2-T07": ([], ["인구"]), "S2-T08": ([], ["외국인"]), "S2-T09": ([2023], ["출생"]),
    "S2-T10": ([2005, 2025], ["인구"]), "S2-T11": ([], ["이동"]),
    "S2-X01": ([2023], ["출생", "혼인"]), "S2-X02": ([], ["출생", "사망"]), "S2-X03": ([], ["외국인", "출생"]),
    "S2-X04": ([], ["가구"]), "S2-X05": ([], ["이동", "인구"]), "S2-X06": ([], ["혼인", "이혼", "초혼"]),
    "S2-X07": ([], ["외국인", "출입국"]),
    "S2-A01": ([], ["인구"]), "S2-A02": ([], ["인구"]), "S2-A03": ([], ["가구"]), "S2-A04": ([], ["출생"]),
    "S2-A05": ([], ["외국인"]), "S2-A06": ([], ["이동"]),
    "S2-N01": ([2024], ["실업"]), "S2-N02": ([], ["매매가격"]), "S2-N03": ([], ["물가"]), "S2-N04": ([], ["소득"]),
    "S2-N05": ([], ["진학"]), "S2-N06": ([], ["자동차"]), "S2-N07": ([], ["고용"]), "S2-N08": ([], ["소득"]),
    "S2-N09": ([], ["범죄"]), "S2-N10": ([], ["쌀"]), "S2-N11": ([], ["암"]), "S2-N12": ([], ["수출입"]),
    "S2-N13": ([], ["미세먼지"]), "S2-N14": ([], ["인구"]), "S2-N15": ([], ["가계부채"]), "S2-N16": ([2030], ["출생"]),
    "S2-N17": ([], ["가구"]), "S2-N18": ([2023], ["통근", "통학"]), "S2-N19": ([1990], ["외국인"]),
    "S2-N20": ([2027], ["출생"]), "S2-N21": ([], ["가구"]), "S2-N22": ([2015], ["동포"]), "S2-N23": ([1900], ["인구"]),
    "S2-N24": ([], ["인구"]), "S2-N25": ([], ["인구"]), "S2-N26": ([], ["가구"]), "S2-N27": ([], ["키"]),
    "S2-N28": ([], ["출생"]), "S2-N29": ([], ["가구"]),
}


DIMENSIONS: dict[str, list[str]] = {
    "S2-P03": ["가구원수"], "S2-P04": ["성", "연령", "혼인상태"], "S2-P07": ["시나리오"],
    "S2-P08": ["시나리오", "연령", "성"], "S2-P10": ["시군구", "국적"],
    "S2-E02": ["시도"], "S2-E04": ["시도"], "S2-E06": ["가구원수"], "S2-E07": ["시도"],
    "S2-C02": ["가구원수"], "S2-C03": ["연령"], "S2-C08": ["모연령"], "S2-C09": ["전입사유"], "S2-C10": ["육아휴직"],
    "S2-C11": ["가구주와의관계", "연령"], "S2-C12": ["세대구성"], "S2-C13": ["가구원수", "연령"], "S2-C14": ["연령"],
    "S2-C15": ["성"], "S2-C16": ["통근통학지"], "S2-C17": ["연령"],
    "S2-S01": ["시군구", "연령"], "S2-S02": ["읍면동", "연령"], "S2-S03": ["시도"], "S2-S04": ["시나리오"],
    "S2-S05": ["시도"], "S2-S06": ["시군구"], "S2-S07": ["시군구"], "S2-S09": ["시군구"],
    "S2-S11": ["시군구", "성", "연령"], "S2-S13": ["시도", "가구주연령"], "S2-S14": ["시군구", "성"],
    "S2-Q01": ["연령", "성", "혼인상태"], "S2-Q02": ["연령", "성"], "S2-Q03": ["시도", "교육정도", "연령"],
    "S2-Q04": ["모연령", "출산순위"], "S2-Q05": ["시군구", "연령", "가구원수"], "S2-Q06": ["연령", "전입사유"],
    "S2-Q07": ["시도", "성", "국적"], "S2-Q08": ["연령", "성"], "S2-Q09": ["맞벌이"], "S2-Q10": ["거처종류"],
    "S2-Q11": ["국적", "시도"],
    "S2-T05": ["시도"], "S2-T06": ["시도"], "S2-T07": ["성", "연령"], "S2-T10": ["연령"], "S2-T11": ["시군구"],
    "S2-X01": ["시도"], "S2-X03": ["시도"], "S2-X04": ["가구원수"], "S2-X05": ["시도", "연령"],
    "S2-N05": ["시도"], "S2-N07": ["연령"], "S2-N09": ["시도"], "S2-N13": ["행정구역"], "S2-N17": ["가구원수"],
    "S2-N24": ["혈액형"], "S2-N25": ["MBTI"], "S2-N26": ["반려동물"], "S2-N27": ["시도"], "S2-N29": ["소득분위"],
}  # every other question asks for no breakdown
OUT_OF_VOCAB = {"전입사유", "육아휴직", "맞벌이", "혈액형", "MBTI", "반려동물", "소득분위"}


def load() -> list[dict]:
    return [json.loads(line) for line in QUERY_GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]


def apply() -> None:
    rows = load()
    missing = {r["qid"] for r in rows} ^ set(LABELS)
    if missing or set(DIMENSIONS) - set(LABELS):
        sys.exit(f"labels and golden set differ: {sorted(missing | (set(DIMENSIONS) - set(LABELS)))}")
    unknown = {d for dims in DIMENSIONS.values() for d in dims} - set(DIMENSION_KEYS.values()) - OUT_OF_VOCAB
    if unknown:
        sys.exit(f"not a vocab key and not listed in OUT_OF_VOCAB: {sorted(unknown)}")
    for r in rows:
        r["years"], r["measures"] = LABELS[r["qid"]]
        r["dimensions"] = DIMENSIONS.get(r["qid"], [])
        QueryItem.model_validate(r)
    QUERY_GOLDEN.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"{QUERY_GOLDEN.name}: years·measures·dimensions {len(rows)}/{len(rows)}")


def review_sample(rows: list[dict]) -> list[dict]:
    """20 test items spread over the question types (the letter after S2-), the same every run."""
    by_type: dict[str, list[dict]] = {}
    for r in sorted((r for r in rows if r["split"] == "test"), key=lambda r: r["qid"]):
        by_type.setdefault(r["qid"][3], []).append(r)
    rng = random.Random(99)
    for group in by_type.values():
        rng.shuffle(group)
    picked, i = [], 0
    while len(picked) < REVIEW_N:  # round robin over the types
        for group in by_type.values():
            if i < len(group) and len(picked) < REVIEW_N:
                picked.append(group[i])
        i += 1
    return sorted(picked, key=lambda r: r["qid"])


def review() -> None:
    fields = [f for group in REVIEWERS.values() for f in group]
    with REVIEW.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["qid", "question", "reviewer", *fields, "note"])
        for n, r in enumerate(review_sample(load())):
            # each item is reviewed by both sides, each on the fields the other side labelled; half the items each
            reviewer = "data" if n % 2 == 0 else "eval"
            w.writerow([r["qid"], r["question"], reviewer, *([""] * len(fields)), ""])
    print(f"{REVIEW}: {REVIEW_N} items, fill only the reviewer's columns without looking at the labels "
          f"(data: {', '.join(REVIEWERS['data'])} / eval: {', '.join(REVIEWERS['eval'])}); lists as a|b, none as -")


def cell(field: str, text: str):
    text = text.strip()
    if field in ("period_start", "period_end"):  # "2024-06-30" and "202406" mean the same month
        return None if text in ("", "-") else "".join(ch for ch in text if ch.isdigit())[:6]
    if field == "cycle":
        return None if text in ("", "-") else text
    items = [] if text in ("", "-") else [t.strip() for t in text.split("|")]
    return sorted(int(t) for t in items) if field == "years" else sorted(items)


def compare() -> None:
    gold = {r["qid"]: r for r in load()}
    mismatches, graded = Counter(), Counter()
    with REVIEW.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            for f in REVIEWERS[row["reviewer"]]:
                label = gold[row["qid"]][f]
                label = sorted(label) if isinstance(label, list) else label
                graded[f] += 1
                if cell(f, row[f]) != label:
                    mismatches[f] += 1
                    print(f"{row['qid']} {f}: label {label} / review {cell(f, row[f])}  {row['note']}")
    for f in graded:
        print(f"{f}: {mismatches[f]}/{graded[f]} mismatched")
    print(f"total: {sum(mismatches.values())}/{sum(graded.values())} field labels mismatched")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("apply", "review", "compare"))
    {"apply": apply, "review": review, "compare": compare}[parser.parse_args().action]()
