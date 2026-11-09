"""Phase 2 L1 reproduction with this repo's settings: phase 2's parity_l1 dump / compare over backend/app/config.py.

    set PYTHONPATH=.;src;vendor\\phase2\\src;vendor\\phase1;vendor\\phase1\\src
    python scripts\\search_l1.py dump --preset hybrid+cond --questions <retrieval golden .jsonl> --out data\\parity\\l1_t3_hybrid+cond.jsonl
    python scripts\\search_l1.py dump --preset staged --questions <same file> --out data\\parity\\l1_t3_staged.jsonl
    python scripts\\search_l1.py compare <phase 2 l1_s0_hybrid+cond.jsonl> data\\parity\\l1_t3_hybrid+cond.jsonl --allow-moved
    python scripts\\search_l1.py compare <phase 2 l1_s0_staged.jsonl> data\\parity\\l1_t3_staged.jsonl --allow-moved --rerank-old <old unseen-e2.pt>

Everything but the settings is vendor/phase2/scripts/parity_l1.py as it is (rows, manifest, config fingerprint,
compare rules, route guide -> found). Its build_preset_graph reads phase 2's backend.app.config, which this repo's
backend.app shadows, so here the graph is built from agent_config() (the shell values the agent runs with, data under
<KOSIS_WORKSPACE>/data) with the preset's overrides. compare refuses when the config fingerprints differ (collection,
embedder, tables.jsonl sha256, ...): that is the check that this repo searches the same index as phase 2 did.
--questions is required: the phase 2 default reads its evals package, which this repo's evals/ shadows.
--allow-moved (compare only): the checkpoints were renamed (data/models/) and the collection loaded again, so the
fingerprints' collection, embed and rerank1_ckpt differ by name. Accepted only when: embed has the same sha256 prefix
after "@" (same weights), and rerank1_ckpt's file in dump b has the same sha256 as the old file given with
--rerank-old (needed when the preset uses stage 1). The collection name may differ; its doc_format, the tables.jsonl
sha256 and every other key must still match, and the rankings are then compared as phase 2 compares them.
(01-reproduce-baselines.md W3.)
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "vendor" / "phase1" / "src", ROOT / "vendor" / "phase1", ROOT / "vendor" / "phase2" / "src",
           ROOT / "src", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
os.environ.setdefault("KOSIS_WORKSPACE", str(ROOT))

PARITY_L1 = ROOT / "vendor" / "phase2" / "scripts" / "parity_l1.py"
# phase 2 evals.runners.retrieval.STAGED @56b4252 for the two L1 presets ("" rerank_ckpt = stage 1 off)
PRESETS = {
    "hybrid+cond": {"cond_filter": True, "rerank_ckpt": "", "stage2": "none"},
    "staged": {"cond_filter": True, "stage2": "none"},  # + stage 1 (RERANK_CKPT)
}


def _load_parity_l1():
    spec = importlib.util.spec_from_file_location("phase2_parity_l1", PARITY_L1)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def preset_config(preset: str):
    """(spec, options) of agent_config() with a preset's overrides."""
    from backend.app.config import agent_config

    cfg, p = agent_config(), PRESETS[preset]
    options = cfg.options.model_copy(update={"cond_filter": p["cond_filter"], "stage2": p["stage2"]})
    spec = cfg.spec.model_copy(update={"rerank_ckpt": p["rerank_ckpt"] or None}) if "rerank_ckpt" in p else cfg.spec
    return spec, options


def build_preset_graph(preset: str):
    """(graph, resources) as parity_l1.build_preset_graph builds them, from this repo's settings."""
    from kosis_rag.adapters.loader import load_resources
    from kosis_rag.graph.search import build_graph

    spec, options = preset_config(preset)
    resources = load_resources(spec=spec, options=options, llm=None)  # both presets: stage 2 off
    return build_graph(resources), resources


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def allow_moved(parity, rerank_old: Path | None) -> None:
    """Accept the name-only differences of a reloaded collection and renamed checkpoints in compare (see --allow-moved)."""
    original = parity.diff_config

    def diff_config(ca: dict, cb: dict) -> list[str]:
        problems = original(ca, cb)
        accepted = []
        if ca.get("collection") != cb.get("collection"):
            accepted.append("collection")
        ea, eb = str(ca.get("embed") or ""), str(cb.get("embed") or "")
        if ea != eb and "@" in ea and "@" in eb and ea.rsplit("@", 1)[1] == eb.rsplit("@", 1)[1]:
            accepted.append("embed")
        ra, rb = ca.get("rerank1_ckpt"), cb.get("rerank1_ckpt")
        if ra != rb and ra and rb and rerank_old is not None and rerank_old.is_file() and Path(rb).is_file():
            if _sha256(rerank_old) == _sha256(Path(rb)):
                accepted.append("rerank1_ckpt")
        for key in accepted:
            print(f"note: {key} {ca.get(key)!r} -> {cb.get(key)!r} accepted (same weights)", file=sys.stderr)
        drop = {f"config.{k}: {ca.get(k)!r} vs {cb.get(k)!r}" for k in accepted}
        return [p for p in problems if p not in drop]

    parity.diff_config = diff_config


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["dump"] and "--questions" not in argv:
        print("search_l1: dump needs --questions (the retrieval golden file the phase 2 dump used)", file=sys.stderr)
        return 2
    parity = _load_parity_l1()
    parity.build_preset_graph = build_preset_graph
    rerank_old = None
    if "--rerank-old" in argv:
        i = argv.index("--rerank-old")
        if i + 1 >= len(argv):
            print("search_l1: --rerank-old needs the old .pt file", file=sys.stderr)
            return 2
        rerank_old = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if "--allow-moved" in argv:
        argv.remove("--allow-moved")
        if argv[:1] != ["compare"]:
            print("search_l1: --allow-moved goes with compare", file=sys.stderr)
            return 2
        allow_moved(parity, rerank_old)
    return parity.main(argv)


if __name__ == "__main__":
    from kosis_agent.envfile import load_env_file

    load_env_file()  # <repo>/.env, shell values win
    sys.exit(main())
