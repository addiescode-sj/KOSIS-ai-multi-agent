"""Answerability data v2: G0 gate on a generated batch (04-dataset-plan.md §8): blind-judge agreement and realism.

    python score_g0.py --dir DIR [--review 50]
reads DIR/checked.jsonl + DIR/judge_out_*.jsonl, writes DIR/g0_report.json, DIR/labeled.jsonl (kept rows with judge
verdict and final label: 1 / 0 / None when judge and spec disagree or judge says ambiguous) and DIR/review_50.md
(rows for the user: disagreements first, then a stratified sample by kind).
Gate: code pass >= 90%, agreement >= 95% for base, ctrl, no_item each, realistic >= 80%, 2 no_item kept in >= 70% tables.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

WANT = {"base": "yes", "ctrl": "yes", "no_item": "no", "oos": "no"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--review", type=int, default=50)
    ap.add_argument("--seed", type=int, default=20)
    a = ap.parse_args()
    rows = [json.loads(x) for x in (a.dir / "checked.jsonl").open(encoding="utf-8") if x.strip()]
    judged = {}
    for p in sorted(a.dir.glob("judge_out_*.jsonl")):
        for x in p.open(encoding="utf-8"):
            if x.strip():
                j = json.loads(x)
                judged[j["jid"]] = j
    title = {}
    for x in (a.dir / "judge_in.jsonl").open(encoding="utf-8"):
        if x.strip():
            j = json.loads(x)
            title[j["jid"]] = j["card"].split("\n")[0].replace("통계표: ", "")
    kept = [r for r in rows if r["keep"]]
    agree, amb, real = defaultdict(Counter), Counter(), Counter()
    out, hid = [], 0
    for r in kept:
        j = judged.get(r["id"])
        if j is None:
            continue
        want = WANT[r["kind"]]
        v = j.get("verdict")
        if (r.get("check") or {}).get("hidden_pos") and v == "ambiguous":
            out.append({**r, "judge": j, "label": 1})  # value exists (code) but the card hides it: not a disagreement
            hid += 1
            continue
        agree[r["kind"]][v == want] += 1
        amb[r["kind"]] += v == "ambiguous"
        real[bool(j.get("realistic"))] += 1
        label = (1 if want == "yes" else 0) if v == want else None
        out.append({**r, "judge": j, "label": label})
    tables = {r["table"] for r in rows if r["kind"] != "oos"}
    ni = Counter(r["table"] for r in out if r["kind"] == "no_item" and r["label"] == 0)
    code_pass = len(kept) / max(1, len(rows))
    rate = {k: round(c[True] / max(1, sum(c.values())), 3) for k, c in agree.items()}
    realistic = round(real[True] / max(1, sum(real.values())), 3)
    ni_ok = sum(ni[t] >= 2 for t in tables) / max(1, len(tables))
    gate = {"code_pass>=0.90": code_pass >= 0.9,
            "agree>=0.95 (base,ctrl,no_item)": all(rate.get(k, 0) >= 0.95 for k in ("base", "ctrl", "no_item")),
            "realistic>=0.80": realistic >= 0.8, "2 no_item in >=70% tables": ni_ok >= 0.7}
    slots = Counter((r["kind"], r["judge"].get("slot")) for r in out if r["label"] is None)
    mtype = Counter((r.get("mutation") or {}).get("type") for r in out if r["kind"] == "no_item" and r["label"] is None)
    rep = {"rows": len(rows), "kept": len(kept), "judged": len(out), "hidden_pos_ambiguous": hid, "missing_judge": len(kept) - len(out),
           "code_pass": round(code_pass, 3), "agree": rate, "ambiguous": dict(amb), "realistic": realistic,
           "tables_2_no_item_after_judge": round(ni_ok, 3), "disagree_by_kind_slot": [[*k, v] for k, v in slots.items()],
           "no_item_disagree_by_type": dict(mtype), "unrealistic_by_kind": dict(Counter(
               r["kind"] for r in out if not r["judge"].get("realistic"))), "gate": gate, "pass": all(gate.values())}
    (a.dir / "g0_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    with (a.dir / "labeled.jsonl").open("w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    rng = random.Random(a.seed)
    dis = [r for r in out if r["label"] is None]
    rng.shuffle(dis)
    pick = dis[: min(len(dis), a.review // 4)]
    quota = {"base": 0.34, "no_item": 0.33, "ctrl": 0.25, "oos": 0.08}
    left = a.review - len(pick)
    for k, share in quota.items():
        pool = [r for r in out if r["kind"] == k and r not in pick]
        rng.shuffle(pool)
        pick += pool[: round(left * share)]
    lines = ["# 파일럿 확인용 문항", "", "각 문항: 질문 / 대상 표 / 의도한 라벨(생성 기준) / 판정자 결과. "
             "'틀림'이나 '부자연스러움'이 보이면 번호만 알려 주시면 됨.", ""]
    for i, r in enumerate(pick[: a.review], 1):
        j = r["judge"]
        mt = r.get("mutation") or {}
        intent = {"base": "답 있음", "ctrl": "답 있음 (대조군)", "no_item": f"답 없음 (변형 {mt.get('type', '')}: "
                  f"{mt.get('from', '')} → {mt.get('to', '')})", "oos": "답 없음 (범위 밖)"}[r["kind"]]
        flag = "" if r["label"] is not None else " **[불일치]**"
        lines += [f"{i}. {r['q']}{flag}", f"   - 표: {title.get(r['id'], '')} (`{r['table']}`)", f"   - 의도: {intent}",
                  f"   - 판정: {j.get('verdict')} ({j.get('slot')}) {j.get('reason', '')}"
                  + ("" if j.get("realistic") else " · 부자연스럽다고 봄"), ""]
    (a.dir / "review_50.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
