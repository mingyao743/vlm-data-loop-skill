# ms-swift · VQA eval（多模态）

适用：`swift eval --dataset xxx.jsonl`（eval 集与 train 同 messages 格式，语义上为 held-out +
ground truth 答案用于打分；swift 按模型模板渲染并比对）。

⚠️ eval 集复用 train 的 vqa_sft canonical，**只是 `meta.split=eval` 且答案作 ground truth**——
不是新格式，是同一 canonical 的 eval 语义切片。

## 样例行示例

```json
{"messages":[{"role":"user","content":"<image>这张图中标注的零件号是什么?"},
             {"role":"assistant","content":"图示为 XX-2044 轴承座。"}],
 "images":["pages/ABC/ABC_p0042_b.png"]}
```

（与 [vqa_sft.md](vqa_sft.md) 同；eval/train 的区分在 `.meta.jsonl` 的 `split` 字段，不在样本行。）

## 映射规则（canonical eval → swift eval）

| canonical eval | swift 字段 | 说明 |
|---|---|---|
| 问题 | `messages[user].content` | `<image>` 占位 |
| ground truth 答案 | `messages[assistant].content` | eval 时作标准答案 |
| 图像 | `images` | 长度 == `<image>` 数 |
| 溯源（source/axis/split） | 不进入样本 | 写 `.meta.jsonl`，`split=eval`，轴标签进 meta 用于 per-axis 打分 |

## 对齐校验（audit 必查）

- `images` 数 == user 文本 `<image>` 总数
- 末条 assistant 非空、可验证（ground truth 必须本身正确，否则 eval 无意义）
- eval 集来源单元与 train 集不重叠（[eval_fit.md](../../references/eval_fit.md) E4）
- 交付前 [`audit_vqa.py`](../../scripts/audit_vqa.py) exit 0（eval 集也走 vqa audit）

## 平台 eval 输出 → canonical eval-result（喂 parse_eval）

swift eval 的结果（每个样本的模型输出 vs 标准答案）按下表转成 parse_eval 的输入：

| swift eval 输出 | canonical eval-result 字段 |
|---|---|
| question + image | `question` / `image` |
| 标准答案 | `expected_answer` |
| 模型输出 | `predicted_answer` |
| 是否一致（精确匹配） | `correct` |
| `.meta.jsonl` 的 source | `source` |

转成一行：`{"kind":"vqa","question":...,"image":...,"source":...,"expected_answer":...,"predicted_answer":...,"correct":bool}`

## 已知坑

- swift eval 的打分依赖模型模板的 answer 提取；长图 + 长 QA 可能截断，pilot 先跑通。
- 语义等价判定（如"轴承座"=="XX-2044 轴承座"）swift 不内建，需配 LLM-judge 兜底
  （[eval_fit.md](../../references/eval_fit.md) E3）。
- eval 集答案本身错 → 误报假阳；eval 集上线前必须 `[manual]` 抽核 ground truth。
