"""L0: metadata quality over the table registry (fill rates, unmapped fields, Chroma load check)."""

from __future__ import annotations

import json

from evals.schemas import DATASETS, ROOT, load_items

METADATA_SAMPLE = DATASETS / "metadata_sample.jsonl"  # L0 accuracy sample, labelled by the evaluation side (§3.3)


def run(args) -> tuple[list[dict], dict, str]:
    """L0 metadata quality over the registry; the load check runs only with --retriever chroma (needs the server)."""
    from evals.evaluators import metadata
    from evals.runners import retrieval as runner

    records = {r.info.key: r for r in runner.load_records()}
    gold = set().union(*(i.gold_keys for i in load_items(args.golden)))
    sample = ({json.loads(line)["key"]: json.loads(line) for line in METADATA_SAMPLE.read_text(encoding="utf-8").splitlines()
               if line.strip()} if METADATA_SAMPLE.exists() else None)
    loaded = None
    if args.retriever == "chroma":
        loaded = set(runner.connect_chroma().collection.get(include=[])["ids"])
    s = metadata.score(records, gold, sample, loaded)
    rows = [{"key": k, **metadata.fields(r), "latency_ms": 0.0} for k, r in records.items()]
    lines = ["| 필드 | 채움률 (전체) | 채움률 (골든 정답 표) |", "| --- | --- | --- |"]
    lines += [f"| {f} | {s['all']['fill'][f]:.3f} | {s['gold']['fill'][f]:.3f} |" for f in metadata.FIELDS]
    fmt = lambda v: "—" if v is None else f"{v:.3f}"
    report = "\n".join([
        f"# L0 metadata quality — {args.stage}", "",
        f"- 표 {s['n_tables']}개, 골든 정답 표 {s['n_gold']}개 · 필드는 필터가 쓰는 함수로 읽음 (kosis_rag.shared.fields / filters / roles)", "",
        *lines, "",
        f"- regions \"해당 없음\"(행정구역 분류 없는 표): 전체 {s['all']['regions_none']:.3f} · 골든 {s['gold']['regions_none']:.3f}",
        f"- measure 미매핑(이름이 세는 대상을 말하지 않음·기타): 전체 {s['all']['measure_unmapped']:.3f} · 골든 {s['gold']['measure_unmapped']:.3f}",
        f"- dimension 미매핑(질문 분류 어휘로 고를 수 없는 분류 이름): 전체 {fmt(s['all']['dimension_unmapped'])}"
        f" ({s['all']['dimension_names']}개) · 골든 {fmt(s['gold']['dimension_unmapped'])} ({s['gold']['dimension_names']}개)",
        "- 정규화 정확도: " + (", ".join(f"{k} {fmt(v)}" for k, v in s["accuracy"].items()) + f" (표본 {s['n_sample']})"
                         if s["accuracy"] else f"표본 대기 ({METADATA_SAMPLE.relative_to(ROOT)} 없음)"),
        "- 적재 일치: " + (("일치" if s["load"]["match"] else f"불일치 — 빠짐 {s['load']['missing']}, 남음 {s['load']['extra']}")
                        + f" ({s['load']['loaded']}개)" if "load" in s else "확인 안 함 (--retriever chroma 로 확인)"),
    ]) + "\n"
    return rows, s, report


def owners(rows: list[dict], summary: dict) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for scope in ("gold",):
        s = summary.get(scope) or {}
        if s.get("measure_unmapped"):
            found.setdefault("data", []).append(f"정답 표 measure 미매핑 {s['measure_unmapped']:.1%}")
        if s.get("dimension_unmapped"):
            found.setdefault("data", []).append(f"정답 표 dimension 미매핑 {s['dimension_unmapped']:.1%}")
    if (summary.get("load") or {}).get("match") is False:
        found.setdefault("data", []).append("컬렉션 적재 키 불일치")
    return found


def target(args, op: dict) -> str:
    return "registry"
