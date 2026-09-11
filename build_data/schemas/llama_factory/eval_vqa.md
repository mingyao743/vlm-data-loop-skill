# LLaMA-Factory · VQA eval（多模态）

适用：LF 的 eval 能力有限（训练导向），VQA eval 通常用 `llamafactory-cli eval` 或直接
generate + 比对。格式与 train 同 sharegpt，语义为 held-out + ground truth。
⚠️ LF **不支持 embedding eval**（同 [vqa_sft.md](vqa_sft.md) 契约）。

## 样例行示例

```json
{"conversations":[{"from":"human","value":"<image>图中标注的零件号是什么?"},
                  {"from":"gpt","value":"图示为 XX-2044 轴承座。"}],
 "images":["pages/ABC/ABC_p0042_b.png"]}
```

（与 [vqa_sft.md](vqa_sft.md) 同；eval/train 区分在 `.meta.jsonl` 的 `split`。）

## 映射规则（canonical eval → LF eval）

| canonical eval | LF 字段 | 说明 |
|---|---|---|
| 问题 | `conversations[human].value` | `<image>` 占位 |
| ground truth 答案 | `conversations[gpt].value` | eval 时作标准答案 |
| 图像 | `images` | 长度 == `<image>` 数 |
| 溯源 | 不进样本 | `.meta.jsonl`，`split=eval` |

## 对齐校验

- `images` 数 == human 文本 `<image>` 数
- 末条 gpt 非空、role 名必须是 `human/gpt`（错名会静默当纯文本跳过图）
- eval 集需在 `dataset_info.json` 注册（同 train 契约）
- 交付前 [`audit_vqa.py`](../../scripts/audit_vqa.py) exit 0（audit 兼容两种 role 命名）

## 平台 eval 输出 → canonical eval-result（喂 parse_eval）

LF eval / generate 的结果（每个样本的模型输出 vs 标准答案）转成 parse_eval 输入：

| LF 输出 | canonical eval-result 字段 |
|---|---|
| question + image | `question` / `image` |
| 标准答案（gpt value） | `expected_answer` |
| 模型输出 | `predicted_answer` |
| 是否一致 | `correct` |
| `.meta.jsonl` 的 source | `source` |

## 已知坑

- LF eval 的答案比对通常是精确匹配，语义等价需外挂 LLM-judge
  （[eval_fit.md](../../references/eval_fit.md) E3）。
- role 命名错 = 静默失图（eval/train 同坑），交付前务必核对 `from: human/gpt`。
