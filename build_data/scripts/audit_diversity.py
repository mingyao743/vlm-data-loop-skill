#!/usr/bin/env python3
"""Audit sample diversity against profile quotas ([auto], pure statistics).

Closes the gap between "多样性 > 同源堆量" (methodology principle 7) and any
executable mechanism. Reads the [manual] checklist items (风格分布/仿真度) and
turns them into machine-checkable ratios. Zero-cost, deterministic, works on
both lines:

  - embedding: queries extracted from messages[0].content (strip <image>)
  - vqa:       questions extracted from user-role turns

Checks (each needs profile ``diversity:`` config; missing config = skip):
  1. length buckets   — short/medium/long share vs min_ratio
                        (全长句训练 → 短句 OOD, lessons#query_style_ood)
  2. colloquial share — question-mark / 疑问词 hit rate vs colloquial_min_ratio
  3. distinct-n       — vocabulary diversity ("句式指纹" detector)
  4. per-axis floor   — samples per similarity_axes id (meta.jsonl "type")
  5. per-source floor — samples per VQA source (meta.jsonl "source")

Output mirrors audit_*.py: [OK]/[WARN] lines + optional --report json whose
"metrics" block feeds record_turn.py → turn_log "proxy" block. Add its keys to
regression_baselines (e.g. ``diversity.distinct_n``) so diff_turn guards
against style-narrowing regressions (修坏例把风格修窄了).

Usage: audit_diversity.py OUT_DIR_OR_JSONL [--profile ...] [--report out_audit_div.json]
Exit: 0 ok/warn; 1 hard fail = quota file unreadable or zero parseable lines.
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


def extract_query(d: dict, kind: str) -> str:
    """Pull the query/question text from either line format."""
    if kind == "emb":
        try:
            return str(d["messages"][0]["content"]).replace("<image>", "").strip()
        except (KeyError, IndexError, TypeError):
            return ""
    raw = d.get("messages") or d.get("conversations") or []
    for m in raw:
        role = m.get("role") or m.get("from")
        if role in ("user", "human"):
            c = m.get("content", m.get("value", ""))
            return str(c or "").replace("<image>", "").strip()
    return ""


def distinct_n(queries: list[str], n: int) -> float:
    grams = []
    for q in queries:
        toks = list(q) if not re.search(r"[a-zA-Z0-9 ]", q) else re.findall(r"\w+", q.lower())
        grams += [tuple(toks[i:i + n]) for i in range(max(len(toks) - n + 1, 0))]
    return len(set(grams)) / len(grams) if grams else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="out dir or samples_*.jsonl")
    ap.add_argument("--profile", default=os.path.join(os.path.dirname(__file__), "..", "profiles", "tech_manual.yaml"))
    ap.add_argument("--report", default=None, help="machine-readable summary json")
    a = ap.parse_args()

    prof = load_profile(a.profile)
    div = prof.get("diversity") or {}
    if not div:
        print("[SKIP] no diversity: section in profile — nothing to check")
        return 0

    path = a.target
    cand = [path] if path.endswith(".jsonl") else [
        os.path.join(path, f) for f in ("samples_infonce.jsonl", "samples_vqa.jsonl")]
    jsonl = next((c for c in cand if os.path.exists(c)), None)
    if jsonl is None:
        print(f"[FAIL] no samples jsonl under {path}")
        return 1
    kind = "emb" if "infonce" in os.path.basename(jsonl) else "vqa"

    # per-axis / per-source counters from .meta.jsonl
    meta_path = jsonl + ".meta.jsonl"
    types, sources = collections.Counter(), collections.Counter()
    if os.path.exists(meta_path):
        for line in open(meta_path, encoding="utf-8"):
            m = json.loads(line)
            if m.get("type") is not None:
                types[m["type"]] += 1
            sources[m.get("source", m.get("type", "?"))] += 1

    queries, bad_lines = [], 0
    for line in open(jsonl, encoding="utf-8"):
        if not line.strip():
            continue
        try:
            q = extract_query(json.loads(line), kind)
        except json.JSONDecodeError:
            bad_lines += 1
            continue
        if q:
            queries.append(q)
    if not queries:
        print("[FAIL] 0 parseable queries — cannot measure diversity")
        return 1

    n = len(queries)
    print(f"== diversity audit: {n} queries ({kind}, {os.path.basename(jsonl)}) ==")
    metrics: dict = {}
    warn_any = False

    # 1. length buckets
    buckets = div.get("query_len_buckets") or {}
    lens = [len(q) for q in queries]
    for name, cfg in buckets.items():
        lo = int(cfg.get("min", 0))
        hi = int(cfg.get("max", 10**9))
        share = sum(lo <= L <= hi for L in lens) / n
        floor = float(cfg.get("min_ratio", 0))
        ok = share >= floor
        warn_any |= not ok
        metrics[f"len_{name}_ratio"] = round(share, 4)
        print(f"[{'OK' if ok else 'WARN'}] len {name:<6} ({lo}-{hi if hi < 10**8 else '∞'} chars): "
              f"{share:.0%} < {floor:.0%} min" if not ok else
              f"[OK] len {name:<6} ({lo}-{hi if hi < 10**8 else '∞'} chars): {share:.0%} (floor {floor:.0%})")

    # 2. colloquial share
    markers = [str(m) for m in div.get("colloquial_markers") or []]
    floor = float(div.get("colloquial_min_ratio", 0))
    if markers and floor:
        pat = re.compile("|".join(map(re.escape, markers)))
        share = sum(bool(pat.search(q)) for q in queries) / n
        ok = share >= floor
        warn_any |= not ok
        metrics["colloquial_ratio"] = round(share, 4)
        print(f"[{'OK' if ok else 'WARN'}] colloquial style: {share:.0%} (floor {floor:.0%}, "
              f"markers {len(markers)})")

    # 3. distinct-n
    dn = int(div.get("distinct_n", 2))
    dmin = float(div.get("distinct_min", 0))
    if dmin:
        val = distinct_n(queries, dn)
        ok = val >= dmin
        warn_any |= not ok
        metrics[f"distinct_{dn}"] = round(val, 4)
        print(f"[{'OK' if ok else 'WARN'}] distinct-{dn}: {val:.3f} (floor {dmin}) "
              + ("← 句式指纹" if not ok else ""))

    # 4. per-axis floor (embedding only)
    floor = int(div.get("min_per_axis", 0))
    if floor and types:
        lows = {t: c for t, c in types.items() if c < floor}
        metrics["axes_below_floor"] = len(lows)
        print(f"[{'OK' if not lows else 'WARN'}] per-axis ≥{floor}: "
              + (f"{len(lows)} axis below ({dict(sorted(lows.items()))})" if lows
                 else f"all {len(types)} axes ok"))

    # 5. per-source floor (vqa only)
    floor = int(div.get("min_per_source", 0))
    if floor and sources and kind == "vqa":
        known = {str(s) for s in prof.get("vqa_sources") or []}
        lows = {s: c for s, c in sources.items() if c < floor and str(s) in known}
        metrics["sources_below_floor"] = len(lows)
        print(f"[{'OK' if not lows else 'WARN'}] per-source ≥{floor}: "
              + (f"{len(lows)} below ({lows})" if lows else f"all ok"))
        unk = [s for s in sources if str(s) not in known]
        if unk:
            print(f"[WARN] unknown sources (audit_vqa 也会报): {', '.join(sorted(map(str, unk)))}")

    if bad_lines:
        print(f"[WARN] {bad_lines} unparseable lines skipped")

    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump({"kind": f"diversity_{kind}", "samples": n, "metrics": metrics,
                       "warn": warn_any}, f, ensure_ascii=False, indent=2)
        print(f"[INFO] report → {a.report}")
    print("[HINT] 将 metrics 进 regression_baselines(key: diversity.*)可让 diff_turn 守卫风格收窄")
    return 0


if __name__ == "__main__":
    sys.exit(main())
