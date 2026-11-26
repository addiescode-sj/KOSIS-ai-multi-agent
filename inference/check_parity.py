"""Check that the model servers give the same numbers as loading the models in process.

    python inference/check_parity.py --embed-url http://127.0.0.1:8001 --rerank-url http://127.0.0.1:8002 \
        --embed-ckpt data/models/embedder/E2s-e2.pt --rerank-ckpt data/models/reranker/RR-E2s-e2.pt

Loads both models locally (vendor/phase2 adapters.models, needs torch), sends the same questions and passages to
the servers, and prints the smallest cosine between local and remote vectors and the largest score gap, plus
whether the re-ranker order is the same. Same device and dtype on both sides -> cosine 1.0000 and gap ~0;
GPU fp16 on one side and CPU fp32 on the other -> cosine about 0.999+, gap about 1e-3, order usually the same.
Either URL can be left out to check only the other server.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "vendor/phase2/src")]

QUESTIONS = ["2023년 서울 출생아수 알려줘", "시도별 고령인구 비율 추이", "최근 5년 청년 실업률 변화",
             "1인 가구 비율이 가장 높은 지역은?", "전국 평균 월세는 얼마야"]
PASSAGES = ["인구동향조사 / 시군구별 출생아수 / 시도, 시군구 / 년 2000~2023 / 출생아수(명)",
            "장래인구추계 / 시도별 고령인구 비율 / 시도 / 년 2020~2050 / 65세 이상 인구 비율(%)",
            "경제활동인구조사 / 성·연령별 실업률 / 연령계층 / 월 2015~2024 / 실업률(%)",
            "인구총조사 / 가구원수별 가구 / 시도 / 년 2015~2023 / 1인 가구(가구)",
            "주택임대차 조사 / 지역별 평균 월세 / 시도 / 분기 2019~2024 / 월세(만원)"]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--embed-url")
    p.add_argument("--rerank-url")
    p.add_argument("--embed-ckpt", default="data/models/embedder/E2s-e2.pt")
    p.add_argument("--rerank-ckpt", default="data/models/reranker/RR-E2s-e2.pt")
    p.add_argument("--device", default=None, help="local device (default: cuda when available)")
    args = p.parse_args()

    from inference.client import RemoteEmbedder, RemoteScorer
    from kosis_rag.adapters import models
    from kosis_rag.shared.tabletext import norm_query

    ok = True
    questions = [norm_query(q) for q in QUESTIONS]
    if args.embed_url:
        remote = RemoteEmbedder(args.embed_url)
        local = models.load_embedder(args.embed_ckpt, device=args.device)
        print(f"embed  local {local.ckpt}  remote {remote.ckpt} ({remote.doc_format})")
        same = local.ckpt == remote.ckpt
        q_cos = min(cosine(local.embed_query(q), remote.embed_query(q)) for q in questions)
        d_cos = min(cosine(a, b) for a, b in zip(local.embed_documents(PASSAGES), remote.embed_documents(PASSAGES)))
        print(f"       same weights {same}  min cosine query {q_cos:.5f}  document {d_cos:.5f}")
        ok &= same and q_cos > 0.995 and d_cos > 0.995
    if args.rerank_url:
        remote_s = RemoteScorer(args.rerank_url)
        local_s = models.load_cross_encoder(args.rerank_ckpt, device=args.device)
        print(f"rerank local {local_s.ckpt}  remote {remote_s.ckpt} ({remote_s.doc_format})")
        same = local_s.ckpt == remote_s.ckpt
        gap, same_order = 0.0, 0
        for q in questions:
            a, b = local_s.score(q, PASSAGES), remote_s.score(q, PASSAGES)
            gap = max(gap, max(abs(x - y) for x, y in zip(a, b)))
            same_order += sorted(range(len(a)), key=lambda i: -a[i]) == sorted(range(len(b)), key=lambda i: -b[i])
        print(f"       same weights {same}  max score gap {gap:.5f}  same order {same_order}/{len(questions)}")
        ok &= same and gap < 0.02
    if not (args.embed_url or args.rerank_url):
        p.error("give --embed-url and/or --rerank-url")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
