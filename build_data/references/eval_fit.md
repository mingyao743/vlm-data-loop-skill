# Eval Fit（可选 eval 裁决 + 选择方法论·part 1）

build_data 的可选顶层观测：把 true_r（训练后下游 eval）从"里程碑手到"变成"可选项，
按需开启"。本文件镜像 [training_fit.md](training_fit.md) 的决策树风格，只做裁决支持，
不含训练/eval 的 **run**（run 归 train_kit 或外部平台；build_data 只拥有 **eval 集构建 +
eval 结果解释**，见 §0）。

启用开关：profile 的 `eval.enabled`。`false` 时 skill 行为与无 eval 时完全一致（只到 audit 层）。
有训练产物 / 基座可调 / 想让飞轮真正转起来的用户才打开。

## 0. 边界（与 training_fit 的接缝）

training_fit 决策树 C 已声明：`decision != finetune` 时 skill **仅产出评测集**。
也就是说 eval 集构建本来就在 build_data 范围内，只是此前没给格式契约（见
[schemas/](../schemas/README.md) 的 `eval_*.md`）。本 gate 把"产出评测集"和"解释 eval 结果"
两件事都工具化，run 仍归外部：

| 归属 | 内容 |
|---|---|
| build_data（本 skill） | eval 集构建（canonical + 平台契约）、eval 结果 → 结构化 badcase、选择与路由 |
| 外部 / train_kit | 训练 run、eval run、平台 eval 命令本身 |

## 决策树 E1：该不该 eval

```
没有任何模型可调（既无训练产物也无基座 API）
  → 不 eval；数据只做评测集待用（training_fit C 已路由），Workflow 到 audit 止
有基座 / 训练产物
  → 进 E2
```

## 决策树 E2：离线 vs 在线

| 条件 | 方案 | 数据含义 |
|---|---|---|
| 有标注 / 可构造 ground truth | **离线 eval 集**（可控、可回归、可按轴 breakdown） | 按 `schemas/<platform>/eval_*.md` 造 eval 集 |
| 有线上埋点 + 用户反馈通道 | **在线 eval**（真实 query 流量） | 需 [badcase_intake.md](badcase_intake.md) 把 log 归一成 badcase |
| 两者皆可 | 离线做主、在线做校验 | 离线为主战场；在线只校准"代理 R 与真实 R 偏差" |

在线 eval 的失败回流走 [badcase_intake.md](badcase_intake.md)，与离线 eval 走**同一条**选择管线。

## 决策树 E3：指标选择

- **Embedding 线**：Recall@k（k=1,5,10）+ 假阳分析（top-k 里不该命中的命中了）。
  逐轴 breakdown（哪个轴的 Recall 掉了 = 该轴表征或难负有问题）。
- **VQA 线**：答案准确率（精确匹配 + LLM-judge 兜底语义等价）+ 幻觉率（答案声称图内内容但不可见）。
  逐来源 breakdown（vqa_sources 哪类问答掉链子）。
- **两线共同**：看轴级 / 来源级 breakdown，**不看单一总分**——总分平滑会掩盖某条轴的退化。

## 决策树 E4：eval 集规模与切分

- eval 集 ≥ 2k；按**来源单元**切分（按文档 / 按场景，复用 [methodology.md](methodology.md) §2 原则 7），
  禁随机切样本（泄漏）。
- 每条相似轴 / 每个 vqa_source 至少 50 条，否则该轴 eval 不可单独信。
- eval 集与 train 集的来源单元必须不重叠（同文档的页不能一半 train 一半 eval）。

## 决策树 E5：节奏

| 层 | 节奏 | 触发 |
|---|---|---|
| proxy（audit） | 每批 | 自动 |
| true_r（eval） | 每回合（飞轮转一圈） | 见 [flywheel.md](flywheel.md)：坏例达阈值 / true_r 停滞 N 轮 / 里程碑 |

## 裁决输出（eval 快照，写入 profile）

```yaml
eval:
  enabled: true
  mode: offline              # offline | online | both
  metrics: [recall@10, recall@1]   # 或 [answer_acc, halluc_rate]
  eval_set: out/eval         # canonical eval 集路径
  cadence: per_turn
  sample_floor: 2000
  per_axis_floor: 50
```

**判定纪律：**
- `enabled: false` 时，Workflow 步骤 6（eval）跳过，飞轮治理（[flywheel.md](flywheel.md)）不启用；
- eval 集必须先过对应 `audit_*.py`（eval 集也是一种样本集，格式契约同 train 集 + `split:eval`）；
- "未知"项不做猜测，保留 null 并按保守默认（不 eval）推进。
