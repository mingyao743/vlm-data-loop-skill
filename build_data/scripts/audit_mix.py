#!/usr/bin/env python3
"""Audit sample-type mix against profile quotas ([auto], VQA line).

Sample types (from .meta.jsonl ``sample_type``, default "normal"):
  normal      可见图内有据可答的标准样本(正样本由产线本身定义,不在此计量)
  refusal     拒答样本:图中不可见/不可答,正确行为是拒答或空答案
  adversarial 对抗样本:诱导性问题/异常图,测鲁棒性,预期也是拒答或稳健作答

Why quotas matter (missing until now):
  - zero refusal samples ⇒ model learns "figure has no answer? hallucinate one"
    (the same root cause methodology.md attributes 幻觉 to — now measurable)
  - too many ⇒ model becomes conservative, refuses answerable questions
  - adversarial samples are the only cheap proxy for上线鲁棒性 in the audit tier

Usage: audit_mix.py OUT_DIR_OR_JSONL [--profile ...] [--report out_audit_mix.json]
Exit: 0 ok/warn; 1 on missing input / zero parseable lines.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def load_profile(path: str | None) -> dict:
    if not path or yaml is None or not os.path.exists(path):
        return {}
    return yaml.safe_load(open(path, encoding="utf-8")) or {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="out dir or samples_vqa.jsonl")
    ap.add_argument("--profile", default=os.path.join(os.path.dirname(__file__), "..", "profiles", "tech_manual.yaml"))
    ap.add_argument("--report", default=None)
    a = ap.parse_args()

    prof = load_profile(a.profile)
    mix = prof.get("mix") or {}
    jsonl = a.target if a.target.endswith(".jsonl") else os.path.join(a.target, "samples_vqa.jsonl")
    if not os.path.exists(jsonl):
        print(f"[FAIL] not found: {jsonl}")
        return 1

    meta_path = jsonl + ".meta.jsonl"
    if not os.path.exists(meta_path):
        print("[FAIL] no .meta.jsonl — sample_type 无法判定(需要逐样本溯源标注)")
        return 1
    types = collections.Counter()
    n = 0
    for line in open(meta_path, encoding="utf-8"):
        if line.strip():
            types[str(json.loads(line).get("sample_type", "normal"))] += 1
            n += 1
    if n == 0:
        print("[FAIL] empty .meta.jsonl")
        return 1

    print(f"== mix audit: {n} samples ({os.path.basename(jsonl)}) ==")
    metrics, bad = {}, False
    for stype in ("normal", "refusal", "adversarial"):
        share = types.get(stype, 0) / n
        metrics[f"{stype}_ratio"] = round(share, 4)
        line = f"  {stype:<11} {types.get(stype, 0):>5} ({share:.1%})"
        cfg = mix.get(stype) or {}
        lo, hi = cfg.get("min_ratio"), cfg.get("max_ratio")
        if lo is not None and share < float(lo):
            print(f"[WARN]{line} < min {float(lo):.0%} — " +
                  ("幻觉风险(不可答问题没教拒答)" if stype == "refusal" else
                   "鲁棒性无保障(没有对抗样本测)" if stype == "adversarial" else "结构失衡"))
            bad = True
        elif hi is not None and share > float(hi):
            print(f"[WARN]{line} > max {float(hi):.0%} — " +
                  ("模型会变保守,该答的不答" if stype == "refusal" else
                   "对抗占比过高,正常能力被稀释" if stype == "adversarial" else "结构失衡"))
            bad = True
        else:
            if lo is not None and hi is not None:
                bound = f"[{float(lo):.0%},{float(hi):.0%}]"
            elif lo is not None:
                bound = f"≥{float(lo):.0%}"
            elif hi is not None:
                bound = f"≤{float(hi):.0%}"
            else:
                bound = ""
            print(f"[OK]{line}" + (f" (quota {bound})" if bound else ""))
    unknown = set(types) - {"normal", "refusal", "adversarial"}
    if unknown:
        print(f"[WARN] unknown sample_type: {sorted(unknown)}")
    print("[HINT] refusal/adversarial 的 empty-answer 语义由 audit_vqa.py 按 sample_type 豁免")

    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump({"kind": "mix", "samples": n, "metrics": metrics, "warn": bad},
                      f, ensure_ascii=False, indent=2)
        print(f"[INFO] report → {a.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
