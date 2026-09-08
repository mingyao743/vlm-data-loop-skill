#!/usr/bin/env python3
"""Audit VQA/SFT JSONL output.

Platform-format contract: schemas/<platform>/vqa_sft.md.
Answer-length ceiling comes from the profile (`vqa.max_answer_len`).

Checks (methodology.md §3 analogues, marked [auto]):
  1. schema alignment (images count == <image> tags, ends with assistant)
  2. answer leakage: answer substring appears inside the question
  3. answer quality: non-empty, length ceiling
  4. source balance (from .meta.jsonl, per profile vqa_sources)

Usage: audit_vqa.py OUT_DIR_OR_JSONL [--profile ...] [--leak-check/--no-leak-check]
Exit: 0 ok/warn, 1 hard fail (schema misalignment or empty answer).
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
    default = {"vqa": {"max_answer_len": 500}, "vqa_sources": []}
    if not path:
        return default
    if yaml is None:
        print(f"[FAIL] pyyaml not installed; cannot load profile {path}")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        p = yaml.safe_load(f) or {}
    return {**default, **p}


def norm(s: str) -> str:
    """Normalize for leakage check: strip spaces/punct, lower-case."""
    return re.sub(r"[\s\W_]+", "", s.lower())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="out dir or path to samples_vqa.jsonl")
    ap.add_argument("--profile", default=os.path.join(os.path.dirname(__file__), "..", "profiles", "tech_manual.yaml"))
    ap.add_argument("--max-answer-len", type=int, default=None, help="override profile vqa.max_answer_len")
    ap.add_argument("--no-leak-check", action="store_true", help="disable answer-substring leakage check")
    a = ap.parse_args()

    profile = load_profile(a.profile)
    max_len = a.max_answer_len or int(profile.get("vqa", {}).get("max_answer_len", 500))
    known_sources = profile.get("vqa_sources") or []

    jsonl = a.target if a.target.endswith(".jsonl") else os.path.join(a.target, "samples_vqa.jsonl")
    if not os.path.exists(jsonl):
        print(f"[FAIL] not found: {jsonl}")
        return 1

    sources = collections.Counter()
    meta_path = jsonl + ".meta.jsonl"
    if os.path.exists(meta_path):
        for line in open(meta_path, encoding="utf-8"):
            m = json.loads(line)
            sources[m.get("source", m.get("type", "?"))] += 1

    base_dir = os.path.dirname(os.path.abspath(jsonl))
    hard_fail = False
    n = leaked = long_ans = bad_asset = 0
    leak_ex: list[str] = []
    bad_asset_ex: list[str] = []

    for i, line in enumerate(open(jsonl, encoding="utf-8")):
        d = json.loads(line)
        n += 1

        # Canonical uses role=user/assistant; sharegpt-style platforms
        # (LLaMA-Factory) use from=human/gpt with a top-level conversations key.
        raw_msgs = d.get("messages") or d.get("conversations") or []
        role_of = lambda m: m.get("role") or m.get("from")
        content_of = lambda m: m.get("content") if m.get("content") is not None else m.get("value", "")
        USER_ROLES, ASST_ROLES = {"user", "human"}, {"assistant", "gpt"}
        msgs = [{"role": role_of(m), "content": content_of(m)} for m in raw_msgs]
        ok = (
            bool(msgs)
            and all(role_of(m) for m in raw_msgs)
            and msgs[-1]["role"] in ASST_ROLES
            and sum(str(m["content"]).count("<image>")
                    for m in msgs if m["role"] in USER_ROLES) == len(d.get("images", []))
        )
        if not ok:
            print(f"[FAIL] schema: line {i} misaligned (roles / <image> count)")
            hard_fail = True

        q_text = " ".join(str(m["content"]) for m in msgs if m["role"] in USER_ROLES)
        ans = str(msgs[-1]["content"]) if msgs and msgs[-1]["role"] in ASST_ROLES else ""

        if not ans.strip():
            print(f"[FAIL] empty answer: line {i}")
            hard_fail = True
        elif len(ans) > max_len:
            long_ans += 1

        # asset paths exist ([auto] methodology §3)
        for p in d.get("images", []) or []:
            if not (p and (os.path.isfile(p) or os.path.isfile(os.path.join(base_dir, p)))):
                bad_asset += 1
                if len(bad_asset_ex) < 3:
                    bad_asset_ex.append(f"line {i}: {p}")

        n_ans = norm(ans)
        q_norm = norm(q_text.replace("<image>", ""))
        if not a.no_leak_check and len(n_ans) >= 4 and n_ans in q_norm:
            leaked += 1
            if len(leak_ex) < 3:
                leak_ex.append(f"line {i}")

    print(f"== {n} samples ==")

    leak_pct = leaked / n if n else 0
    print(f"[{'FAIL' if leaked else 'OK'}] answer leakage: {leaked} samples"
          + (f" — {', '.join(leak_ex)}" if leak_ex else ""))
    if leaked:
        hard_fail = True

    la_pct = long_ans / n if n else 0
    print(f"[{'WARN' if la_pct > 0.3 else 'OK'}] long answers: {la_pct:.0%} exceed {max_len} chars")

    print(f"[{'FAIL' if bad_asset else 'OK'}] asset paths: {bad_asset} missing image file(s)"
          + ((" — " + "; ".join(bad_asset_ex)) if bad_asset_ex else ""))
    if bad_asset:
        hard_fail = True

    if known_sources:
        unknown = [s for s in sources if s not in known_sources]
        if unknown:
            print(f"[WARN] unknown VQA sources in .meta.jsonl: {', '.join(sorted(map(str, unknown)))} "
                  f"(profile declares: {', '.join(known_sources)})")
    if sources:
        parts = ", ".join(f"{s}={c}" for s, c in sorted(sources.items(), key=lambda x: str(x[0])))
        print(f"[INFO] source balance: {parts}")
    else:
        print("[WARN] source balance: no .meta.jsonl, cannot count per-source")

    return 1 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
