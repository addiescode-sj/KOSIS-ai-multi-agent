r"""KOSIS 챗봇 데모: 서비스와 같은 에이전트 그래프(실전 2 검색 -> 실전 1 수치 요약 -> Vega-Lite 차트)를
브라우저 채팅 화면 하나로 띄운다. 데모용이며 레포의 기존 파일은 바꾸지 않는다.

실행 (레포 루트에서, Anaconda Prompt):
    python <이 폴더>\serve.py --collection kosis_mixft_E2s_e2 --embed-ckpt data\models\embedder\E2s-e2.pt ^
        --rerank-ckpt data\models\reranker\RR-E2s-e2.pt --hq-build "" --stage2 gpt --interval 5
    -> http://localhost:8010 (자동으로 열림)

설정 우선순위는 run_eval과 같다: 인자 > 셸(set X=값) > <레포>/.env > 기본값. 검색 인자 이름도 run_eval과 같다.
그래프는 backend/app/main.py build_agent()와 같게 만들고 한 가지만 다르다: 행 수가 --max-rows(기본 100만)를 넘는
표는 실전 1에 넘기지 않고 오류로 끝낸다(evals/trace.py의 E2E_MAX_TABLE_ROWS와 같은 보호, 0이면 끔).
웹 서버는 표준 라이브러리 http.server라서 그래프가 쓰는 패키지 외에 더 설치할 것이 없다.
화면의 그래프는 vega-embed(CDN)로 그린다.

    GET  /       index.html (같은 폴더)
    GET  /info   지금 설정 (키는 없음)
    POST /chat   {question, thread_id} -> SSE: step* (검색 노드·실전 1이 끝날 때마다), result | error
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

PAGE = Path(__file__).resolve().with_name("index.html")
MAX_TABLE_ROWS = 1_000_000  # evals/trace.py와 같은 기본 한도
ERROR_CHARS = 600  # 화면·콘솔에 보일 오류 문장 길이 (SchemaError 문장은 수 MB까지 나온다)

# 인자 -> 환경변수 (backend/app/config.py가 읽는 이름). 검색 인자는 evals/run_eval.py SEARCH_ARGS와 같다.
SETTINGS = {
    "chroma_host": ("CHROMA_HOST", "Chroma 호스트 (기본 localhost)"),
    "chroma_port": ("CHROMA_PORT", "Chroma 포트 (기본 8001)"),
    "collection": ("CHROMA_COLLECTION", "dense 컬렉션, 예: kosis_mixft_E2s_e2"),
    "embed_ckpt": ("EMBED_CKPT", "질의 임베더 체크포인트 .pt 또는 base"),
    "rerank_ckpt": ("RERANK_CKPT", "1차 리랭커 체크포인트 .pt 또는 base"),
    "text_format": ("TEXT_FORMAT", "컬렉션의 표 텍스트 형식"),
    "rerank_mode": ("RERANK_MODE", "1차 리랭커: blend | score"),
    "answer_k": ("ANSWER_K", "gate에 넘기는 후보 수"),
    "hq_build": ("HQ_BUILD", '질문 인덱스 build id ("" = 끔)'),
    "hq_weight": ("HQ_WEIGHT", "질문 목록의 RRF 가중치"),
    "stage2": ("STAGE2", "2차 리랭커 LLM: clova | hcx | gpt | none (e2e-best는 gpt)"),
    "stage2_model": ("STAGE2_MODEL", "2차 모델 이름"),
    "stage2_base_url": ("STAGE2_BASE_URL", "2차 gpt: OpenAI 호환 서버 주소 (예: https://.../v1)"),
    "stage2_api_key": ("STAGE2_API_KEY", "그 서버의 키 (기본: OPENAI_API_KEY / HCX_API_KEY)"),
    "hcx_thinking": ("HCX_THINKING", "2차 hcx: none | low"),
    "llm": ("STAT_PROVIDER", "실전 1 모델: hcx | mock (run_eval --llm)"),
    "interval": ("STAT_RETRY_INTERVAL", "실전 1 HCX 429 재시도 대기 초 (run_eval --interval)"),
    "alt_tables": ("ALT_TABLE_BUDGET", "첫 표가 표 불일치로 거절됐을 때 더 시도할 후보 표 수 (기본 0)"),
}


class TableTooLarge(RuntimeError):
    """표의 행 수가 --max-rows를 넘어 실전 1에 넘기지 않음."""


class ClientGone(Exception):
    """브라우저가 응답을 다 받기 전에 연결을 닫음."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="KOSIS 챗봇 데모 (검색 -> 수치 요약 -> 그래프)")
    p.add_argument("--repo", help="KOSIS-ai-multi-agent 레포 루트 (기본: 현재 폴더, 아니면 이 파일의 상위 폴더)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8010)
    p.add_argument("--max-rows", type=int, default=None,
                   help=f"이 행 수를 넘는 표는 열지 않음 (기본 E2E_MAX_TABLE_ROWS 또는 {MAX_TABLE_ROWS:,}; 0 = 끔)")
    p.add_argument("--no-env-file", action="store_true", help="<레포>/.env를 읽지 않음")
    p.add_argument("--no-browser", action="store_true", help="브라우저를 자동으로 열지 않음")
    for field, (name, text) in SETTINGS.items():
        p.add_argument("--" + field.replace("_", "-"), default=None, help=f"{name}: {text}")
    return p.parse_args(argv)


def find_root(given: str | None) -> Path:
    """레포 루트: --repo, 현재 폴더, 이 파일의 상위 폴더 순서로 src/kosis_agent가 있는 곳."""
    candidates = [Path(given)] if given else [Path.cwd(), *Path(__file__).resolve().parents]
    for folder in candidates:
        if (folder / "src" / "kosis_agent").is_dir() and (folder / "backend" / "app" / "config.py").is_file():
            return folder.resolve()
    raise SystemExit("KOSIS-ai-multi-agent 레포를 찾지 못했습니다. 레포 루트에서 실행하거나 --repo <경로>를 주세요.")


def bootstrap(root: Path) -> None:
    """import 경로를 pytest.ini / evals/__init__.py와 같게 잡고, 상대 경로(체크포인트)가 e2e 실행 때와 같게 읽히도록
    레포 루트로 옮긴다."""
    for folder in (root / "vendor" / "phase1" / "src", root / "vendor" / "phase1", root / "vendor" / "phase2" / "src",
                   root / "src", root):
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
    os.chdir(root)


def apply_settings(args: argparse.Namespace) -> Path | None:
    """<레포>/.env를 os.environ에 올린 뒤(셸 값이 먼저) 인자로 준 값을 그 위에 덮는다. run_eval apply_settings와 같다."""
    import kosis_agent  # noqa: F401  (KOSIS_WORKSPACE 기본값 = 레포 루트. 실전 1·2 모듈보다 먼저 import)
    from kosis_agent.envfile import load_env_file

    loaded = None if args.no_env_file else load_env_file()
    for field, (name, _) in SETTINGS.items():
        if (value := getattr(args, field)) is not None:
            os.environ[name] = str(value)
    return loaded


def guarded_summarize(max_rows: int) -> Callable[..., Any]:
    """실전 1 summarize_stat_run 앞에 행 수 확인을 둔 함수. 행 수는 Parquet footer에서 읽는다(표를 열지 않음).
    LookupError(표 키가 tables.csv에 없음)는 그대로 올려, summarize_stat 툴이 전처럼 거절 답으로 바꾸게 한다."""
    from kosis_chat import api

    def summarize(question: str, table_key: str, **kwargs: Any):
        if max_rows:
            import pyarrow.parquet as pq

            rows = pq.ParquetFile(api.table_path(table_key)).metadata.num_rows
            if rows > max_rows:
                raise TableTooLarge(f"{table_key}: {rows:,}행 > 한도 {max_rows:,}행 (--max-rows로 조정, 0이면 끔)")
        return api.summarize_stat_run(question, table_key, **kwargs)

    return summarize


def build_agent(max_rows: int):
    """(설정, 그래프). backend/app/main.py build_agent()와 같고 summarize만 행 수 확인을 둔 것으로 바꾼다."""
    from backend.app.config import agent_config
    from kosis_agent.graph.main_graph import build_graph
    from kosis_agent.graph.state import new_checkpointer
    from kosis_rag.api import SearchModule

    cfg = agent_config()
    search = SearchModule(cfg.spec, cfg.options, cfg.stage2_llm)
    graph = build_graph(search, cfg.stat_llm, retry_interval=cfg.stat_retry_interval,
                        summarize=guarded_summarize(max_rows), checkpointer=new_checkpointer(),
                        alt_table_budget=cfg.alt_table_budget)
    return cfg, graph


def describe(cfg: Any, max_rows: int) -> dict:
    """화면 머리말과 콘솔에 보일 설정. 키는 넣지 않는다."""
    stage2 = cfg.stage2_llm
    return {
        "collection": cfg.spec.chroma.collection,
        "chroma": f"{cfg.spec.chroma.host}:{cfg.spec.chroma.port}",
        "embed_ckpt": cfg.spec.embed_ckpt,
        "rerank_ckpt": cfg.spec.rerank_ckpt,
        "hq_build": cfg.spec.hq_build,
        "stage2": cfg.options.stage2,
        "stage2_model": getattr(stage2, "model", None),
        "stage2_base_url": getattr(stage2, "base_url", None),
        "stat": f"{cfg.stat_llm.provider}:{cfg.stat_llm.model}",
        "alt_table_budget": cfg.alt_table_budget,
        "max_rows": max_rows,
    }


def jsonable(value: Any) -> Any:
    """JSON으로 보낼 수 있는 값. Pydantic 모델은 dict로, NaN·inf는 null로(브라우저 JSON.parse가 못 읽음)."""
    if hasattr(value, "model_dump"):
        try:
            value = value.model_dump(mode="json")
        except Exception:  # noqa: BLE001 - JSON 모드로 못 바꾸는 값이 섞였으면 아래에서 하나씩 바꾼다
            value = value.model_dump()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if hasattr(value, "item"):  # numpy 스칼라
        try:
            return jsonable(value.item())
        except Exception:  # noqa: BLE001
            pass
    return str(value)


def dumps(value: Any) -> str:
    return json.dumps(jsonable(value), ensure_ascii=False, allow_nan=False)


def short(text: str) -> str:
    return text if len(text) <= ERROR_CHARS else f"{text[:ERROR_CHARS]} ... (전체 {len(text):,}자)"


class Handler(BaseHTTPRequestHandler):
    graph: Any = None  # main()이 채운다
    info: dict = {}
    result_of: Callable[[dict], Any] = staticmethod(lambda state: state)  # main()이 chat_result로 바꾼다
    lock = threading.Lock()  # 한 번에 한 질문 (모델과 실전 1 캐시를 동시에 쓰지 않음)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 기본 접근 로그는 끄고 질문·결과만 찍는다
        pass

    def send_body(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):  # 매번 읽는다: 화면만 고쳤으면 새로고침으로 반영
            self.send_body(200, "text/html; charset=utf-8", PAGE.read_bytes())
        elif path == "/info":
            self.send_body(200, "application/json; charset=utf-8", dumps(self.info).encode("utf-8"))
        else:
            self.send_body(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/chat":
            self.send_body(404, "text/plain; charset=utf-8", b"not found")
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            question = str(body.get("question") or "").strip()
            thread_id = str(body.get("thread_id") or uuid.uuid4().hex)
        except (ValueError, AttributeError):
            question, thread_id = "", ""
        if not question:
            self.send_body(400, "text/plain; charset=utf-8", "question이 비어 있습니다".encode("utf-8"))
            return
        # HTTP/1.0 응답: 길이 없이 보내고 끝나면 연결을 닫는다(브라우저 fetch는 닫힐 때까지 읽는다)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.answer(question, thread_id)
        except ClientGone:
            print("           -> 브라우저가 연결을 닫음", flush=True)

    def emit(self, event: str, data: Any) -> None:
        try:
            self.wfile.write(f"event: {event}\ndata: {dumps(data)}\n\n".encode("utf-8"))
            self.wfile.flush()
        except OSError as error:
            raise ClientGone from error

    def answer(self, question: str, thread_id: str) -> None:
        """한 턴: 그래프를 돌리며 단계가 끝날 때마다 step, 끝에 result(ChatResult + seconds) 또는 error를 보낸다.
        그래프 호출은 backend/app/main.py chat_stream과 같다."""
        print(f"[{time.strftime('%H:%M:%S')}] Q: {question}", flush=True)
        started = time.perf_counter()
        config = {"configurable": {"thread_id": thread_id}}
        final: dict = {}
        try:
            with self.lock:
                for mode, chunk in self.graph.stream({"messages": [("user", question)]}, config,
                                                     stream_mode=["custom", "values"]):
                    if mode == "custom":
                        name, payload = chunk
                        payload = payload if isinstance(payload, dict) else {}
                        self.emit("step", {"name": name, "seconds": payload.get("seconds"), "key": payload.get("key")})
                    else:
                        final = chunk
            result = jsonable(self.result_of(final))
        except ClientGone:
            raise
        except Exception as error:  # noqa: BLE001 - 어떤 실패든 화면에 오류로 보인다
            seconds = time.perf_counter() - started
            detail = short(str(error))
            print(f"           -> 오류 {type(error).__name__}: {detail}", flush=True)
            print("".join(traceback.format_tb(error.__traceback__)[-6:]), flush=True)
            self.emit("error", {"type": type(error).__name__, "detail": detail, "seconds": seconds})
            return
        result["seconds"] = time.perf_counter() - started
        print(f"           -> {result.get('outcome')} {result.get('table_key') or '-'} {result['seconds']:.1f}초",
              flush=True)
        self.emit("result", result)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # cp949 콘솔에서 못 찍는 글자 때문에 죽지 않게
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    root = find_root(args.repo)
    bootstrap(root)
    loaded = apply_settings(args)
    max_rows = args.max_rows if args.max_rows is not None else int(os.environ.get("E2E_MAX_TABLE_ROWS") or MAX_TABLE_ROWS)

    try:  # 포트부터 잡는다: 모델을 다 올린 뒤에 포트 충돌로 죽지 않게
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as error:
        print(f"포트 {args.port}를 열 수 없습니다 ({error}). --port로 다른 번호를 주세요.")
        return 1
    server.daemon_threads = True

    print(f"레포: {root}")
    print(f".env: {'읽음' if loaded else '읽지 않음'}")
    print("검색 자원을 여는 중입니다 (표 목록, Chroma, 모델) ...", flush=True)
    started = time.perf_counter()
    try:
        cfg, graph = build_agent(max_rows)
        from kosis_agent.graph.state import chat_result
    except Exception as error:  # noqa: BLE001
        traceback.print_exc()
        print(f"\n시작 실패: {type(error).__name__}: {short(str(error))}")
        print("확인할 것: Chroma 서버(기본 localhost:8001)가 떠 있는지, --collection / --embed-ckpt / --rerank-ckpt 값이 맞는지.")
        server.server_close()
        return 1

    Handler.graph, Handler.info, Handler.result_of = graph, describe(cfg, max_rows), staticmethod(chat_result)
    for name, value in Handler.info.items():
        print(f"  {name}: {value}")
    if Handler.info["stage2"] in ("none", "off", ""):
        print("  주의: 2차 리랭커가 꺼져 있습니다 (e2e-best와 같게 하려면 --stage2 gpt).")
    shown_host = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    url = f"http://{shown_host}:{args.port}"
    print(f"준비됨 ({time.perf_counter() - started:.0f}초). 브라우저에서 {url} 을 여세요. 끝내려면 Ctrl+C.", flush=True)
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
