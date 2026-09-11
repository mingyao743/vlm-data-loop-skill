#!/usr/bin/env python3
"""Convert platform eval output into canonical eval-result jsonl (P1-2).

parse_eval.py only eats the canonical format (schemas/<platform>/eval_*.md).
Until now the conversion existed only as a markdown table — the biggest
operational friction of the flywheel. This script closes that gap for the two
most common shapes; extend with new --format handlers as needed.

Supported formats:
  swift_ir     ms-swift embedding eval / BEIR-style retrieval output:
               one JSON per line with {"query_id","query","positive_ids",
               "topk":[{"id":..,"score":..}]} (or "hits"), plus optional
               "--axis-map meta.jsonl" mapping query_id -> axis for novelty.
  vqa_jsonl    per-sample VQA judge output:
               {"question","image","source"?,"expected_answer"|"gt",
                "predicted_answer"|"pred","correct"} per line.
  recall_only  summary-only output: {"recall@10":0.71,...} flat JSON — emitted
               as a metrics.json for record_turn (NOT a canonical eval-result;
               per-badcase analysis impossible in this shape).

Usage:
  convert_eval.py INFILE --format swift_ir --k 10 --out eval_result.jsonl [--axis-map META.jsonl]
  convert_eval.py INFILE --format vqa_jsonl --out eval_result.jsonl
  convert_eval.py INFILE --format recall_only --out metrics.json
Exit: 0 ok; 1 on missing input / unknown format / zero converted lines.
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def conv_swift_ir(inp: str, out: str, k: int, axis_map: str | None) -> int:
    amap = {}
    if axis_map:
        for line in open(axis_map, encoding="utf-8"):
            if line.strip():
                m = json.loads(line)
                amap[str(m.get("query_id") or m.get("qid"))] = m.get("axis") or m.get("type")
    n = 0
    with open(out, "w", encoding="utf-8") as fo:
        for line in open(inp, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            qid = str(d.get("query_id") or d.get("qid") or "")
            pos = d.get("positive_ids") or d.get("pos_ids") or []
            hits = d.get("topk") or d.get("hits") or []
            topk = [{"id": (h.get("id") if isinstance(h, dict) else h),
                     "score": (h.get("score", 0.0) if isinstance(h, dict) else 0.0)}
                    for h in hits][:max(k, 1)]
            topk_ids = {t["id"] for t in topk}
            fo.write(json.dumps({
                "kind": "embedding", "query": d.get("query", ""), "query_id": qid,
                "axis": amap.get(qid, "unknown"),
                "expected_pos_ids": list(pos), "predicted_topk": topk,
                "k": k, "hit_at_k": bool(set(pos) & topk_ids),
            }, ensure_ascii=False) + "\n")
            n += 1
    return n


def conv_vqa(inp: str, out: str) -> int:
    n = 0
    with open(out, "w", encoding="utf-8") as fo:
        for line in open(inp, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            gt = d.get("expected_answer", d.get("gt"))
            pred = d.get("predicted_answer", d.get("pred", ""))
            correct = d.get("correct")
            if correct is None and gt is not None:
                correct = str(pred).strip() == str(gt).strip()
            fo.write(json.dumps({
                "kind": "vqa", "question": d.get("question", ""), "image": d.get("image", ""),
                "source": d.get("source", "unknown"),
                "channel": "online" if d.get("channel") == "online" else "offline",
                "expected_answer": gt, "predicted_answer": pred, "correct": bool(correct),
            }, ensure_ascii=False) + "\n")
            n += 1
    return n


def conv_recall_only(inp: str, out: str) -> int:
    d = json.load(open(inp, encoding="utf-8"))
    metrics = {k: v for k, v in d.items() if isinstance(v, (int, float))}
    if not metrics:
        return 0
    with open(out, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    return len(metrics)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("infile")
    ap.add_argument("--format", required=True, choices=["swift_ir", "vqa_jsonl", "recall_only"])
    ap.add_argument("--out", default=None)
    ap.add_argument("--k", type=int, default=10, help="cutoff for hit@k in swift_ir (default 10)")
    ap.add_argument("--axis-map", default=None, help="meta.jsonl mapping query_id -> axis (swift_ir)")
    a = ap.parse_args()

    if not os.path.exists(a.infile):
        print(f"[FAIL] not found: {a.infile}")
        return 1
    default_out = {"swift_ir": "eval_result.jsonl", "vqa_jsonl": "eval_result.jsonl",
                   "recall_only": "metrics.json"}[a.format]
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.infile)), default_out)

    if a.format == "swift_ir":
        n = conv_swift_ir(a.infile, out, a.k, a.axis_map)
    elif a.format == "vqa_jsonl":
        n = conv_vqa(a.infile, out)
    else:
        n = conv_recall_only(a.infile, out)

    if n == 0:
        print(f"[FAIL] 0 lines converted — check input shape against --format {a.format}")
        return 1
    print(f"[OK] {n} lines → {out} (canonical; feed parse_eval.py or record_turn.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
