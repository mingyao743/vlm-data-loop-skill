# Badcase 选择方法论（选择方法论·part 2）

把 [methodology.md](methodology.md) §2.5 的"失败 case 倒推，别先调超参"从原则变成
**可执行的选择器**：面对 eval / 生产回流的上百个 badcase，**先做哪个**。本文件给一个
域无关的杠杆评分 + 路由框架；具体阈值与权重写进 profile 的 `selection:` 段，正文不含域词。

## 0. 与 §2.5 的关系

§2.5 列了三个失败来源（失败 case 倒推 > audit/judge 命中 > true_r 新模式）并定优先级，
但没回答"失败 case 有 300 个，先做哪 5 个"。本文件补这一步：把每个 badcase 打分、聚类、
路由到动作 A。选择结果是**一张优先队列**，飞轮每圈只动队列头部——杠杆最高、最能证伪某条
R 指标的 case。

执行者是 [`scripts/parse_eval.py`](../scripts/parse_eval.py)（`[auto]` 评分器）；**决定是否
真的动手是 `[manual]` / alive**——parse_eval 只评分与路由建议，不执行动作（活/冻结）。

## 1. 杠杆分（四因子）

| 因子 | 含义 | 取值 | 数据来源 |
|---|---|---|---|
| `freq` | 该失败模式在 eval 里的出现次数 | 计数 | parse_eval 聚合 |
| `severity` | 多严重：完全 miss / top-k 近 miss（VQA：答错/答不全） | {hard, soft} | parse_eval 打分 |
| `fixability` | **能不能用数据动作解决**：语料缺口=0，表征/shortcut/幻觉=1 | {0,1} | 交叉 profile `axis_corpus_gaps` / `capability_gates` |
| `novelty` | 新轴 / 已知 | {new, known} | 交叉 `similarity_axes`（次数上升另报 `count_regression_hint`，不作倍率） |

```
leverage = (w_freq·freq_norm + w_sev·severity + w_fix·fixability + w_novel·novelty_raw) · novelty_mult
```

`novelty` 取值 {new, known}（P0-3：计数层面的"回归"不再是倍率，见下）。权重与倍数均可由
profile `selection:` 覆盖。

**回归的裁决权（P0-3）**：失败**次数**上升只是 `count_regression_hint`（计数值噪声大、且随
eval 集大小变化），**永远不直接改 leverage、不阻断沉淀**。真正的回归判定只看**指标级**
守卫（[`scripts/diff_turn.py`](../scripts/diff_turn.py)，turn-over-turn + 历史最佳）——
飞轮倒转是最高优先级，但它由指标说话，不由计数猜测说话。双信号冲突时：**指标级为准，
计数级只作线索**。

## 2. 路由（诊断 → 动作 A）

| 诊断 | fixability | 路由动作 | 落地到 |
|---|---|---|---|
| 语料缺口（轴无承载结构） | 0 | **不补数据**，降权/弃轴，推理期 metadata filter 补偿 | profile `capability_gates` + `metrics` 权重 |
| 表征缺口（轴存在但欠采样） | 1 | 重平衡 / 补样本 | 回 Workflow step 2-5 |
| shortcut / 污染 / 泄漏 | 1 | 重写、补定向难负、修污染 | 回 audit→修复 |
| 幻觉（答案声称图内不可见） | 1 | 重写答案 / 弃样本 | VQA 修复 |
| 不匹配任何已知轴 | — | **注册新轴**（register_axis 建议，人确认）→ re-pilot 该轴 | profile `similarity_axes` + 新 pilot |

语料缺口（fixability=0）是"数据救不了语料不足"在飞轮里的落地：这类 case **不进数据动作队列**，
直接进 profile 降权通道，避免飞轮空转在数据修不动的地方。

## 3. 去重（新轴 vs 已知回归）

同一失败模式会被反复报。路由前必须去重（由 [`scripts/register_axis.py`](../scripts/register_axis.py)
做 `[auto]` 建议，人确认）：

- 命中已知轴 + 本回合指标比上次差（diff_turn 指标级判定）→ **真回归**：diff_turn exit 1
  阻断沉淀、动作回滚；badcase 层面仅标 `count_regression_hint` 供人核查
- 命中已知轴 + 指标持平 → `known`（不进队列，只更新 freq 基线）
- 不命中任何已知轴 → 候选 `new`（人确认后注册，呼应 §2.5 开放轴注册表）

## 4. 输出

`parse_eval.py` 产 `badcases.jsonl`（按 leverage 降序，每行带 axis/source/诊断/建议动作），
飞轮每圈只取头部 N 条进 Workflow step 4（修复）。典型用法：

```bash
python3 "$SKILL_DIR/scripts/parse_eval.py" out/eval_result.jsonl --top 10
# 产 out/badcases.jsonl + 打印 top10 优先队列
```
