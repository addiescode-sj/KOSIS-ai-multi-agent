"""Answerability data v2 (KOS-193): decide a (question spec, table) pair against the registry, without a model.

A generated question carries a condition spec (05-label-guide.md §2):
    {"time": {"years": [2015], "months": ["2015.03"], "quarters": ["2015.1"]},
     "region": {"level": "시군구", "names": ["수원시"]},
     "measure": {"items": ["전세(월세없음)"]},
     "dims": [{"class": "연령별", "labels": ["30 ~ 34세", "35 ~ 39세"]}]}
check(spec, rec, own=...) returns {"verdict": "yes" | "no" | "unknown", "slots": {slot: verdict + reason}, "hidden": bool}.
    yes      every condition is met by the table's periods / region levels and names / class axes and labels / items
    no       at least one condition is surely not met (reason per slot)
    unknown  nothing failed, but at least one condition cannot be decided by code (wording of another table, open
             vocabulary measure, sub-region level): a blind Claude judgement decides, the pair is never labelled by code
own=True means the spec was written from this very table (its labels / items are in this table's notation), so a
missing label or item is a sure "no"; for any other table a notation difference is "unknown".
hidden=True when a "no" rests on labels / items the card does not show (cards.py caps): such negatives are dropped.
Pure Python; imports cards.py (years_of, MAX_LABELS, MAX_ITEMS).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cards import MAX_ITEMS, MAX_LABELS, years_of  # noqa: E402

# ---- class axis normalization -------------------------------------------------------------------------------------

ROLES = ("가구주", "모의", "모 ", "남편", "아내", "부의", "배우자")
AXIS_RULES = (  # (axis, keywords) checked in order; a class name may hit several axes ("경제활동상태 및 직업별")
    ("scenario", ("시나리오",)),
    ("surname", ("성씨", "본관")),
    ("age", ("연령", "각세", "년생", "나이")),
    ("marital", ("혼인", "배우관계")),
    ("education", ("교육", "학력", "학교")),
    ("tenure", ("점유",)),
    ("dwelling", ("거처", "주거종류", "주택의 종류", "주택종류")),
    ("hh_type", ("세대구성", "가구구분", "유형구성", "가구유형", "다문화가구")),
    ("hh_size", ("가구원수", "가구크기", "가구의 크기", "가구규모", "가구원 수")),
    ("occupation", ("직업",)),
    ("industry", ("산업",)),
    ("econ", ("경제활동", "취업상태", "취업여부")),
    ("emp_status", ("종사상",)),
    ("nationality", ("국적",)),
    ("visa", ("체류자격",)),
    ("origin_place", ("거주지", "전입지", "전출지", "출생지", "목적지", "통근통학지", "통근·통학지", "통근.통학지")),
    ("rooms", ("사용방수", "방수")),
    ("rent", ("월세", "사글세", "전세", "보증금")),
    ("transport", ("교통수단",)),
    ("commute_time", ("소요시간",)),
    ("births", ("출생자녀", "출생아수", "출산순위", "자녀수", "자녀 수")),
    ("cause", ("사인", "사망원인")),
)
AGE_HEAD = re.compile(r"^(\d+)\s*(?:세)?\s*(?:[-~]\s*(\d+))?\s*세?\s*(이상|미만)?$")
DECADE = re.compile(r"^(\d+)0대(?:\s*(이상))?$")
TOTALS = {"계", "합계", "총계", "전체", "소계"}
REGION_LEVELS = ("전국", "시도", "시군구", "읍면동")
ALIASES = {  # old / short names of 시도 -> the label forms a table may use
    "강원도": {"강원특별자치도", "강원"}, "강원특별자치도": {"강원도", "강원"},
    "전라북도": {"전북특별자치도", "전북"}, "전북특별자치도": {"전라북도", "전북"},
    "제주도": {"제주특별자치도", "제주"}, "제주특별자치도": {"제주도", "제주"},
    "세종특별자치시": {"세종시", "세종"}, "세종시": {"세종특별자치시", "세종"},
}


def _squash(s: str) -> str:
    return re.sub(r"[\s_·.,()\[\]]", "", str(s))


def axes_of(name: str) -> set[str]:
    """Axis keys of one class name: role prefix (가구주 / 모 / 남편 / 아내 ...) + axis, e.g. "가구주의 연령별" -> {"가구주:age"}.
    A name hit by no rule keeps its squashed form (minus 별) as its own axis."""
    s = name.strip()
    role = next((r.strip() for r in ROLES if s.startswith(r) or s.startswith(r.strip() + " ")), "")
    role = role.replace("의", "") if role else ""
    hits = {axis for axis, kws in AXIS_RULES for k in kws if k in s}
    bare = re.sub(r"성씨|성비|구성|성인|성격|성과|형성|작성|여성|남성|성장|특성|속성", "", s)
    if re.search(r"(^|[\s(,/·])성(별)?($|[\s),/·]|\s*및)", bare):
        hits.add("sex")
    if not hits:
        hits = {_squash(s).rstrip("별")}
    return {f"{role}:{h}" if role else h for h in hits}


def table_axes(rec: dict) -> dict[str, list[dict]]:
    """axis -> the table's non-region classes on it."""
    out: dict[str, list[dict]] = {}
    for c in rec.get("classes") or []:
        if c.get("levels") or REGION_NAME.search(c.get("name", "")):
            continue
        for a in axes_of(c.get("name", "")):
            out.setdefault(a, []).append(c)
    return out


