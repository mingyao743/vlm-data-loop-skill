#!/usr/bin/env python3
"""Regression guard across flywheel turns ([auto]).

Guards, in precedence order (metric-level wins; counts are only hints):

  0. **eval-set consistency** (P0-1): if the latest turn's ``eval_set_sha``
     differs from the previous turn's, REFUSE to compare (exit 2) — a changed
     exam makes recall deltas meaningless. 考卷不一致,先解决考卷再谈回归.
  1. **turn-over-turn** (robust live detector): latest vs previous turn, for
     both ``eval`` metrics and ``proxy`` metrics (audit ratios — regression
     interception at 秒级, not 天级, P1-1).
  2. **historical-best**: latest vs profile ``regression_baselines`` best.
     ``best: null`` = no baseline yet → this turn establishes it.

Exit codes: 0 ok · 1 regression · 2 eval-set mismatch (not comparable).
Exit 1 blocks sedimentation (flywheel §3); exit 2 blocks comparison entirely.

Also prints stop criteria (advisory): plateau over N turns, marginal ROI below
``flywheel.roi_floor`` (fallback selection.roi_floor) — flywheel §2/§5, P1-4.

Usage: diff_turn.py TURN_LOG.jsonl [--profile ...] [--tolerance 0] [--update]
  --update : write improved/new baselines to NEW_baselines.yaml for human review
             (does NOT mutate the profile in place; 活/冻结: sedimentation is alive).
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
    default = {"regression_baselines": []}
    if not path:
        return default
    if yaml is None:
        print(f"[FAIL] pyyaml not installed; cannot load profile {path}")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return {**default, **(yaml.safe_load(f) or {})}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _worse(cur, ref, lower_is_better, tol):
    """True if cur is worse than ref beyond tolerance."""
    return (cur > ref + tol) if lower_is_better else (cur < ref - tol)


def _status(cur, ref, lib, tol):
    return "REGRESS" if _worse(cur, ref, lib, tol) else ("BETTER" if _worse(ref, cur, lib, tol) else "OK")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="turn_log.jsonl, or dir containing it")
    ap.add_argument("--profile", default=os.path.join(os.path.dirname(__file__), "..", "profiles", "tech_manual.yaml"))
    ap.add_argument("--tolerance", type=float, default=0.0, help="allowed regression (default 0)")
    ap.add_argument("--update", action="store_true", help="write improved/new baselines to NEW_baselines.yaml")
    a = ap.parse_args()

    profile = load_profile(a.profile)
    baselines = {str(b["key"]): b for b in (profile.get("regression_baselines") or []) if b.get("key")}

    log = a.target if a.target.endswith(".jsonl") else os.path.join(a.target, "turn_log.jsonl")
    if not os.path.exists(log):
        print(f"[FAIL] not found: {log}")
        return 1

    turns = [json.loads(l) for l in open(log, encoding="utf-8") if l.strip()]
    if not turns:
        print("[WARN] empty turn_log")
        return 0
    latest, prev = turns[-1], (turns[-2] if len(turns) >= 2 else None)
    ev_l, pr_l = latest.get("eval") or {}, latest.get("proxy") or {}
    ev_p, pr_p = (prev.get("eval") or {}, prev.get("proxy") or {}) if prev else ({}, {})
    label = f"latest turn {latest.get('turn')} eval={ev_l} proxy={pr_l}"
    print("== " + label + (f" | prev turn {prev.get('turn')}" if prev else "") + " ==")

    # ---------- 0. eval-set consistency gate (P0-1) ----------
    if prev:
        s_l, s_p = latest.get("eval_set_sha"), prev.get("eval_set_sha")
        if s_l and s_p and s_l != s_p:
            print(f"[MISMATCH] eval_set_sha 变了: prev={s_p} → latest={s_l}")
            print("           考卷不同,指标变化不可比 —— 回归判定拒绝执行 (exit 2)。")
            print("           修复:要么回滚 eval 集,要么 --update 重置基线后把本回合当新考卷的第一圈。")
            return 2
        if s_l and s_p and s_l == s_p:
            print(f"[OK] eval_set_sha 一致 ({s_l}) — 考卷可比")

    regressed = False
    improved = []
    if not baselines:
        print("[WARN] no regression_baselines in profile; only deltas reported (no direction)")
        for blk, cur_, prev_ in (("eval", ev_l, ev_p), ("proxy", pr_l, pr_p)):
            for k in cur_:
                if prev and k in prev_:
                    d, p = _num(cur_[k]), _num(prev_[k])
                    if d is not None and p is not None:
                        print(f"  [INFO] {blk}.{k}: {p:.4f} -> {d:.4f} (delta {d-p:+.4f}, direction undeclared)")
    else:
        for blk, cur_map, prev_map in (("eval", ev_l, ev_p), ("proxy", pr_l, pr_p)):
            for key, b in baselines.items():
                if "." in key:                      # allow explicit "proxy.fn_ratio" form
                    blk_, key = key.split(".", 1)
                    if blk_ != blk:
                        continue
                cur = _num(cur_map.get(key))
                if cur is None:
                    continue
                lib = bool(b.get("lower_is_better", False))
                line = f"  {blk}.{key}: cur={cur:.4f}"

                # ---------- 2. historical-best ----------
                best_raw = b.get("best")
                if best_raw is None:
                    line += " best=null(BASELINE)"
                else:
                    best = float(best_raw)
                    if _worse(cur, best, lib, a.tolerance):
                        print(f"[FAIL]{line} best={best:.4f} lib={lib}  ← REGRESSION vs 历史最佳 (回合不沉淀,动作回滚)")
                        regressed = True
                        continue
                    line += f" best={best:.4f}"

                # ---------- 1. turn-over-turn ----------
                pv = _num(prev_map.get(key)) if prev else None
                if pv is not None:
                    if _worse(cur, pv, lib, a.tolerance):
                        print(f"[FAIL]{line} prev={pv:.4f}  ← REGRESSION vs 上一回合")
                        regressed = True
                        continue
                    line += f" prev={pv:.4f}"

                st = None
                if best_raw is None:
                    st = "BASELINE"
                else:
                    st = _status(cur, float(best_raw), lib, a.tolerance)
                if st == "BASELINE":
                    print(f"[BASELINE]{line} (无基线,本回合建立)")
                    improved.append({"key": key, "best": cur, "lower_is_better": lib})
                elif st == "BETTER":
                    print(f"[BETTER]{line} (新最佳,建议 --update 沉淀)")
                    improved.append({"key": key, "best": cur, "lower_is_better": lib})
                else:
                    print(f"[OK]{line}")

    # ---------- P1-4: stop criteria (advisory) ----------
    if prev is not None:
        deltas = [t.get("true_r_delta") for t in turns if t.get("true_r_delta") is not None]
        pw = profile.get("flywheel") or {}
        plateau_n = int(pw.get("plateau_turns", 2))
        roi_floor = float(pw.get("roi_floor") if pw.get("roi_floor") is not None
                          else (profile.get("selection") or {}).get("roi_floor") or 0.0)
        if len(deltas) >= plateau_n and all(d <= 0 for d in deltas[-plateau_n:]):
            print(f"[STOP?] plateau: 最近 {plateau_n} 轮 true_r_delta<=0 → 自动停 (flywheel §2)")
        costs = [t.get("cost") for t in turns if t.get("cost") is not None]
        if roi_floor and len(costs) >= 2 and deltas:
            # marginal: last delta / (cost increase across those turns)
            span = costs[-1] - costs[-min(len(costs), len(deltas))]
            if span > 0:
                mr = deltas[-1] / span
                if mr < roi_floor:
                    print(f"[STOP?] 边际 ROI {mr:+.4f}/元 < roi_floor {roi_floor} → 停 (flywheel §5)")

    if a.update and improved:
        out = os.path.join(os.path.dirname(os.path.abspath(log)), "NEW_baselines.yaml")
        with open(out, "w", encoding="utf-8") as f:
            yaml.safe_dump({"regression_baselines": improved}, f, allow_unicode=True, sort_keys=False)
        print(f"[INFO] improved/new baselines written to {out} (review & merge into profile)")

    return 1 if regressed else 0


if __name__ == "__main__":
    sys.exit(main())
