# Training Fit(前置裁决 gate)

数据构建的第一道 gate:在写 profile、建 pipeline 之前,先回答
**"该不该微调?全量还是 LoRA?硬件撑得住吗?数据为哪个方案而建?"**。
本文件只做决策支持,不含训练命令与超参(训练 → `train_kit/`)。
裁决结果落盘为 profile 的可选 `training_fit:` 段(快照,非蓝图)。

交互方式:2–4 个选项的小问题,允许"未知"——未知项按保守默认走 pilot,不阻塞。

## 决策树 A:需不需要微调(最容易救钱的一步)

先分诊症状,再考虑训不训:

```
症状 = "检索不准 / 找不到"
  → 先跑数据侧诊断(假负率、轴配比、污染)→ 数据问题:不训,回 audit 修复动作
  → embedding 质量尚可但 top-k 混入近似错:不训模型,加 reranker(VQA 模式可低成本伪装)

症状 = "答案不对 / 不会答"
  → 基座 zero-shot / few-shot 先测(不训练,只写 prompt)
      达标   → 不训;若要提效,数据只做评测集
      差口气 → 先试 RAG(检索 + 上下文注入,复用 embedding 线)
      确实缺领域能力 → 进决策树 B
```

**进入树 B 的合法理由**(满足其一):
- 专业术语/私有标识导致基座系统性答错(zero-shot 明显低于任务要求)
- 输出格式/风格有强约束,上下文工程无法稳定达成
- 有持续供给的高质量领域数据,且零样本评测确认有差距

**非法理由**(记录并劝返):"别人都在微调"、"数据已经有了不想浪费"、
"没试过 zero-shot 但感觉不行"——最后这条是高频误诊,必须先补基线。

## 决策树 B:全量 vs LoRA vs QLoRA(硬件驱动)

先取硬件事实(GPU 型号 × 显存 × 卡数;云上则取预算与机型档位):

| 硬件条件 | 训练方案 | 数据含义 |
|---|---|---|
| 单卡 < 24GB(如 4090 以下 / 3060 12G) | **QLoRA** | 1–2k 高质量精标起步;宁缺毋滥最严格执行 |
| 单卡 24–48GB(4090/A6000) | LoRA(全模组) | 2k–50k;假负例红线不变 |
| 单卡 80GB(A100/H100) | LoRA 或部分层全参 | 5k–100k |
| 多卡 ≥ 4×A100/H100 且样本 ≥ 10万 | 全量微调可议 | 可容忍略脏、靠规模摊薄噪声 |
| 云上无固定卡 | 按预算档位映射到上表 | pilot 阶段一律按 QLoRA 红线准备数据 |

**分线差异:**
- **Embedding 线**:通常只训投影层/浅层,LoRA 即足够,硬件门槛远低于 VQA 线;
  16GB 级单卡即可起步。
- **VQA 线**:受视觉 token 影响,显存开销显著高于同参数纯文本模型;
  上表按 7B–8B 级基座估算,更大基座整体上移一档。

**数据量联动规则(gate 结论直接影响 build_data 行为):**

| 方案 | 目标样本量(pilot) | 质量红线收紧点 |
|---|---|---|
| QLoRA | 1k–2k | 假负率阈值降半;[manual] 项全查 |
| LoRA | 2k–10k | 标准 profile 规则 |
| 全量 | 10万+ | audit 照常,另需分桶抽检防长尾 |

## 决策树 C:平台/交付路由

```
Embedding 线
  → ms-swift(唯一原生支持对比学习)→ schemas/ms_swift/embedding_infonce.md

VQA 线
  ├─ 自持 GPU:
  │    基座是 Qwen-VL / InternVL / ModelScope 系 → ms-swift
  │    其他基座或团队熟悉 LF                    → LLaMA-Factory
  │                                              → schemas/llama_factory/vqa_sft.md
  ├─ 无 GPU / 想用闭源模型
  │    → 不构建训练集;数据只做评测集(few-shot + 上下文工程路线)
  └─ 云托管(PAI/百炼等)
       → 按平台模板交付,在 schemas/ 补对应平台文件
```

## 裁决输出(training_fit 快照)

```yaml
training_fit:
  decision: finetune | no_finetune_prompt | no_finetune_data_fix | rag_first | eval_only
  reason: 一句话判据(症状 + 基线证据)
  method: qlora | lora | partial_ft | full_ft      # decision=finetune 时填
  hardware: "2×4090 24G"                            # 用户口述原样记录
  scale_target: 2000                                # 联动规则得出的 pilot 样本量
  platform: ms_swift | llama_factory | ...
  schema: schemas/ms_swift/vqa_sft.md               # 交付通道
```

**判定纪律:**
- `decision != finetune` 时,skill 仅产出评测集或不产出,Workflow 后续步骤跳过;
- method 与硬件矛盾(如 QLoRA 却报 4×H100 数据量)→ 回树 B 重算 scale_target;
- "未知"项不做猜测,在快照中保留 null 并按保守默认推进。
