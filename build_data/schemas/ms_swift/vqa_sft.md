# ms-swift · VQA / SFT(多模态)

适用:`swift sft --dataset xxx.jsonl`(多模态 messages 格式,视觉 token 由 swift 按模型模板渲染)。

## 样例行示例

纯文本答案:

```json
{"messages":[{"role":"user","content":"<image>这张图中标注的零件号是什么?"},
             {"role":"assistant","content":"图示为 XX-2044 轴承座。"}],
 "images":["pages/ABC/ABC_p0042_b.png"]}
```

无图(纯文本指令样本)则省略 `images`:

```json
{"messages":[{"role":"user","content":"如何根据故障码 E23 判断问题?"},
             {"role":"assistant","content":"先检查 …,再看 …。"}]}
```

多轮对话按 roles 顺序追加 user / assistant 即可。

## 映射规则(canonical → swift)

| canonical | swift 字段 | 说明 |
|---|---|---|
| 问题(query) | `messages[user].content` | 图像占位 `<image>` 写在 user 文本中 |
| 答案 | `messages[assistant].content` | 必须非空、可验证 |
| 图像资产 | `images` | 列表长度 == user 文本中 `<image>` 数 |
| 溯源(source/doc_id/page_no) | 不进入训练样本 | 写入 `.meta.jsonl` 伴生文件 |

## 对齐校验(audit 必查)

- `images` 数量 == 所有 user 消息中 `<image>` 标签总数
- 最后一条消息必须是 assistant;每条 assistant 非空且长度 ≤ profile 设定的上限
- 答案不得以子串形式泄漏在问题里(答案泄漏检测)

## 已知坑

- 视觉 token 数与模型相关:长图 + 长 QA 会撑爆上下文,pilot 阶段先跑通再放量。
- 交付前必须通过 `audit_vqa.py` 且 exit 0。
