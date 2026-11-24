"""Lab: linear mix of a fine-tuned embedder checkpoint with its base model (weight interpolation).

merged = a * finetuned + (1 - a) * base, for every float tensor in the checkpoint. The checkpoint keys are
the auto_model state-dict keys (same mapping as kosis_rag.adapters.models.put_back), so base values come from
SentenceTransformer(base)[0].auto_model.state_dict(). Non-tensor entries are copied as is.

Usage:
  python scripts/lab_mix_weights.py --ckpt data/models/embedder/E2s-e2.pt --alphas 0.3 0.5 0.7
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch


def base_state(base: str) -> dict[str, torch.Tensor]:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(base, device="cpu")
    return {k: v.detach().float().clone() for k, v in model[0].auto_model.state_dict().items()}


def mix(ft: dict, base: dict[str, torch.Tensor], a: float) -> dict:
    out = {}
    for k, v in ft.items():
        if isinstance(v, torch.Tensor) and v.is_floating_point():
            if k not in base:
                raise KeyError(f"{k} not in base model")
            b = base[k]
            if b.shape != v.shape:
                raise ValueError(f"shape mismatch at {k}: {tuple(v.shape)} vs {tuple(b.shape)}")
            out[k] = (a * v.float() + (1.0 - a) * b).to(v.dtype)
        else:
            out[k] = v
    return out


def self_check(ft: dict, base: dict[str, torch.Tensor]) -> None:
    keys = [k for k, v in ft.items() if isinstance(v, torch.Tensor) and v.is_floating_point()][:5]
    sub = {k: ft[k] for k in keys}
    one, zero = mix(sub, base, 1.0), mix(sub, base, 0.0)
    for k in keys:
        assert torch.equal(one[k], ft[k]), f"a=1.0 differs from fine-tuned at {k}"
        assert torch.allclose(zero[k], base[k].to(ft[k].dtype)), f"a=0.0 differs from base at {k}"
        assert not torch.equal(ft[k], base[k].to(ft[k].dtype)) or "LayerNorm" in k, f"ft == base at {k}?"
    print(f"self-check ok on {len(keys)} tensors")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--base", default="BAAI/bge-m3")
    ap.add_argument("--alphas", type=float, nargs="+", default=[0.3, 0.5, 0.7])
    args = ap.parse_args()

    ckpt = Path(args.ckpt)
    ft = torch.load(ckpt, map_location="cpu")
    base = base_state(args.base)
    self_check(ft, base)
    meta_src = ckpt.with_suffix(".json")
    src_meta = json.loads(meta_src.read_text(encoding="utf-8")) if meta_src.is_file() else {}
    for a in args.alphas:
        out = ckpt.with_name(f"{ckpt.stem}-mix{round(a * 100):02d}.pt")
        torch.save(mix(ft, base, a), out)
        meta = {"name": out.stem, "mix_of": ckpt.name, "base": args.base, "alpha": a,
                "doc_format": src_meta.get("doc_format", "n2-L60"), "model": args.base}
        out.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"a={a} -> {out}")


if __name__ == "__main__":
    main()
