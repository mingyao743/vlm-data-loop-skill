#!/usr/bin/env python3
"""Audit InfoNCE JSONL output (embedding line).

Platform-format contract: schemas/<platform>/embedding_*.md.
Domain rules (pollution patterns, fake-negative rules, page-id regex)
are loaded from a profile YAML (default: profiles/tech_manual.yaml).

Checks (methodology.md §3 items marked [auto]):
  1. schema alignment (<image> counts, positive==1, neg len match)
  2. false negatives, per profile rules (same page / adjacency / same key)
  3. caption pollution (profile word list) + over-long positives
  4. axis balance: sample counts per type

Usage: audit_embedding.py OUT_DIR_OR_JSONL [--profile profiles/tech_manual.yaml]
         [--fn-threshold 0.05] [--max-text-len 800]
Exit: 0 ok/warn, 1 hard fail (schema misalignment or FN ratio above threshold).
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
    default = {
        "asset_convention": {"page_id_regex": r"^(?P<doc_id>.+)_p(?P<page>\d+)_b\.png$"},
        "pollution_patterns": [],
        "fake_negative_rules": [],
    }
    if not path:
        return default
    if yaml is None:
        print(f"[FAIL] pyyaml not installed; cannot load profile {path}")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        p = yaml.safe_load(f) or {}
    merged = {**default, **p}
    if "page_id_regex" not in merged.get("asset_convention", {}):
        merged["asset_convention"]["page_id_regex"] = default["asset_convention"]["page_id_regex"]
    return merged


class Rules:
    """Precompiled per-profile rules."""

    def __init__(self, profile: dict):
        conv = profile.get("asset_convention", {})
        self.page_re = re.compile(conv.get("page_id_regex", ""))
        pats = [str(p) for p in profile.get("pollution_patterns") or []]
        if pats:
            flags = re.I if any(p.startswith("(?i)") for p in pats) else re.NOFLAG
            joined = "|".join(p.removeprefix("(?i)") for p in pats)
            self.pollution_re = re.compile(joined, flags)
        else:
            self.pollution_re = None

        self.rule_same_page = False
        self.rule_adjacent = None      # int distance or None
        self.rule_same_key = None      # key field name or None
        for r in profile.get("fake_negative_rules") or []:
            rule, args = r.get("rule"), r.get("args") or {}
            if rule == "same_page":
                self.rule_same_page = True
            elif rule == "adjacent_pages":
                self.rule_adjacent = int(args.get("distance", 2))
            elif rule == "same_key":
                self.rule_same_key = args.get("key_field", "chapter")
            else:
                print(f"[WARN] unknown fake-negative rule ignored: {rule!r}")

    def page_id(self, path: str):
        """Asset path -> (doc_id, page) per profile regex, else None."""
        m = self.page_re.match(os.path.basename(path))
        return (m.group("doc_id"), int(m.group("page"))) if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="out dir or path to samples_infonce.jsonl")
    ap.add_argument("--profile", default=os.path.join(os.path.dirname(__file__), "..", "profiles", "tech_manual.yaml"))
    ap.add_argument("--fn-threshold", type=float, default=0.05, help="hard-fail FN ratio (default 5%%)")
    ap.add_argument("--max-text-len", type=int, default=800, help="positive text length ceiling (default 800)")
    a = ap.parse_args()

    profile = load_profile(a.profile)
    rules = Rules(profile)

    jsonl = a.target if a.target.endswith(".jsonl") else os.path.join(a.target, "samples_infonce.jsonl")
    if not os.path.exists(jsonl):
        print(f"[FAIL] not found: {jsonl}")
        return 1

    types = collections.Counter()
    keys: dict = {}  # (doc_id, page) -> key value (e.g. chapter)
    meta_path = jsonl + ".meta.jsonl"
    if os.path.exists(meta_path):
        for line in open(meta_path, encoding="utf-8"):
            m = json.loads(line)
            types[m["type"]] += 1
            mm = m.get("meta") or {}
            if rules.rule_same_key and mm.get(rules.rule_same_key):
                keys[(mm.get("doc_id"), mm.get("page_no"))] = mm[rules.rule_same_key]

    base_dir = os.path.dirname(os.path.abspath(jsonl))
    hard_fail = False
    n = polluted = long_text = bad_asset = 0
    bad_asset_ex: list[str] = []
    fn = {"dup": 0, "adj": 0, "key": 0}
    ex: dict = {"dup": [], "adj": [], "key": []}
    neg_total = 0

    def flag(kind, line_no, pid, nid):
        fn[kind] += 1
        if len(ex[kind]) < 3:
            ex[kind].append(f"line {line_no}: {pid} <- {nid}")

    for i, line in enumerate(open(jsonl, encoding="utf-8")):
        d = json.loads(line)
        n += 1

        # 1. schema alignment (per schemas/*/embedding_*.md contract)
        try:
            q = d["messages"][0]["content"]
            pm, pi = d["positive_messages"], d["positive_images"]
            nm, ni = d.get("negative_messages", []), d.get("negative_images", [])
            ok = (q.count("<image>") == len(d.get("images", []))
                  and len(pm) == 1
                  and pm[0][0]["content"].count("<image>") == len(pi[0])
                  and len(nm) == len(ni)
                  and all(m[0]["content"].count("<image>") == len(im) for m, im in zip(nm, ni)))
        except (KeyError, IndexError, TypeError):
            ok = False
        if not ok:
            print(f"[FAIL] schema: line {i} misaligned")
            hard_fail = True

        def asset_exists(sub):
            return bool(sub) and (os.path.isfile(sub)
                                  or os.path.isfile(os.path.join(base_dir, sub)))

        def check_assets(paths):
            """Return True if every referenced image exists on disk."""
            nonlocal bad_asset
            good = True
            for p in paths or []:
                for sub in p if isinstance(p, list) else [p]:
                    if not asset_exists(sub):
                        bad_asset += 1
                        if len(bad_asset_ex) < 3:
                            bad_asset_ex.append(f"line {i}: {sub}")
                        good = False
            return good

        # 0. asset paths exist ([auto] methodology §3)
        try:
            check_assets(d.get("images", []))
            check_assets(d.get("positive_images", []))
            check_assets(d.get("negative_images", []))
        except (KeyError, IndexError, TypeError):
            pass

        # 3. pollution (profile word list) + length
        try:
            ptext = d["positive_messages"][0][0]["content"]
        except (KeyError, IndexError, TypeError):
            ptext = ""
        if rules.pollution_re and rules.pollution_re.search(ptext):
            polluted += 1
        if len(ptext) > a.max_text_len:
            long_text += 1

        # 2. false negatives (profile rules)
        try:
            pid = rules.page_id(d["positive_images"][0][0])
        except (KeyError, IndexError, TypeError):
            pid = None
        for im in d.get("negative_images", []):
            neg_total += 1
            try:
                nid = rules.page_id(im[0]) if im else None
            except (IndexError, TypeError):
                nid = None
            if nid is None or pid is None:
                continue
            if rules.rule_same_page and nid == pid:
                flag("dup", i, pid, nid)
            elif nid[0] == pid[0]:
                if rules.rule_adjacent is not None and abs(nid[1] - pid[1]) <= rules.rule_adjacent:
                    flag("adj", i, pid, nid)
                elif rules.rule_same_key:
                    pk, nk = keys.get(pid), keys.get(nid)
                    if pk and pk == nk:
                        flag("key", i, pid, nid)

    print(f"== {n} samples, {neg_total} neg slots ==")

    fn_total = sum(fn.values())
    fn_ratio = fn_total / neg_total if neg_total else 0.0
    status = "FAIL" if fn_ratio > a.fn_threshold else "OK"
    detail = " | ".join(f"{k}={v}" for k, v in fn.items() if v)
    print(f"[{status}] false negatives: {fn_ratio:.1%} (threshold {a.fn_threshold:.0%})"
          + (f" — {detail}" if detail else ""))
    for kind, lst in ex.items():
        for e in lst:
            print(f"    {e}")
    if fn_ratio > a.fn_threshold:
        hard_fail = True

    pol_pct = polluted / n if n else 0
    print(f"[{'WARN' if pol_pct > 0.3 else 'OK'}] caption pollution: {pol_pct:.0%} positives "
          f"match profile pollution patterns; {long_text / n if n else 0:.0%} exceed {a.max_text_len} chars")

    ba_pct = bad_asset / n if n else 0
    print(f"[{'FAIL' if bad_asset else 'OK'}] asset paths: {bad_asset} missing image file(s)"
          + ((" — " + "; ".join(bad_asset_ex)) if bad_asset_ex else ""))
    if bad_asset:
        hard_fail = True

    if types:
        ratio = max(types.values()) / max(1, min(types.values()))
        parts = ", ".join(f"type{t}={c}" for t, c in sorted(types.items()))
        print(f"[{'WARN' if ratio > 50 else 'OK'}] axis balance: {parts} (max:min = {ratio:.0f}:1)")
    else:
        print("[WARN] axis balance: no .meta.jsonl, cannot count per-type")

    return 1 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
