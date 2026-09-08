# Skills

pi-agent 技能(skill)集合。每个子目录是一个独立 skill,由 `SKILL.md`(带 frontmatter)作为入口,
按需附带 references / schemas / profiles / scripts 等资产。技能可被 pi-agent 按描述加载。

## 约定

- **一个 skill = 一个子目录**,入口为该目录下的 `SKILL.md`(YAML frontmatter 含 `name` / `description`)。
- skill 正文与脚本**不含领域词**;领域特有内容下沉到 `profiles/<domain>.yaml`,
  训练平台格式契约放在 `schemas/<platform>/`。换领域 = 写 profile,换平台 = 加 schema。
- 脚本尽量保持 stdlib-only;确需依赖时在脚本顶部声明(如 `pyyaml`)。

## 技能索引

| Skill | 用途 | 入口 |
|---|---|---|
| `build_data` | 构建 VLM 训练数据集(VQA SFT + VL-Embedding/对比)的闭环流水线:前置裁决 → R 总账/观测金字塔/包络 gate → canonical schema → 平台契约 → 自动审计 | [build_data/SKILL.md](build_data/SKILL.md) |

新增技能 = 新增子目录 + 在本表登记一行。

## build_data 的分层结构(参考模板)

```
build_data/
├── SKILL.md                  入口:闭环示意 / Workflow / Commands / 铁律
├── references/               通用方法论与契约(去领域化)
│   ├── methodology.md          闭环方法论:R 总账、观测金字塔、包络裁决、负例经济学、检查清单
│   ├── schema.md               canonical 内部规范格式
│   └── training_fit.md         前置裁决 gate(该不该微调 / 全量 vs LoRA / 硬件包络)
├── schemas/                  平台格式契约(canonical → 平台映射)
│   ├── README.md               平台 × 任务路由表
│   ├── ms_swift/               embedding_infonce.md / vqa_sft.md
│   └── llama_factory/          vqa_sft.md
├── profiles/                 领域配置(领域词只在此)
│   └── tech_manual.yaml        R 指标 / 观测节奏 / 包络 gate / 活冻结 / 相似轴 / 实战教训
└── scripts/                  自动审计脚本
    ├── audit_embedding.py      profile 驱动(schema / 假负 / 污染 / 配比 / 资产路径)
    └── audit_vqa.py            schema / 答案泄漏 / 空答超长 / 资产路径(兼容 messages 与 sharegpt)
```

关键设计:**数据构建 = 运行在代理观测上的 POMDP 闭环**——真实质量是不可直接观测的 S,
audit/judge 是有噪声的 O,数据修复是 A,唯一真实 R 是训练后下游指标。详见
[build_data/references/methodology.md](build_data/references/methodology.md)。
