# 数据飞轮治理（回合制 / 回归守卫 / 沉淀）

[methodology.md](methodology.md) 把数据构建定义成 POMDP 闭环，本文件定义**闭环怎么一圈圈
转、怎么不倒转、怎么沉淀**。前提：eval 已开启（profile `eval.enabled: true`，见
[eval_fit.md](eval_fit.md)）。未开启时本文件不生效。

## 1. 回合（turn）

一个回合 = 一次完整的 build → eval → select → repair → re-eval。每回合用
[`scripts/record_turn.py`](../scripts/record_turn.py) 产 `turn_log.jsonl` 一行（**append-only**，
勿手拼 JSON——schema 漂移是真实事故源）：

```json
{"turn":3,"ts":"2026-09-11T10:00:00","data_version":"sha...","profile_sha":"...",
 "eval_set_sha":"ab12cd34ef56a7b8","eval":{"recall@10":0.71,"recall@1":0.52},
 "proxy":{"fn_ratio":0.03,"pollution_ratio":0.11},
 "badcases_ref":"out/badcases.jsonl","eval_result_ref":"out/eval_result.jsonl",
 "cost":2.44,"true_r_delta":+0.04}
```

关键字段：
- **`eval_set_sha`**：eval 集内容 hash。**两回合考卷不同则指标不可比** —— [`diff_turn.py`](../scripts/diff_turn.py)
  检测到不一致会拒绝比对（exit 2），先解决考卷再谈回归。
- **`proxy`**：audit `--report` 产出的机器可读比率（fn_ratio / pollution_ratio…）。
  回归拦截因此从天级 eval **提前到秒级 audit**（§3）。
- **`badcases_ref` / `eval_result_ref`**：本回合处理了哪个 badcases 文件 —— 事后可答
  "轴 X 是哪回合修的"（badcases 建议按回合命名，如 `badcases_t3.jsonl`）。
- **`true_r_delta`**：record_turn 自动按 eval 指标均值相对上一回合计算，无需手填。

## 2. 触发（什么时候转下一圈）

| 触发条件 | 含义 |
|---|---|
| 坏例数达阈值 | eval 失败数 > profile `selection.badcase_threshold`（默认 50） |
| true_r 停滞 | 连续 N 轮 `true_r_delta ≤ 0`（N 默认 2）→ **自动停**（methodology §2 原则 7 plateau） |
| ROI 触底 | 边际 true_r 增量 / 元 低于 `selection.roi_floor` → 停（§5） |
| 里程碑 | 人工触发（发版前、新机型接入） |

## 3. 回归守卫（防倒转）

每回合 re-eval 后，对 profile `regression_baselines:` 逐指标比对：新值不得劣化历史最佳
超过容差（`tolerance`，默认 0）。劣化 → [`scripts/diff_turn.py`](../scripts/diff_turn.py) 标红，
**该回合不沉淀、动作回滚**——飞轮倒转是最高优先级。

**双信号仲裁**：回归有两套可能信号——**指标级**（diff_turn，recall/acc/fn_ratio）与
**计数级**（`badcase_registry.last_fail_count` 上升）。冲突时**指标级为准，计数级只作线索**
（parse_eval 输出 `count_regression_hint` 供人核查，不阻断沉淀、不加杠杆）——计数随
eval 集大小漂移，指标才是稳定观测。

**proxy 也受守卫**：turn_log 带 `proxy` 段后，fn_ratio / pollution_ratio 等审计比率同样进
regression_baselines（`key: proxy.fn_ratio`）——许多倒转在 audit 秒级就能拦住，不必等训练。

**多样性也受守卫**：[`audit_diversity.py`](../scripts/audit_diversity.py) 的指标
（`len_short_ratio` / `distinct_2` / `colloquial_ratio`…）可进基线（`key: diversity.distinct_2`）。
典型隐性倒转：为修某轴难负批量生成同模板 query → 总指标升但**风格收窄**，短句场景上线即崩
（methodology 检查清单的"全长句 OOD"教训）。多样性基线专门拦这种"修息为宝"。

典型倒转：补了 A 轴的难负把 B 轴的表征压坏；重平衡后总 recall 升但某轴 recall 掉。
回归守卫就是让这种隐性倒转可被机器检出，不必等下一轮训练才发现。

## 4. 沉淀（让教训不重发现）

每回合的发现必须落进 profile，否则下一回合会"重新发现"同一个问题：
- 新轴 → `similarity_axes` 注册 + `badcase_registry` 加条目（status / last_fail_count）
- 语料缺口 → `axis_corpus_gaps` 声明 + `capability_gates` 复核
- 失败 case → 升格为 profile 规则后，从"失败 case 倒推"（§2.5 source#1）降级为
  "audit/judge 命中"（source#2）

沉淀 = profile 钉版本（data_version ↔ profile_sha），回滚能回到任意回合。注册新轴走
[`scripts/register_axis.py`](../scripts/register_axis.py)（`[auto]` 建议 → 人确认 → 写回 profile）。

## 5. ROI（转得值不值）

`turn_log.jsonl` 累计 cost 与 true_r_delta。飞轮停的另一个判据：**边际 true_r 增量 / 元**
低于 profile `flywheel.roi_floor`（回退 `selection.roi_floor`）时停——不是"没坏例"才停，
是"再花一块钱买不到足够 true_r"也停。与 §2 的 plateau 停止互补：plateau 管"还在涨但
涨不动"，ROI 管"还在涨但不值"。两个判据均由 `record_turn.py`（写入时）与 `diff_turn.py`
（守卫时）自动检查并打印 `[STOP?]`，不再依赖人肉翻 log。

## 6. 选择器的自校准（meta-loop）

杠杆权重（`w_freq/w_sev/w_fix/w_novel`、`new_bonus`）初始是拍的默认值。每隔 N 回合（建议
5）做一次校准：从 turn_log 取回各回合 `badcases_ref`，核对当时 top 选择的 cluster
"投入的动作 vs 本回合指标增量"——若相关性 ≈ 0，说明权重在猜：按贡献重排，或退回人工
排优先级。这是对**选择器本身**的 eval，防止飞轮高效地修不重要的事。
