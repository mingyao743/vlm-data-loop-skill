#!/usr/bin/env python3
"""Audit VQA/SFT JSONL output.

Platform-format contract: schemas/<platform>/vqa_sft.md.
Answer-length ceiling comes from the profile (`vqa.max_answer_len`).

Checks (methodology.md §3 analogues, marked [auto]):
  1. schema alignment (images count == <image> tags, ends with assistant)
  2. answer leakage: answer substring appears inside the question
  3. answer quality: non-empty, length ceiling
     — meta sample_type in {refusal, adversarial} 豁免非空检查,反而要求拒答风格
       (profile vqa.refusal_markers);修复"audit 系统性消灭拒答能力→教模型幻觉"的冲突
  4. source balance (from .meta.jsonl, per profile vqa_sources)

Usage: audit_vqa.py OUT_DIR_OR_JSONL [--profile ...] [--leak-check/--no-leak-check] [--report out_audit.json]
Exit: 0 ok/warn, 1 hard fail (schema misalignment or empty answer).
With --report, also writes a machine-readable summary (proxy metrics feed
record_turn.py → turn_log "proxy" block → diff_turn guards them, P1-1).
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
    default = {"vqa": {"max_answer_len": 500,
                       "refusal_markers": ["图内未见", "图中没有", "无法确定", "无法回答", "无相关信息"]},
               "vqa_sources": []}
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
    ap.add_argument("--report", default=None, help="write machine-readable summary json here")
    a = ap.parse_args()

    profile = load_profile(a.profile)
    max_len = a.max_answer_len or int(profile.get("vqa", {}).get("max_answer_len", 500))
    known_sources = profile.get("vqa_sources") or []
    refusal_re = re.compile("|".join(map(re.escape,
        profile.get("vqa", {}).get("refusal_markers") or [""]) or ["^$"]), re.I)

    jsonl = a.target if a.target.endswith(".jsonl") else os.path.join(a.target, "samples_vqa.jsonl")
    if not os.path.exists(jsonl):
        print(f"[FAIL] not found: {jsonl}")
        return 1

    # line-indexed sample_type from .meta.jsonl (normal/refusal/adversarial)
    sample_types: dict = {}
    sources = collections.Counter()
    meta_path = jsonl + ".meta.jsonl"
    if os.path.exists(meta_path):
        for li, line in enumerate(open(meta_path, encoding="utf-8")):
            m = json.loads(line)
            sample_types[li] = str(m.get("sample_type", "normal"))
            sources[m.get("source", m.get("type", "?"))] += 1

    base_dir = os.path.dirname(os.path.abspath(jsonl))
    hard_fail = False
    n = leaked = long_ans = bad_asset = refused_wrongly = 0
    leak_ex: list[str] = []
    bad_asset_ex: list[str] = []
    refusal_ex: list[str] = []

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
            if sample_types.get(i, "normal") in ("refusal", "adversarial"):
                pass  # 拒答/对抗样本的预期行为,不算失败
            else:
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

        # 拒答/对抗样本若强行作答(非拒答风格) = 教模型对不可答问题幻觉,硬失败
        if refusal_re.pattern and sample_types.get(i, "normal") in ("refusal", "adversarial") \
                and ans.strip() and not refusal_re.search(ans):
            refused_wrongly += 1
            if len(refusal_ex) < 3:
                refusal_ex.append(f"line {i}:{ans[:30]!r}")

    print(f"== {n} samples ==")

    leak_pct = leaked / n if n else 0
    print(f"[{'FAIL' if leaked else 'OK'}] answer leakage: {leaked} samples"
          + (f" — {', '.join(leak_ex)}" if leak_ex else ""))
    if leaked:
        hard_fail = True

    la_pct = long_ans / n if n else 0
    print(f"[{'WARN' if la_pct > 0.3 else 'OK'}] long answers: {la_pct:.0%} exceed {max_len} chars")

    n_r = sum(1 for t in sample_types.values() if t in ("refusal", "adversarial"))
    if n_r:
        print(f"[INFO] refusal/adversarial samples: {n_r}/{n} (empty answers exempted)")
    if refused_wrongly:
        print(f"[FAIL] refusal/adversarial samples answered non-refusally: {refused_wrongly}"
              + (f" — {', '.join(refusal_ex)}" if refusal_ex else ""))
        print("       对不可答问题强行作答 = 幻觉源头;改答案为拒答风格,或修正 sample_type")
        hard_fail = True

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

    if a.report:
        rep = {
            "kind": "vqa",
            "samples": n,
            "metrics": {
                "leakage_count": leaked,
                "leakage_ratio": round(leak_pct, 4),
                "long_answer_ratio": round(la_pct, 4),
                "bad_asset_count": bad_asset,
            },
            "hard_fail": hard_fail,
        }
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print(f"[INFO] report → {a.report}")

    return 1 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
