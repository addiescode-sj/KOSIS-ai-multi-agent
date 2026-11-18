"""Answerability classifier v2 (Colab): fine-tune a cross-encoder on judged (question, table card) pairs.

Data: the folder written by the PC builder scripts/answerability/v2/build_data_v2.py (zipped as <build>.zip):
    pairs.jsonl  {q, table, label, kind, group, own, src, split}. kinds: pos, ctrl, pos_alt (1); neg_near, np_table,
                 np_near, easy, no_item, oos (0). group = one question family (base question + its no_item / ctrl
                 minimal pairs + the same question on other tables). split = train / val (whole menu families).
    cards.json   key -> card text ("ans-c1"); KOS-192 dev / test tables are not in it.
Training: each update takes --groups whole groups, so a minimal pair (ctrl vs no_item, same question on two tables)
always sits in one batch. Large groups are cut to --max-group pairs, keeping every label-1 pair and its no_item twin.
Loss: BCE with logits, pos_weight = min(--max-pos-weight, n0 / n1) per update. Model and init as v1
(answerability_train.build_model / trainable: --init rr | emb | base, top --layers + head trained).
Validation (before training and after each epoch), on split = val:
    auc                    label 1 vs label 0, all pairs
    auc_pos_vs_<kind>      pos+ctrl+pos_alt vs each label-0 kind
    acc_<kind>             accuracy at logit 0 for each kind
    minimal_pair_acc       share of groups with both a ctrl and a no_item where score(ctrl) > score(no_item)
    question               per group and question text: max logit over its tables; answerable if any label 1.
                           AUC and best-F1 refusal threshold (refusal = positive class)
Each epoch writes <out>/<run>/<name>-e<N>.pt and .json (settings, data sha8s, val numbers, threshold).

    python answerability_train_v2.py --selftest
    python answerability_train_v2.py --data data/ans-v2 --init rr --init-ckpt <RR-E2s-e2.pt> --limit 200 --epochs 1 --steps-per-epoch 20
    python answerability_train_v2.py --data data/ans-v2 --init rr --init-ckpt <RR-E2s-e2.pt>
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from answerability_train import CARD_FORMAT, auc, best_f1, build_model, sha8, trainable  # noqa: E402

NAME = "ANS2"
POS_KINDS = ("pos", "ctrl", "pos_alt")
NEG_KINDS = ("neg_near", "np_table", "np_near", "easy", "no_item", "oos")


def read_jsonl(p: Path) -> list[dict]:
    with p.open(encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def load(data: Path) -> tuple[list[dict], dict, dict]:
    rows = read_jsonl(data / "pairs.jsonl")
    cards = json.loads((data / "cards.json").read_text(encoding="utf-8"))
    stats = json.loads((data / "stats.json").read_text(encoding="utf-8"))
    if stats.get("card_format") != CARD_FORMAT:
        raise ValueError(f"{data} cards are {stats.get('card_format')!r}, this trainer reads {CARD_FORMAT!r}")
    rows = [r for r in rows if r["table"] in cards]
    return rows, cards, stats


def groups_of(rows: list[dict], split: str) -> list[list[dict]]:
    g = defaultdict(list)
    for r in rows:
        if r["split"] == split:
            g[r["group"]].append(r)
    return [g[k] for k in sorted(g)]


def cut(group: list[dict], k: int, rng: random.Random) -> list[dict]:
    """At most k pairs: all label-1 pairs and no_item pairs first (minimal pairs), then a random rest."""
    if len(group) <= k:
        return group
    first = [r for r in group if r["label"] == 1 or r["kind"] == "no_item"]
    rest = [r for r in group if not (r["label"] == 1 or r["kind"] == "no_item")]
    rng.shuffle(rest)
    return (first + rest)[:max(k, len(first))]


def metrics(pairs: list[dict], scores: list[float]) -> dict:
    pos = [s for p, s in zip(pairs, scores) if p["label"] == 1]
    neg = [s for p, s in zip(pairs, scores) if p["label"] == 0]
    out = {"pairs": len(pairs), "auc": auc(pos, neg),
           "acc0": round(sum((s > 0) == (p["label"] == 1) for p, s in zip(pairs, scores)) / max(1, len(pairs)), 4)}
    for kind in POS_KINDS + NEG_KINDS:
        ks = [(p, s) for p, s in zip(pairs, scores) if p["kind"] == kind]
        if not ks:
            continue
        out[f"n_{kind}"] = len(ks)
        out[f"acc_{kind}"] = round(sum((s > 0) == (p["label"] == 1) for p, s in ks) / len(ks), 4)
        if kind in NEG_KINDS:
            out[f"auc_pos_vs_{kind}"] = auc(pos, [s for _, s in ks])
    by_g = defaultdict(lambda: defaultdict(list))
    for p, s in zip(pairs, scores):
        by_g[p["group"]][p["kind"]].append(s)
    mp = [max(k["ctrl"]) > max(k["no_item"]) for k in by_g.values() if k.get("ctrl") and k.get("no_item")]
    out["minimal_pair_n"] = len(mp)
    out["minimal_pair_acc"] = round(sum(mp) / len(mp), 4) if mp else None
    qs = defaultdict(list)
    for p, s in zip(pairs, scores):
        qs[(p["group"], p["q"])].append((s, p["label"]))
    mx = [max(s for s, _ in v) for v in qs.values()]
    refuse = [0 if any(y for _, y in v) else 1 for v in qs.values()]
    out["question_n"] = {"answerable": refuse.count(0), "unanswerable": refuse.count(1)}
    out["question_auc"] = auc([-m for m, y in zip(mx, refuse) if y], [-m for m, y in zip(mx, refuse) if not y])
    out["question_best_f1"] = best_f1(mx, refuse)
    return out


def train(args) -> Path:
    import numpy as np
    import torch

    started = time.perf_counter()
    data = Path(args.data)
    rows, cards, stats = load(data)
    tr, va = groups_of(rows, "train"), groups_of(rows, "val")
    if args.limit:
        rl = random.Random(args.seed)
        tr, va = rl.sample(tr, min(args.limit, len(tr))), rl.sample(va, min(args.limit, len(va)))
    v_pairs = [r for g in va for r in g]
    print(json.dumps({"data": data.name, "train_groups": len(tr), "val_groups": len(va),
                      "train_kinds": dict(Counter(r["kind"] for g in tr for r in g)),
                      "val_kinds": dict(Counter(r["kind"] for r in v_pairs))}, ensure_ascii=False))

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model, tok, base, overlaid = build_model(args.init, args.init_ckpt, device)
    params = trainable(model, args.layers)
    print(f"device {device}; trainable {sum(p.numel() for p in params) / 1e6:.1f}M params")

    def logits(qs: list[str], docs: list[str]):
        enc = tok(qs, docs, truncation="longest_first", max_length=args.max_length, padding=True,
                  return_tensors="pt").to(device)
        with torch.autocast(device_type=device.type, dtype=torch.float16, enabled=device.type == "cuda"):
            return model(**enc).logits.view(-1).float()

    def check() -> dict:
        model.eval()
        out = []
        with torch.no_grad():
            for b in range(0, len(v_pairs), args.eval_batch):
                ch = v_pairs[b:b + args.eval_batch]
                out += logits([p["q"] for p in ch], [cards[p["table"]] for p in ch]).cpu().tolist()
        model.train()
        return metrics(v_pairs, out) if v_pairs else {}

    val = {"before": check()}
    print(f"val before: {json.dumps(val['before'], ensure_ascii=False)}")
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.01)
    steps_per_epoch = args.steps_per_epoch or max(1, math.ceil(len(tr) / args.groups))
    total = steps_per_epoch * args.epochs
    warm = max(1, int(total * 0.1))
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: (s + 1) / warm if s < warm else max(0.0, (total - s) / max(1, total - warm)))
    amp = torch.amp if hasattr(torch.amp, "GradScaler") else None
    scaler = (amp.GradScaler("cuda", enabled=device.type == "cuda") if amp
              else torch.cuda.amp.GradScaler(enabled=device.type == "cuda"))
    rng = random.Random(args.seed)
    order: list[list[dict]] = []
    run_dir = Path(args.out) / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    step, seen = 0, Counter()
    model.train()
    for ep in range(1, args.epochs + 1):
        losses, t_ep = [], time.perf_counter()
        for _ in range(steps_per_epoch):
            if len(order) < args.groups:
                order += rng.sample(tr, len(tr))
            bx, order = order[:args.groups], order[args.groups:]
            pairs = [r for g in bx for r in cut(g, args.max_group, rng)]
            seen.update(r["kind"] for r in pairs)
            y = torch.tensor([float(r["label"]) for r in pairs], device=device)
            n_pos = float(y.sum().item())
            pos_w = torch.tensor(min(args.max_pos_weight, (len(pairs) - n_pos) / max(1.0, n_pos)), device=device)
            opt.zero_grad(set_to_none=True)
            loss = 0.0
            for b in range(0, len(pairs), args.pair_batch):
                ch = pairs[b:b + args.pair_batch]
                s = logits([r["q"] for r in ch], [cards[r["table"]] for r in ch])
                part = torch.nn.functional.binary_cross_entropy_with_logits(
                    s, y[b:b + len(ch)], pos_weight=pos_w, reduction="sum") / len(pairs)
                scaler.scale(part).backward()
                loss += part.item()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            scaler.step(opt)
            scaler.update()
            sched.step()
            losses.append(loss)
            step += 1
            if step % 50 == 0:
                print(f"  {args.name} epoch {ep} step {step}/{total} loss {np.mean(losses[-50:]):.4f} "
                      f"({time.perf_counter() - started:.0f}s)")
        after = check()
        val[f"e{ep}"] = after
        ckpt = run_dir / f"{args.name}-e{ep}.pt"
        names = {n for n, p in model.named_parameters() if p.requires_grad} | set(overlaid)
        torch.save({n: t.detach().cpu() for n, t in model.state_dict().items() if n in names}, ckpt)
        meta = {
            "name": args.name, "epoch": ep, "base": base, "init": args.init, "init_ckpt": args.init_ckpt,
            "init_ckpt_sha8": sha8(Path(args.init_ckpt)) if args.init in ("rr", "emb") else None,
            "card_format": CARD_FORMAT, "data_dir": data.name, "data_stats": stats,
            "pairs_sha8": sha8(data / "pairs.jsonl"), "cards_sha8": sha8(data / "cards.json"),
            "layers": args.layers, "lr": args.lr, "groups": args.groups, "max_group": args.max_group,
            "pair_batch": args.pair_batch, "max_length": args.max_length, "max_pos_weight": args.max_pos_weight,
            "seed": args.seed, "limit": args.limit, "train_groups": len(tr), "val_groups": len(va),
            "pairs_seen": dict(seen), "steps": step, "steps_per_epoch": steps_per_epoch,
            "loss_mean": float(np.mean(losses)), "val": val,
            "threshold_best_f1": after.get("question_best_f1", {}).get("threshold"),
            "seconds": round(time.perf_counter() - started), "epoch_seconds": round(time.perf_counter() - t_ep),
        }
        ckpt.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"-> {ckpt} (loss {meta['loss_mean']:.4f})\n   val e{ep}: {json.dumps(after, ensure_ascii=False)}")
    return run_dir


def selftest() -> None:
    import tempfile

    rows = [
        {"q": "2010 A 30대", "table": "A", "label": 1, "kind": "pos", "group": "g1", "split": "val"},
        {"q": "2010 A 30대", "table": "B", "label": 0, "kind": "neg_near", "group": "g1", "split": "val"},
        {"q": "2010 A 90대", "table": "A", "label": 0, "kind": "no_item", "group": "g1", "split": "val"},
        {"q": "2010 A 40대", "table": "A", "label": 1, "kind": "ctrl", "group": "g1", "split": "val"},
        {"q": "2003 A 30대", "table": "A", "label": 0, "kind": "np_table", "group": "g1", "split": "val"},
        {"q": "x", "table": "C", "label": 1, "kind": "pos", "group": "g2", "split": "train"},
    ]
    sc = [3.0, -2.0, -1.0, 2.0, -4.0, 1.0]
    va = [r for g in groups_of(rows, "val") for r in g]
    m = metrics(va, [sc[rows.index(r)] for r in va])
    assert m["auc"] == 1.0 and m["minimal_pair_acc"] == 1.0 and m["minimal_pair_n"] == 1, m
    assert m["question_n"] == {"answerable": 2, "unanswerable": 2} and m["question_auc"] == 1.0, m
    assert m["acc_no_item"] == 1.0 and m["auc_pos_vs_np_table"] == 1.0
    big = [{"label": 0, "kind": "neg_near"}] * 10 + [{"label": 1, "kind": "pos"}, {"label": 0, "kind": "no_item"}]
    c = cut(big, 4, random.Random(0))
    assert len(c) == 4 and c[0]["kind"] == "pos" and c[1]["kind"] == "no_item"
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "ans-x"
        d.mkdir()
        (d / "pairs.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        (d / "cards.json").write_text(json.dumps({"A": "a", "B": "b"}), encoding="utf-8")
        (d / "stats.json").write_text(json.dumps({"card_format": CARD_FORMAT}), encoding="utf-8")
        r2, _, _ = load(d)
        assert len(r2) == 5  # C has no card -> dropped
    print("selftest ok")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--data", default="data/ans-v2")
    p.add_argument("--name", default=NAME)
    p.add_argument("--init", choices=("rr", "emb", "base"), default="rr")
    p.add_argument("--init-ckpt", default="")
    p.add_argument("--layers", type=int, default=8)
    p.add_argument("--lr", type=float, default=1e-5)
    p.add_argument("--epochs", type=int, default=2)
    p.add_argument("--groups", type=int, default=6, help="whole question groups per update")
    p.add_argument("--max-group", type=int, default=12, help="pairs kept per group and update")
    p.add_argument("--pair-batch", type=int, default=24)
    p.add_argument("--steps-per-epoch", type=int, default=0)
    p.add_argument("--max-pos-weight", type=float, default=3.0)
    p.add_argument("--max-length", type=int, default=512)
    p.add_argument("--eval-batch", type=int, default=32)
    p.add_argument("--limit", type=int, default=0, help="train / val groups (quick check)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", default="outputs/answerability_runs")
    args = p.parse_args()
    if args.selftest:
        selftest()
        return
    print(f"-> {train(args)}")


if __name__ == "__main__":
    main()
