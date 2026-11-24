"""Check the e2e settings before a long run (11/20): the 11/19 e2e run lost every stage 2 call to a wrong STAGE2.

Reads .env the way the e2e entry point does (shell values first) and prints what stage 2 and the data services will
use. Keys are never printed, only whether one is set. One GET to the stage 2 server's /models and one to Chroma.
Exit code 1 when something would make the run invalid.

    python -m scripts.qwen38.preflight_e2e
"""

from __future__ import annotations

import json
import sys
import urllib.request

from kosis_agent.envfile import load_env_file


def get(url: str, key: str | None = None) -> tuple[int | None, str]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001 - any connection failure is a finding here
        return None, f"{type(e).__name__}: {e}"


def main() -> int:
    loaded = load_env_file()
    from backend.app import config

    problems: list[str] = []
    cfg = config.agent_config()
    name = cfg.options.stage2
    print(f".env: {loaded}")
    scope = getattr(cfg.options, "cond_scope", None)
    print(f"STAGE2={name}  COND_FILTER={cfg.options.cond_filter}  ANSWER_K={cfg.options.answer_k}  COND_SCOPE={scope}  "
          f"(최고 설정 = years: .env COND_SCOPE=years 또는 --agent scripts.answerability.e2e_condyears:build)")
    try:
        import torch

        print(f"torch {torch.__version__}  cuda={torch.cuda.is_available()}")
    except ImportError:
        print("torch 없음")
    llm = config.stage2_llm(name)
    if llm is None:
        problems.append(f"2차가 꺼짐(STAGE2={name}). Gemma/Qwen 비교라면 STAGE2=gpt")
    else:
        key = llm.api_key.get_secret_value() if hasattr(llm.api_key, "get_secret_value") else llm.api_key
        print(f"2차 provider={llm.provider} model={llm.model} base_url={llm.base_url} "
              f"key={'있음' if key else '없음'} max_tokens={getattr(llm, 'max_tokens', None)}")
        if name in ("clova", "hcx"):
            problems.append(f"STAGE2={name}: 11/19에 이 설정으로 90건 전부 401. 자체 서버 2차라면 STAGE2=gpt")
        elif not llm.base_url:
            problems.append("STAGE2=gpt인데 STAGE2_BASE_URL 없음 → 실제 OpenAI로 감")
        else:
            status, body = get(llm.base_url.rstrip("/") + "/models", key)
            print(f"2차 서버 /models: {status}")
            if status != 200:
                problems.append(f"2차 서버 응답 {status} {body[:120]}")
            else:
                served = [m.get("id") for m in json.loads(body).get("data", [])]
                print(f"  서빙 모델: {served}")
                if llm.model not in served:
                    problems.append(f"STAGE2_MODEL {llm.model!r}이 서버 목록에 없음")
    chroma = cfg.spec.chroma
    if chroma is not None:
        url = f"http://{chroma.host}:{chroma.port}"
        status = next((s for s, _ in (get(url + p) for p in ("/api/v2/heartbeat", "/api/v1/heartbeat")) if s == 200), None)
        print(f"Chroma {url} collection={chroma.collection}: {'OK' if status == 200 else '응답 없음'}")
        if status != 200:
            problems.append(f"Chroma {url} 응답 없음")
        if not chroma.collection:
            problems.append("CHROMA_COLLECTION 없음 → e2e 검색 단계에서 'no collection' 오류. 목록: python -m kosis_rag_build.cli chroma --list --chroma-host localhost --chroma-port 8001")
    print(f"STAT_PROVIDER={config._env('STAT_PROVIDER') or 'hcx'}")
    # T3-51: 2차 프롬프트 (v4 = 지금 최고 설정, v4m = 멀티표 판단 together 추가)
    print(f"STAGE2_PROMPT={config._env('STAGE2_PROMPT') or 'v4'}")
    from kosis_rag.rerank import selectors

    print(f"  2차 selector: {type(selectors.LLM_RERANKERS[name]({}, llm)).__name__}" if llm is not None else "")
    for p in problems:
        print(f"[문제] {p}")
    print("OK: 실행해도 됨" if not problems else "멈춤: 위 문제를 고친 뒤 다시 확인")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
