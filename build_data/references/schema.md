# Canonical Format(内部规范格式)

pipeline 产出的平台无关中间表示。两条产线(VQA / Embedding)共享同一批
图像资产与 caption/query 中间产物,仅在样本组装处分离。

## Embedding 线(对比样本)

一行 = query + 正例 + 难负(可选)+ 溯源:

```
query_text        仿真真实用户问法(query 世界观,禁 shortcut 特征)
positives         [(asset_path, text), ...]           实际语义配对
hard_negatives    [(asset_path, text), ...], 可选     分层难负
meta              {type, doc_id?, page_no?, ...}      溯源,写入 .meta.jsonl
```

约束:
- 相似轴由领域 profile 的 `similarity_axes` 定义,样本按轴标注 `type`
- 难负不得命中 profile 的 `fake_negative_rules`(同页/相邻/同 key/孪生页)
- 正例 text ≤ `max_text_len`(默认 800),且不命中 `pollution_patterns`

## VQA 线(SFT 样本)

一行 = 问题 + 答案 + 图像(可选)+ 溯源:

```
question          仿真真实用户问法,不含答案线索
answer            非空、可验证、长度 ≤ profile 的 `vqa.max_answer_len`
                  (例外:meta.sample_type ∈ {refusal, adversarial} 时,答案应为
                  拒答风格(命中 profile vqa.refusal_markers)或空 —— 见下)
image_assets      [asset_path, ...], 可选
meta              {source, sample_type?, doc_id?, page_no?, ...}

sample_type(可选,缺省 normal):
  normal      可答样本,答案非空硬约束照常
  refusal     拒答样本:图中不可见/不可答;答案 = 拒答风格或空,
              强行作答会被 audit_vqa 硬失败(教模型幻觉)
  adversarial 对抗样本:诱导性问题/异常图,测鲁棒性;语义同 refusal 豁免
配额:profile `mix:` 段(audit_mix.py 机检;不设配额 = 不混入 = 幻觉风险)
```

约束:
- 答案若来自图内内容,必须满足跨模态合法性判据(图里真实可见),防幻觉
- 问题不得包含答案子串(答案泄漏)
- 来源由 profile 的 `vqa_sources` 枚举,按来源统计配比

## 平台交付

canonical 字段到具体平台样本行的映射契约、对齐校验与已知坑,
见 [schemas/](../schemas/README.md)(如 ms-swift / LLaMA-Factory)。
pipeline 支持 `--schema` 选择目标平台格式;审计脚本按 schema 契约校验。

## 伴生产物

- `samples_*.jsonl`:训练样本
- `samples_*.jsonl.meta.jsonl`:逐样本溯源
- `cost_log.txt`:成本日志
