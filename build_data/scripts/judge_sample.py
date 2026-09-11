#!/usr/bin/env python3
"""Judge-tier sampling: VLM spot-check vs proxy (audit) verdicts (P2-1).

The middle tier of the observation pyramid (methodology.md §0) existed only as
prose. Its ONE job: measure how far proxy (audit's deterministic checks)
drifts from judged reality — if the drift rate grows, the proxy itself is
lying and audit thresholds need recalibration.

Flow:
  1. sample N lines from samples_*.jsonl (random, --seed reproducible)
  2. for each, run deterministic proxy checks locally (leakage / emptiness /
     pollution pattern)
  3. ask a VLM (OpenAI-compatible endpoint) to judge answer quality with a
     fixed rubric: faithful-to-image / answers-question / no-hallucination
  4. emit per-sample agreement + drift rate:
       drift = P(judge disagrees with proxy signal)
     High drift on a specific check ⇒ that proxy check is unreliable.
  5. self-consistency (标注一致性,P2-4 延伸): --n-judges K>1 时同一样本独立判定
     K 次,输出 per-field agreement rate(简单一致率,样本量小不做 κ);低于
     profile judge.min_agreement(默认 0.85)→ rubric/模型不稳定告警。这也是
     人工标注验收的同构协议:把 VLM 换成第二个标注员即得到人-人一致率。

Offline fallback: --no-vlm runs only steps 1-2 & reports the distribution the
judge WOULD need to verify (still useful to size the spot-check batch).

Usage: judge_sample.py SAMPLES.jsonl [--n 30] [--endpoint URL --api-key KEY --model NAME]
        [--profile ...] [--no-vlm] [--out judge_report.json]
Exit: 0 ok; 1 on missing input / API failure.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import sys
import urllib.request

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def load_profile(path: str | None) -> dict:
    if not path or yaml is None or not os.path.exists(path):
        return {}
    return yaml.safe_load(open(path, encoding="utf-8")) or {}


def norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s.lower())


def proxy_checks(d: dict, pollution_re) -> dict:
    """Same deterministic signals audit_*.py uses ([auto] tier)."""
    raw = d.get("messages") or d.get("conversations") or []
    role = lambda m: m.get("role") or m.get("from")
    cont = lambda m: (m.get("content") if m.get("content") is not None else m.get("value", "")) or ""
    msgs = [{"role": role(m), "content": str(cont(m))} for m in raw]
    q = " ".join(m["content"] for m in msgs if m["role"] in {"user", "human"})
    ans = next((m["content"] for m in reversed(msgs) if m["role"] in {"assistant", "gpt"}), "")
    img_count_ok = sum(m["content"].count("<image>") for m in msgs if m["role"] in {"user", "human"}) == len(d.get("images") or [])
    return {
        "proxy_empty_answer": not ans.strip(),
        "proxy_leakage": len(norm(ans)) >= 4 and norm(ans) in norm(q.replace("<image>", "")),
        "proxy_schema_misaligned": not img_count_ok,
        "proxy_pollution": bool(pollution_re and pollution_re.search(next(
            (m["content"] for m in msgs if m["role"] in {"assistant", "gpt"}), ""))),
        "_q": q, "_ans": ans,
    }


RUBRIC = (
    "你是数据质量裁判。根据图片判断这个回答:1)faithful:回答声称的内容是否真实可见于图片;"
    "2)answers:是否回应了问题;3)hallucination:是否有图内不存在的编造细节。"
    "只输出 JSON:{\"faithful\":bool,\"answers\":bool,\"hallucination\":bool}"
)


def vlm_judge(endpoint: str, api_key: str, model: str, q: str, ans: str, image_path: str | None) -> dict | None:
    content = [{"type": "text", "text": f"问题:{q}\n回答:{ans}\n\n{RUBRIC}"}]
    if image_path and os.path.isfile(image_path):
        b64 = base64.b64encode(open(image_path, "rb").read()).decode()
        mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"}})
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/chat/completions",
        data=json.dumps({"model": model, "temperature": 0,
                         "messages": [{"role": "user", "content": content}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            txt = json.load(r)["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", txt, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] VLM call failed: {e}")
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="samples_vqa.jsonl / samples_infonce.jsonl (or dir)")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--endpoint", default=os.environ.get("VLM_BASE_URL", ""))
    ap.add_argument("--api-key", default=os.environ.get("VLM_API_KEY", ""))
    ap.add_argument("--model", default=os.environ.get("VLM_MODEL", ""))
    ap.add_argument("--profile", default=os.path.join(os.path.dirname(__file__), "..", "profiles", "tech_manual.yaml"))
    ap.add_argument("--no-vlm", action="store_true", help="proxy-only: report what judge must verify")
    ap.add_argument("--n-judges", type=int, default=1, help="独立判定次数(>1 时输出 self-consistency)")
    ap.add_argument("--out", default=None, help="judge_report.json path")
    a = ap.parse_args()

    jsonl = a.target if a.target.endswith(".jsonl") else os.path.join(a.target, "samples_vqa.jsonl")
    if not os.path.exists(jsonl):
        print(f"[FAIL] not found: {jsonl}")
        return 1

    prof = load_profile(a.profile)
    pats = [str(p).removeprefix("(?i)") for p in prof.get("pollution_patterns") or []]
    pollution_re = re.compile("|".join(pats), re.I) if pats else None

    lines = [l for l in open(jsonl, encoding="utf-8") if l.strip()]
    sample = random.Random(a.seed).sample(lines, min(a.n, len(lines)))
    base_dir = os.path.dirname(os.path.abspath(jsonl))

    rows, api_fail, warned_ep = [], 0, False
    for l in sample:
        d = json.loads(l)
        pc = proxy_checks(d, pollution_re)
        row = {"proxy": {k: v for k, v in pc.items() if k.startswith("proxy_")}}
        imgs = d.get("images") or []
        img = os.path.join(base_dir, imgs[0]) if imgs and not os.path.isabs(imgs[0]) else (imgs[0] if imgs else None)
        if a.no_vlm:
            row["judge"] = None
        elif not (a.endpoint and a.api_key and a.model):
            if not warned_ep:
                print("[SKIP] 未配置 VLM 端点(--endpoint/--api-key/--model 或环境变量)— 仅出 proxy 检查")
                warned_ep = True
            row["judge"] = None
        elif a.n_judges > 1:
            judges = [vlm_judge(a.endpoint, a.api_key, a.model, pc["_q"], pc["_ans"], img)
                      for _ in range(a.n_judges)]
            row["judges"] = judges
            row["judge"] = next((j for j in judges if j is not None), None)
            api_fail += sum(1 for j in judges if j is None)
        else:
            j = vlm_judge(a.endpoint, a.api_key, a.model, pc["_q"], pc["_ans"], img)
            row["judge"] = j
            if j is None:
                api_fail += 1
        rows.append(row)

    # drift: judge contradicts proxy signal, per check
    drift = {}
    for chk in ["proxy_empty_answer", "proxy_leakage", "proxy_pollution", "proxy_schema_misaligned"]:
        both = [r for r in rows if r["judge"] is not None and chk in r["proxy"]]
        if not both:
            continue
        disagree = 0
        for r in both:
            flagged = r["proxy"][chk]
            j = r["judge"]
            if chk == "proxy_leakage":
                disagree += int(flagged and not (j.get("faithful") is False))
            elif chk == "proxy_pollution":
                disagree += int(flagged and j.get("faithful", True))
            else:
                disagree += int(flagged and j.get("answers", True))
        drift[chk] = {"n": len(both), "disagree": disagree, "drift_rate": round(disagree / len(both), 3)}

    rep = {"samples": len(rows), "api_failures": api_fail, "drift": drift}

    # self-consistency: agreement rate per rubric field across independent judgments
    if a.n_judges > 1:
        fields = ["faithful", "answers", "hallucination"]
        agg = {}
        for fld in fields:
            pairs = total = 0
            for r in rows:
                js = [j for j in r.get("judges", []) if j is not None]
                vals = [j.get(fld) for j in js if j.get(fld) is not None]
                for i in range(len(vals)):
                    for k in range(i + 1, len(vals)):
                        total += 1
                        pairs += int(vals[i] == vals[k])
            agg[fld] = round(pairs / total, 3) if total else None
        rep["self_consistency"] = {"n_judges": a.n_judges, "agreement": agg}
        min_agr = float((prof.get("judge") or {}).get("min_agreement", 0.85))
        print(f"== self-consistency (K={a.n_judges} independent judges) ==")
        for fld, v in agg.items():
            if v is None:
                continue
            flag = " ← UNSTABLE" if v < min_agr else ""
            print(f"  {fld:<14} agreement={v:.0%}{flag}")
        low = [f for f, v in agg.items() if v is not None and v < min_agr]
        if low:
            print(f"[WARN] 字段 {low} 一致率 < {min_agr:.0%} — rubric 或模型不稳定,"
                  f"抽检结论不可信;先修 rubric(更可判定的表述)或换更强裁判模型")
    out = a.out or os.path.join(base_dir, "judge_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    print(f"== judge sampling: {len(rows)} rows ({api_fail} api failures) ==")
    for chk, s in drift.items():
        flag = " ← PROXY DRIFT" if s["n"] and s["disagree"] else ""
        print(f"  {chk:<26} flagged={s['disagree']}/{s['n']} drift={s['drift_rate']:.0%}{flag}")
    print(f"[INFO] report → {out}")
    if drift and any(s["n"] and s["disagree"] and s["drift_rate"] > 0.2 for s in drift.values()):
        print("[WARN] drift_rate > 20% on some check — proxy 判定与 judge 相左,复核 audit 阈值")
    return 0 if (a.no_vlm or api_fail < len(rows)) else 1


if __name__ == "__main__":
    sys.exit(main())