def items_have_sex(rec: dict) -> bool:
    return any(("남자" in i or "여자" in i or "남성" in i or "여성" in i) for i in rec.get("items") or [])


# ---- age intervals ------------------------------------------------------------------------------------------------

def age_interval(label: str) -> tuple[int, int] | None:
    """"30 ~ 34세" -> (30, 34), "85세 이상" -> (85, 200), "15세미만" -> (0, 14), "20대" -> (20, 29), "5세" -> (5, 5)."""
    s = str(label).replace(" ", "")
    m = DECADE.match(s)
    if m:
        lo = int(m.group(1)) * 10
        return (lo, 200) if m.group(2) else (lo, lo + 9)
    m = AGE_HEAD.match(s.replace("세이상", "세이상").replace("세미만", "세미만"))
    if not m:
        return None
    a = int(m.group(1))
    b = int(m.group(2)) if m.group(2) else None
    if m.group(3) == "이상":
        return (a, 200)
    if m.group(3) == "미만":
        return (0, a - 1)
    return (a, b if b is not None else a)


def age_covered(asked: str, labels: list[str]) -> bool | None:
    """Can the asked age band be built from the table's bands (exact tiling by disjoint bands inside it)?
    None when the asked label is not an age band."""
    want = age_interval(asked)
    if want is None:
        return None
    bands = sorted({iv for iv in (age_interval(x) for x in labels) if iv is not None})
    if want in bands:
        return True
    inside = [b for b in bands if want[0] <= b[0] and b[1] <= want[1]]
    best = {want[0] - 1: True}  # dynamic cover: reachable end points
    for lo, hi in inside:
        for end in sorted(best):
            if end == lo - 1:
                best[hi] = True
    return want[1] in best


# ---- the check ----------------------------------------------------------------------------------------------------

REGION_NAME = re.compile(r"행정구역|시도별|시군구별|읍면동|동읍면|대도시별|^도별")


def _region_class(rec: dict) -> dict | None:
    """The region class: one with KOSIS level counts, else one whose name says it is a region (levels from the name)."""
    c = next((c for c in rec.get("classes") or [] if c.get("levels")), None)
    if c is not None:
        return c
    c = next((c for c in rec.get("classes") or [] if REGION_NAME.search(c.get("name", ""))), None)
    if c is None:
        return None
    name = c["name"]
    lv = {"전국": 1}
    for key, level in (("시도", "시도"), ("도별", "시도"), ("시군구", "시군구"), ("읍면동", "읍면동"), ("동읍면", "읍면동")):
        if key in name:
            lv[level] = 1
    if len(lv) == 1:  # "행정구역별" alone: the level is not known
        lv["하위 지역"] = 1
    return {**c, "levels": lv}


