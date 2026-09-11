# ms-swift · embedding eval（Recall@k / IR 评测）

适用：**BEIR/TREC 风格三元组**，任何 IR eval 工具（mteb / 自写 recall 脚本）可消费。
swift 本身是训练导向，**不内建 recall@k eval**；eval run 用外部 IR eval 脚本，本文件只定
**格式契约**。

## 三件套

```
queries.jsonl     # {id, text}                 ← canonical 的 query_text
corpus.jsonl      # {id, text, image?}         ← canonical 的 positives/negatives 去重成池
qrels.tsv         # query_id \t corpus_id \t rel(0/1)   ← canonical 的 positives 即 rel=1
```

canonical 的 positives 直接就是 qrels 的正例（rel=1）；难负不入 qrels（它们是 train 用的，
eval 的 qrels 只标真实相关）。

## 映射规则（canonical → eval 三元组）

| canonical | eval 三元组 | 说明 |
|---|---|---|
| query_text | `queries.jsonl` 的 text | query 不带图 |
| positives（asset,text）去重 | `corpus.jsonl` + `qrels.tsv` rel=1 | positive 既进 corpus 又标相关 |
| 轴 / 来源 | 不进三元组 | 写 `.meta.jsonl`，`split=eval`，轴标签进 queries 的 meta 用于 per-axis recall |

## 对齐校验

- 每个 query 至少 1 条 rel=1（qrels 非空）
- corpus 覆盖所有 qrels 引用的 id
- eval 集 query 的来源单元与 train 不重叠（[eval_fit.md](../../references/eval_fit.md) E4）
- per-axis query 数 ≥ 50（eval_fit E4），否则该轴 recall 不可单独信

## 平台 eval 输出 → canonical eval-result（喂 parse_eval）

IR eval 工具产出的 per-query recall（query_id、是否 hit@k、top-k 命中、expected 正例 id）按下表转：

| IR eval 输出 | canonical eval-result 字段 |
|---|---|
| query 文本 | `query` |
| query_id | `query_id` |
| `.meta.jsonl` 的 axis | `axis` |
| 正例 id 列表 | `expected_pos_ids` |
| top-k 检索结果 | `predicted_topk: [{id, score}]` |
| k | `k` |
| 正例是否在 top-k | `hit_at_k` |

转成一行：`{"kind":"embedding","query":...,"query_id":...,"axis":...,"expected_pos_ids":[...],"predicted_topk":[...],"k":10,"hit_at_k":bool}`

## 已知坑

- recall@k 的 k 要在 eval 脚本侧统一（常用 1/5/10），profile `eval.metrics` 声明。
- 假阳分析（top-k 里不该命中的）需要 corpus 里有"应不相关"的池子——别把 corpus 只放 positives。
- ms-swift 的 embedding 训练形态（caption-rich）与推理形态必须对齐
  （[methodology.md](../../references/methodology.md) §1.5），eval 集的 query/corpus 文本形态
  要匹配推理，否则 recall 测的是另一个分布。
