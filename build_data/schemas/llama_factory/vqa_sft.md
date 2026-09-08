# LLaMA-Factory · VQA / SFT(多模态)

适用:`llamafactory-cli train xxx.yaml`(sharegpt 多模态数据集,`dataset_info.json` 注册)。
⚠️ LLaMA-Factory **不支持 embedding/对比学习任务**——embedding 线请走 `ms_swift/embedding_infonce.md`。

## 样例行示例

sharegpt 格式(`messages` 亦可,见下方映射表):

```json
{
  "conversations": [
    {"from": "human", "value": "<image>图中标注的零件号是什么?"},
    {"from": "gpt", "value": "图示为 XX-2044 轴承座。"}
  ],
  "images": ["pages/ABC/ABC_p0042_b.png"]
}
```

无图样本省略 `images` 字段;多轮按 human/gpt 交替追加。
sharegpt 占位符默认为 `<image>`;换用其他模型模板时可通过 `dataset_info.json` 的
`image_tag` 或数据集 converter 调整,但建议保持 `<image>` 与 canonical 一致。

## dataset_info.json 注册( LF 特有步骤 )

```json
{
  "my_vlm_dataset": {
    "file_name": "samples_vqa.json",
    "formatting": "sharegpt",
    "columns": {
      "messages": "conversations",
      "images": "images"
    },
    "tags": {
      "role_tag": "from",
      "content_tag": "value",
      "user_tag": "human",
      "assistant_tag": "gpt",
      "image_tag": "<image>"
    }
  }
}
```

## 映射规则(canonical → LLaMA-Factory)

| canonical | LF 字段 | 说明 |
|---|---|---|
| 问题(query) | `conversations[human].value` | `<image>` 占位写在 human 文本中 |
| 答案 | `conversations[gpt].value` | 必须非空、可验证 |
| 图像资产 | `images` | 列表长度 == human 文本中 `<image>` 数 |
| 溯源(source/doc_id/page_no) | 不进入训练样本 | 写入 `.meta.jsonl` 伴生文件 |

## 对齐校验(audit 必查)

- `images` 数量 == 所有 human 消息中 `<image>` 标签总数
- last message 必须是 gpt;每条 gpt 非空且 ≤ profile 上限
- 答案不得以子串形式泄漏在问题里
- 与 ms-swift 版相同的 `<image>` 计数规则,但 role 名不同(`human/gpt` vs `user/assistant`)——
  交付前按本表核对 role 命名,脚本 `audit_vqa.py` 对两种命名均兼容

## 已知坑

- **角色命名是 sharegpt 惯例**(`from: human/gpt`),不是 swift 的 `role: user/assistant`;
  用错 role 名会导致训练静默把样本当纯文本跳过图像。
- 数据集必须在 `dataset_info.json` 注册后才可被 `--dataset` 引用,漏注册报"dataset not found"。
- 视觉 token 数随模型模板变化(Qwen2-VL / InternVL / LLaVA 各不同),pilot 先跑通再放量。
- 交付前必须通过 `audit_vqa.py` 且 exit 0。
