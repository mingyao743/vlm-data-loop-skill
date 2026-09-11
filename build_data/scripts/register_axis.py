#!/usr/bin/env python3
"""Suggest new-axis vs known-regression dedup for badcases ([auto], human confirms).

Reads badcases.jsonl (from parse_eval) + profile similarity_axes / badcase_registry,
classifies each cluster as: known | regression | new_axis_candidate. Prints a
suggestion table. Does NOT mutate the profile — ``--apply`` writes a *suggested
patch yaml* for human review (活/冻结: axis registration is a [manual]/alive
decision; references/flywheel.md §4).

Usage: register_axis.py BADCASES.jsonl [--profile ...] [--apply patch.yaml]
Exit: 0 (suggestion tool, not a gate); 1 on missing input.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def load_profile(path: str | None) -> dict:
    default = {"similarity_axes": [], "badcase_registry": []}
    if not path:
        return default
    if yaml is None:
        print(f"[FAIL] pyyaml not installed; cannot load profile {path}")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return {**default, **(yaml.safe_load(f) or {})}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="badcases.jsonl, or dir containing it")
    ap.add_argument("--profile", default=os.path.join(os.path.dirname(__file__), "..", "profiles", "tech_manual.yaml"))
    ap.add_argument("--apply", default=None, help="write a suggested profile patch to this path")
    a = ap.parse_args()

    profile = load_profile(a.profile)
    axes = profile.get("similarity_axes") or []
    next_id = max([int(x.get("id", 0)) for x in axes] or [0]) + 1
    known_names = {str(x.get("name")) for x in axes if x.get("name")}
    known_ids = {str(x.get("id")) for x in axes if x.get("id") is not None}
    registry = {str(r.get("axis")): r for r in (profile.get("badcase_registry") or []) if r.get("axis")}

    jsonl = a.target if a.target.endswith(".jsonl") else os.path.join(a.target, "badcases.jsonl")
    if not os.path.exists(jsonl):
        print(f"[FAIL] not found: {jsonl}")
        return 1

    rows = [json.loads(l) for l in open(jsonl, encoding="utf-8") if l.strip()]
    print(f"== {len(rows)} badcase clusters ==")

    new_axes = []
    reg_updates = []
    for r in rows:
        key = r.get("axis_or_source")
        nov = r.get("novelty")
        if r.get("kind") != "emb":
            print(f"  [INFO] {key:<22} novelty={nov:<10} (非 embedding 轴,不走注册)")
            continue
        known = key in known_names or key in known_ids
        if nov == "new":
            suggest_id = next_id
            print(f"  [SUGGEST-NEW] axis={key!r} → 建议注册 similarity_axes id={suggest_id}, "
                  f"并先过 capability_gates (hard_neg_supply) 再 re-pilot")
            new_axes.append({"id": suggest_id, "name": key, "status": "candidate",
                             "note": "由 register_axis 建议待确认;需先过 capability_gates"})
            next_id += 1
        elif nov == "regression":
            reg = registry.get(key)
            prev = reg.get("last_fail_count") if reg else None
            # P0-3: count-based regression is a HINT. Metric-level guard
            # (diff_turn.py, recall/acc-based) is authoritative; a count rise
            # alone must NOT block sedimentation or inflate leverage.
            print(f"  [HINT-REG] axis={key!r} freq={r.get('freq')} > last={prev} "
                  f"→ 失败次数上升(count hint);是否真回归以 diff_turn 指标级仲裁为准")
            reg_updates.append({"axis": key, "last_fail_count": r.get("freq"),
                                "status": (reg or {}).get("status", "active")})
        else:
            print(f"  [KNOWN] axis={key!r} → 更新 last_fail_count={r.get('freq')} 作为下回合基线")
            reg_updates.append({"axis": key, "last_fail_count": r.get("freq"),
                                "status": (registry.get(key) or {}).get("status", "active")})

    if a.apply and (new_axes or reg_updates):
        patch = {}
        if new_axes:
            patch["similarity_axes_append"] = new_axes
        if reg_updates:
            patch["badcase_registry_replace"] = reg_updates
        with open(a.apply, "w", encoding="utf-8") as f:
            yaml.safe_dump(patch, f, allow_unicode=True, sort_keys=False)
        print(f"[INFO] suggested patch written to {a.apply} (review & merge into profile)")
    elif a.apply:
        print("[INFO] --apply given but nothing to patch (all known, no regression/new)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
