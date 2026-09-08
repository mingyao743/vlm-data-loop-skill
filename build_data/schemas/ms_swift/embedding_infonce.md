# ms-swift · InfoNCE embedding

适用:`swift sft --task_type embedding --loss_type infonce`(可加 `INFONCE_HARD_NEGATIVES` / `INFONCE_MASK_FAKE_NEGATIVE` 环境变量)。

## 样例行示例

```json
{"messages":[{"role":"user","content":"query 文本"}],
 "positive_messages":[[{"role":"user","content":"<image>正例文本"}]],
 "positive_images":[["pos.png"]],
 "negative_messages":[[{"role":"user","content":"<image>负例文本"}]],
 "negative_images":[["neg.png"]]}
```

- 文本 query(无图)一侧无 `<image>`/`images`;带图一侧文本中 `<image>` 占位。
- 难负为 list-of-list,可选。

## 映射规则(canonical → swift)

| canonical | swift 字段 | 说明 |
|---|---|---|
| query 文本 | `messages[0].content` | 不含 `<image>` 则为纯文本 query |
| 正例资产 + 正例文本 | `positive_messages` / `positive_images` | 外层长度必须为 1 |
| 难负资产 + 难负文本 | `negative_messages` / `negative_images` | 可省略 → in-batch 负采样 |
| 溯源(type/doc_id/page_no) | 不进入训练样本 | 写入 `.meta.jsonl` 伴生文件 |

## 对齐校验(audit 必查,违反会崩溃或静默错位)

- 任一文本字段中 `<image>` 标签数 == 其对应 images 列表长度
- `positive_messages` 外层长度必须为 1
- 无 `negative_*` 时走 in-batch 负采样;有时**全数据集负例数必须统一**(`INFONCE_HARD_NEGATIVES` 为定值)

## 已知坑

- `INFONCE_MASK_FAKE_NEGATIVE` 只是训练期兜底,数据侧规避假负例成本更低(audit 硬校验)。
- 交付前必须通过 `audit_embedding.py` 且 exit 0。