def _name_in(name: str, labels: set[str]) -> bool:
    if name in labels:
        return True
    return bool(ALIASES.get(name, set()) & labels)


def check_time(t: dict, rec: dict) -> tuple[str, str]:
    ys = set(years_of(rec.get("periods", [])))
    months = {str(p) for p in rec.get("periods", []) if len(str(p)) == 6}
    for y in t.get("years") or []:
        if int(y) not in ys:
            return "no", f"year {y} not in table"
    for m in t.get("months") or []:
        code = str(m).replace(".", "").replace("-", "")
        if code not in months:
            return "no", f"month {m} not in table"
    for q in t.get("quarters") or []:
        y, n = str(q).split(".")
        if "Q" in (rec.get("prd_se") or []) and int(y) in ys:
            continue
        if all(f"{y}{int(n) * 3 - k:02d}" in months for k in (2, 1, 0)):
            continue  # three months add up to the quarter
        return "no", f"quarter {q} not in table"
    return "yes", ""


def check_region(r: dict, rec: dict) -> tuple[str, str]:
    level, names = r.get("level"), r.get("names") or []
    rc = _region_class(rec)
    if not level and not names:
        return "yes", ""
    if rc is None:
        return ("yes", "") if level in (None, "전국") and not names else ("no", "table has no region class")
    levels = rc.get("levels") or {}
    labels = set(rc.get("labels") or [])
    if level and level != "전국" and level not in levels:
        if "하위 지역" in levels and level in ("시군구", "읍면동"):
            return "unknown", "읍면동 vs 하위 지역"
        return "no", f"level {level} not in table"
    if level == "전국" and "전국" not in labels and "전국" not in levels:
        return "no", "no national row"
    complete = len(labels) >= int(rc.get("n_labels") or len(labels))
    for n in names:
        if not _name_in(n, labels):
            return ("no", f"region {n} not in table") if complete else ("unknown", f"region {n} not listed")
    return "yes", ""


def check_dims(dims: list[dict], rec: dict, own: bool) -> tuple[str, str, bool]:
    tax = table_axes(rec)
    worst, why, hidden = "yes", "", False
    for d in dims or []:
        want = axes_of(d.get("class", ""))
        classes = [c for a in want for c in tax.get(a, [])]
        labels = [x for c in classes for x in c.get("labels") or []]
        if not classes:
            if want & {"sex"} and items_have_sex(rec):
                labels = list(rec.get("items") or [])
            else:
                return "no", f"no {sorted(want)} axis", False
        for lab in d.get("labels") or []:
            if lab in TOTALS or lab in labels or any(_squash(lab) == _squash(x) for x in labels):
                continue
            if any(_squash(lab) in _squash(x) for x in labels) and (want & {"sex"}):
                continue  # 남자 inside 남자_계 / 내국인_남자
            cov = age_covered(lab, labels) if (any("age" in a for a in want)) else None
            if cov is True:
                continue
            shown_cut = any(len(c.get("labels") or []) > MAX_LABELS for c in classes)
            if cov is False or own:
                return "no", f"{d.get('class')}: {lab} not in table", shown_cut
            worst, why = "unknown", f"{d.get('class')}: {lab} wording"
    return worst, why, hidden


UNIT_TAIL = re.compile(r"\s*\((?:[^()\d]{1,8})\)\s*$")  # "(명)", "(천원)", "(%)"; keeps "(60세 이상)"


def check_measure(m: dict, rec: dict, own: bool) -> tuple[str, str, bool]:
    items = rec.get("items") or []
    sq = {_squash(UNIT_TAIL.sub("", i)): i for i in items}
    for it in m.get("items") or []:
        if it in items or _squash(UNIT_TAIL.sub("", it)) in sq:
            continue
        if own:
            return "no", f"item {it} not in table", len(items) > MAX_ITEMS
        return "unknown", f"item {it} wording", False
    return "yes", "", False


