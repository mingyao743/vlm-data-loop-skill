#!/usr/bin/env python3
"""Record a flywheel turn into turn_log.jsonl ([auto]).

diff_turn.py consumes turn_log but nobody wrote it — hand-assembled JSON
drifts. This is the generator. One turn = one appended line; it never rewrites
history (append-only, 活/冻结: the log itself is frozen per line).

Each line records:
  - turn no / timestamp / data_version / profile_sha
  - eval_set_sha  ← sha256 of the eval-set content; diff_turn REFUSES to
                    compare turns whose eval sets differ (考卷一致性, P0-1)
  - eval: metric values (flat) — guarded by regression_baselines
  - proxy: audit-derived ratios (fn_ratio, pollution_ratio, ...) — guarded the
           same way, so regression interception moves from 天级 eval down to
           秒级 audit (P1-1)
  - badcases_ref / eval_result_ref — provenance links (P2-5)
  - cost (cumulative flywheel spend), true_r_delta (computed vs prev turn)

Usage:
  record_turn.py TURN_LOG.jsonl --eval-metrics metrics.json --audit-report out_audit.json
                 [--eval-result path] [--badcases path] [--data-version sha]
                 [--profile profiles/tech_manual.yaml] [--cost 2.44]

  metrics.json     : flat JSON object {"recall@10":0.71,...} (from convert_eval)
  out_audit.json   : machine-readable audit report (--report of audit_*.py);
                     its "metrics" section feeds the proxy: block
Exit: 0 appended; 1 on missing required input.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def sha_obj(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("turn_log", help="turn_log.jsonl (created/appended)")
    ap.add_argument("--eval-metrics", required=True, help="flat metrics.json from convert_eval.py")
    ap.add_argument("--audit-report", default=None, help="audit --report json (optional; feeds proxy block)")
    ap.add_argument("--eval-result", default=None, help="eval_result.jsonl path (hash recorded for eval_set_sha)")
    ap.add_argument("--badcases", default=None, help="badcases.jsonl path (provenance link)")
    ap.add_argument("--data-version", default=None, help="dataset version tag/sha")
    ap.add_argument("--profile", default=os.path.join(os.path.dirname(__file__), "..", "profiles", "tech_manual.yaml"))
    ap.add_argument("--cost", type=float, default=0.0, help="this turn's cost (元); cumulative sum stored")
    a = ap.parse_args()

    if not os.path.exists(a.eval_metrics):
        print(f"[FAIL] not found: {a.eval_metrics}")
        return 1
    eval_metrics = json.load(open(a.eval_metrics, encoding="utf-8"))
    if not isinstance(eval_metrics, dict):
        print("[FAIL] metrics.json must be a flat JSON object")
        return 1

    proxy = {}
    if a.audit_report:
        if not os.path.exists(a.audit_report):
            print(f"[FAIL] not found: {a.audit_report}")
            return 1
        rep = json.load(open(a.audit_report, encoding="utf-8"))
        proxy = rep.get("metrics") or {}

    # eval_set identity: prefer the eval-result file's content hash
    eval_set_sha = sha_file(a.eval_result) if a.eval_result and os.path.exists(a.eval_result) \
        else sha_obj(eval_metrics)

    turns = [json.loads(l) for l in open(a.turn_log, encoding="utf-8") if l.strip()] \
        if os.path.exists(a.turn_log) else []
    prev = turns[-1] if turns else None
    delta = None
    if prev:
        diffs = []
        for k, v in eval_metrics.items():
            p = prev.get("eval", {}).get(k)
            try:
                diffs.append(float(v) - float(p))
            except (TypeError, ValueError):
                pass
        delta = round(sum(diffs) / len(diffs), 4) if diffs else None

    entry = {
        "turn": (prev.get("turn", 0) + 1) if prev else 1,
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "data_version": a.data_version,
        "profile_sha": sha_file(a.profile) if os.path.exists(a.profile) else None,
        "eval_set_sha": eval_set_sha,
        "eval": eval_metrics,
        "proxy": proxy,
        "badcases_ref": os.path.abspath(a.badcases) if a.badcases else None,
        "eval_result_ref": os.path.abspath(a.eval_result) if a.eval_result else None,
        "cost": round((prev.get("cost") or 0.0) + a.cost, 4) if prev else round(a.cost, 4),
        "true_r_delta": delta,
    }

    # ---- P1-4 stop criteria (advisory, printed; flywheel §2/§5) ----
    prof = {}
    if yaml and a.profile and os.path.exists(a.profile):
        prof = yaml.safe_load(open(a.profile, encoding="utf-8")) or {}
    pw = prof.get("flywheel") or {}
    plateau_n = int(pw.get("plateau_turns", 2))
    roi_floor = float(pw.get("roi_floor") if pw.get("roi_floor") is not None
                      else (prof.get("selection") or {}).get("roi_floor") or 0.0)
    notes = []
    if prev is not None and delta is not None:
        recent = [t.get("true_r_delta") for t in turns[-(plateau_n - 1):]] + [delta]
        recent = [d for d in recent if d is not None]
        if len(recent) >= plateau_n and all(d <= 0 for d in recent):
            notes.append(f"[STOP?] plateau: 最近 {len(recent)} 轮 true_r_delta<=0 (flywheel §2)")
    if prev is not None and delta is not None and a.cost > 0:
        marginal_roi = delta / a.cost
        if roi_floor and marginal_roi < roi_floor:
            notes.append(f"[STOP?] 边际 ROI {marginal_roi:+.4f}/元 < roi_floor {roi_floor} (flywheel §5)")

    with open(a.turn_log, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"[OK] turn {entry['turn']} appended → {a.turn_log}")
    print(f"     eval_set_sha={eval_set_sha}  true_r_delta={delta}  cost={entry['cost']}")
    for nt in notes:
        print(f"  {nt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
