#!/usr/bin/env python3
"""Parse eval results into a prioritized badcase pool (selection methodology · [auto]).

Reads a *canonical eval-result* jsonl (platform-agnostic; see
schemas/<platform>/eval_*.md for how to convert platform eval output into this
format) + the domain profile, and emits ``badcases.jsonl`` ranked by leverage
(references/badcase_selection.md §1). This is the [auto] scorer; the decision to
act on a badcase is [manual] / alive — parse_eval only scores & routes (活/冻结).

Canonical eval-result line (one per failed or sampled query):
  {"kind":"embedding","query":str,"query_id":str,"axis":str,
   "expected_pos_ids":[...],"predicted_topk":[{"id":str,"score":float}],"k":int,"hit_at_k":bool}
  {"kind":"vqa","question":str,"image":str,"source":str,"channel":"offline"|"online",
   "expected_answer":str|null,"predicted_answer":str,"correct":bool}

``channel`` (default "offline") marks production-intake rows: their freq is
discounted by ``selection.online_freq_discount`` — production feedback is sparse
& noisy (references/badcase_intake.md). Never infer channel from source name.

Profile inputs: similarity_axes, vqa_sources, axis_corpus_gaps (optional),
badcase_registry (optional), selection (weights/thresholds).

Usage: parse_eval.py EVAL_RESULT.jsonl [--profile ...] [--top 10] [--out badcases.jsonl]
Exit: 1 only on missing input / parse error; 0 otherwise (scorer, not a gate).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def load_profile(path: str | None) -> dict:
    default = {
        "similarity_axes": [], "vqa_sources": [], "axis_corpus_gaps": [],
        "badcase_registry": [],
        "selection": {
            "w_freq": 0.3, "w_sev": 0.3, "w_fix": 0.2, "w_novel": 0.2,
            "new_bonus": 1.2,
            "badcase_threshold": 50, "roi_floor": 0.0, "online_freq_discount": 0.5,
        },
    }
    if not path:
        return default
    if yaml is None:
        print(f"[FAIL] pyyaml not installed; cannot load profile {path}")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        p = yaml.safe_load(f) or {}
    merged = {**default, **p}
    merged["selection"] = {**default["selection"], **(p.get("selection") or {})}
    return merged


def char_overlap(a: str, b: str) -> bool:
    """True if any character-set overlap (cheap severity proxy for VQA)."""
    return bool(set(a) & set(b)) if (a and b) else False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="canonical eval-result jsonl, or dir containing eval_result.jsonl")
    ap.add_argument("--profile", default=os.path.join(os.path.dirname(__file__), "..", "profiles", "tech_manual.yaml"))
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--out", default=None, help="output badcases.jsonl (default: alongside target)")
    a = ap.parse_args()

    profile = load_profile(a.profile)
    axes = profile.get("similarity_axes") or []
    name_to_id = {str(x.get("name")): str(x.get("id")) for x in axes if x.get("name")}
    known_names = set(name_to_id)
    known_ids = {str(x.get("id")) for x in axes if x.get("id") is not None}
    vqa_sources = {str(s) for s in (profile.get("vqa_sources") or [])}
    gaps = {str(x) for x in (profile.get("axis_corpus_gaps") or [])}
    registry = {str(r.get("axis")): r for r in (profile.get("badcase_registry") or []) if r.get("axis")}
    sel = profile["selection"]

    jsonl = a.target if a.target.endswith(".jsonl") else os.path.join(a.target, "eval_result.jsonl")
    if not os.path.exists(jsonl):
        print(f"[FAIL] not found: {jsonl}")
        return 1

    # aggregate failures per (kind, axis_or_source)
    clusters: dict = collections.defaultdict(
        lambda: {"freq": 0, "hard": 0, "soft": 0, "examples": [], "kind": None, "key": None})
    n = tot = 0
    for line in open(jsonl, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            print(f"[WARN] skip unparseable line: {line[:80]}")
            continue
        n += 1
        channel = "online" if d.get("channel") == "online" else "offline"
        kind = d.get("kind")
        if kind == "embedding":
            if d.get("hit_at_k") is False:
                tot += 1
                key = str(d.get("axis") or "unknown")
                c = clusters[("emb", key, channel)]
                c["kind"], c["key"], c["channel"] = "emb", key, channel
                c["freq"] += 1
                exp = set(d.get("expected_pos_ids") or [])
                topk_ids = {x.get("id") for x in (d.get("predicted_topk") or [])}
                # hard = expected not even in full retrieved list; soft = in list but beyond k
                c["hard" if not (exp & topk_ids) else "soft"] += 1
                if len(c["examples"]) < 2:
                    c["examples"].append({"query": d.get("query"), "query_id": d.get("query_id"),
                                          "missed": list(exp - topk_ids)})
        elif kind == "vqa":
            if d.get("correct") is False:
                tot += 1
                key = str(d.get("source") or "unknown")
                c = clusters[("vqa", key, channel)]
                c["kind"], c["key"], c["channel"] = "vqa", key, channel
                c["freq"] += 1
                pa = str(d.get("predicted_answer") or "").strip()
                ea = str(d.get("expected_answer") or "").strip()
                c["hard" if not pa or not char_overlap(pa, ea) else "soft"] += 1
                if len(c["examples"]) < 2:
                    c["examples"].append({"question": d.get("question"), "image": d.get("image"),
                                          "expected": ea, "predicted": pa})
        else:
            print(f"[WARN] unknown kind {kind!r}, skipped")

    if n == 0:
        print("[WARN] 0 eval-result lines parsed")
        return 0
    print(f"== {n} eval lines, {tot} failures, {len(clusters)} clusters ==")

    rows = []
    for (ckind, key, channel), c in clusters.items():
        is_axis = ckind == "emb"
        if is_axis:
            known = (key in known_names) or (key in known_ids)
        else:
            fm = profile.get("vqa_failure_modes") or []
            known = (key in vqa_sources) or (key in {str(x.get("name")) for x in fm if x.get("name")})
        axis_id = name_to_id.get(key)
        gap = (key in gaps) or (axis_id in gaps) if is_axis else False
        fixability = 0 if gap else 1

        # P0-3: count-based regression is a HINT, never a verdict — metric-level
        # guard (diff_turn) is authoritative; surfaced for humans & register_axis.
        reg = registry.get(key)
        count_regression_hint = bool(
            reg and reg.get("last_fail_count") is not None and c["freq"] > reg["last_fail_count"])
        # P0-3 rewrite: count-based "regression" never multiplies leverage.
        # VQA unknown source (not in vqa_sources/vqa_failure_modes) is also "new"
        # — matches its classify_register routing (P0-2/P2-3).
        if not known:
            novelty, nov_mult = "new", sel.get("new_bonus", 1.2)
        else:
            novelty, nov_mult = "known", 1.0

        sev = 1.0 if c["hard"] > 0 else 0.5
        # P0-2: discount production-intake freq by explicit ``channel:"online"``
        eff_freq = c["freq"] * (sel.get("online_freq_discount", 1.0) if channel == "online" else 1.0)
        freq_norm = eff_freq / tot if tot else 0.0
        novelty_raw = 1.0 if novelty in ("new", "regression") else 0.0
        leverage = (sel["w_freq"] * freq_norm + sel["w_sev"] * sev
                    + sel["w_fix"] * fixability + sel["w_novel"] * novelty_raw) * nov_mult

        if gap:
            action, diagnosis = "downgrade_discard", "corpus_gap (数据救不了语料不足)"
        elif novelty == "new":
            action, diagnosis = "register_axis", "新轴候选 (register_axis 建议 → 人确认 → re-pilot)"
        elif is_axis:
            action, diagnosis = "rebalance_resample", "表征缺口 (轴存在但欠采样)"
        elif not known:
            # P0-2/P2-3: VQA unknown source/production = new failure-mode candidate,
            # not a blanket "go rewrite" → open vqa_failure_modes registry.
            action, diagnosis = "classify_register", "VQA 未知来源/失败模式候选 (人确认 → profile vqa_failure_modes)"
        else:
            action, diagnosis = "rewrite_fix", "shortcut/泄漏/幻觉 (回 audit 修复)"

        rows.append({"kind": c["kind"], "axis_or_source": key, "channel": channel,
                     "freq": c["freq"], "hard": c["hard"], "soft": c["soft"],
                     "fixability": fixability, "novelty": novelty,
                     "count_regression_hint": count_regression_hint,
                     "leverage": round(leverage, 4),
                     "diagnosis": diagnosis, "action": action, "examples": c["examples"]})

    rows.sort(key=lambda r: r["leverage"], reverse=True)

    out = a.out or os.path.join(os.path.dirname(os.path.abspath(jsonl)), "badcases.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"[INFO] wrote {out}")
    print(f"[INFO] top {a.top} by leverage:")
    for r in rows[:a.top]:
        hint = " [count-REGRESSION-hint→以diff_turn仲裁]" if r["count_regression_hint"] else ""
        ch = "(online)" if r["channel"] == "online" else ""
        print(f"  {r['leverage']:.3f}  {r['kind']:4} {r['axis_or_source']:<22}{ch} "
              f"freq={r['freq']:<4} {r['novelty']:<10} fix={r['fixability']} → {r['action']}{hint}")
    thr = sel.get("badcase_threshold", 50)
    print(f"[{'WARN' if tot > thr else 'OK'}] total failures {tot} vs badcase_threshold {thr}"
          + (" → 触发下一回合 (flywheel §2)" if tot > thr else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
