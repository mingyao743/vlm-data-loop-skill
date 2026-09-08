# 平台 Schema 索引

内部规范格式(canonical format)见 [references/schema.md](../references/schema.md);
本目录存放 canonical → 各训练平台格式的**映射契约与已知坑**。每个文件标准骨架:
适用命令 / 样例行示例 / 映射规则 / 对齐校验 / 已知坑。

| 平台 | 任务 | 文件 | 备注 |
|---|---|---|---|
| ms-swift | embedding / InfoNCE | [ms_swift/embedding_infonce.md](ms_swift/embedding_infonce.md) | 主要交付格式;唯一原生支持对比学习 |
| ms-swift | VQA SFT(多模态) | [ms_swift/vqa_sft.md](ms_swift/vqa_sft.md) | messages 格式(user/assistant) |
| LLaMA-Factory | VQA SFT(多模态) | [llama_factory/vqa_sft.md](llama_factory/vqa_sft.md) | sharegpt 格式(human/gpt);**不支持 embedding** |

新增平台 = 新增一个子目录 + md 文件(沿用上述骨架),并更新本表。