def check(spec: dict, rec: dict, own: bool = False) -> dict:
    slots, hidden = {}, False
    slots["time"] = check_time(spec.get("time") or {}, rec)
    slots["region"] = check_region(spec.get("region") or {}, rec)
    v, why, h = check_dims(spec.get("dims") or [], rec, own)
    slots["dims"], hidden = (v, why), hidden or (v == "no" and h)
    v, why, h = check_measure(spec.get("measure") or {}, rec, own)
    slots["measure"], hidden = (v, why), hidden or (v == "no" and h)
    vs = [s[0] for s in slots.values()]
    verdict = "no" if "no" in vs else ("unknown" if "unknown" in vs else "yes")
    return {"verdict": verdict, "slots": {k: f"{v}:{w}" if w else v for k, (v, w) in slots.items()},
            "hidden": hidden}


def selftest() -> None:
    assert age_interval("30 ~ 34세") == (30, 34) and age_interval("85세 이상") == (85, 200)
    assert age_interval("15세미만") == (0, 14) and age_interval("20대") == (20, 29) and age_interval("5세") == (5, 5)
    assert age_interval("0~4") == (0, 4) and age_interval("계") is None
    five = [f"{a} - {a + 4}세" for a in range(0, 85, 5)] + ["85세 이상"]
    assert age_covered("30대", five) is True and age_covered("30 ~ 39세", five) is True
    assert age_covered("20 ~ 24세", ["15 - 24세", "25 - 34세"]) is False and age_covered("100세 이상", five) is False
    assert age_covered("부부", five) is None
    assert axes_of("가구주의 연령별") == {"가구주:age"} and axes_of("성별") == {"sex"}
    assert axes_of("경제활동상태 및 직업별") == {"econ", "occupation"} and "sex" in axes_of("성 및 연령별")
    assert "sex" not in axes_of("세대구성별") and axes_of("혼인상태") == {"marital"}
    rec = {"periods": ["2015", "2020"], "prd_se": ["F"], "items": ["가구_계", "전세(월세없음)", "자기집"],
           "classes": [{"name": "행정구역별(시군구)", "labels": ["전국", "서울특별시", "강원특별자치도", "수원시"],
                        "n_labels": 4, "levels": {"전국": 1, "시도": 2, "시군구": 1}},
                       {"name": "가구주의 연령별", "labels": ["계"] + five}]}
    spec = {"time": {"years": [2015]}, "region": {"level": "시군구", "names": ["수원시"]},
            "measure": {"items": ["전세(월세없음)"]}, "dims": [{"class": "가구주 연령별", "labels": ["30대"]}]}
    assert check(spec, rec, own=True)["verdict"] == "yes", check(spec, rec, own=True)
    assert check({**spec, "time": {"years": [2017]}}, rec)["verdict"] == "no"
    assert check({**spec, "region": {"level": "읍면동"}}, rec)["verdict"] == "no"
    assert check({**spec, "region": {"level": "시도", "names": ["강원도"]}}, rec)["verdict"] == "yes"  # alias
    assert check({**spec, "dims": [{"class": "교육정도별", "labels": ["대졸"]}]}, rec)["verdict"] == "no"
    assert check({**spec, "dims": [{"class": "연령별", "labels": ["100세 이상"]}]}, rec)["verdict"] == "no"
    assert check({**spec, "measure": {"items": ["월세 평균"]}}, rec, own=True)["verdict"] == "no"
    assert check({**spec, "measure": {"items": ["월세 평균"]}}, rec, own=False)["verdict"] == "unknown"
    assert check({**spec, "time": {"years": [2015], "months": ["2015.03"]}}, rec)["verdict"] == "no"
    mrec = {**rec, "periods": ["2015", "201501", "201502", "201503"], "prd_se": ["M", "Y"]}
    assert check({**spec, "time": {"quarters": ["2015.1"]}}, mrec)["verdict"] == "yes"
    print("selftest ok")


if __name__ == "__main__":
    selftest()
