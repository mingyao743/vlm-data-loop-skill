---
name: build-data
description: >
  Build VLM training datasets in two lines — VQA (SFT) and VL-Embedding
  (contrastive) — from a shared asset/caption pipeline. Use when constructing
  multimodal SFT samples or InfoNCE text↔image samples from an image/document
  corpus, adapting the pipeline to a new domain, or auditing sample quality
  before delivery to training platforms such as ms-swift.
---

# Build VLM Training Data

数据构建 = 一个运行在**代理观测**上的 POMDP 闭环:数据集的真实质量(S)不可直接观测,
audit/judge 是有噪声的观测(O),数据修复操作是动作(A),唯一真实 R 是训练后的下游指标。

```
             ┌──────────── 三级观测(每圈都是 O,不是 S)────────────┐
             │  proxy:audit 脚本(免费·秒级,每批)                  │
             │  judge:VLM-as-judge 抽检(元级·分钟,每批次)         │
             │  true_r:训练后下游 eval(千元·天级,仅里程碑)        │
             └───────────────────────┬────────────────────────────┘
                                     ▼
  A(数据修复):重写 / 补难负 / 重平衡 / 改 profile 规则 / 弃用弱轴
                                     │
                                     ▼
  语料 → 图像资产化 → caption/query 生成 ─┬→ VQA 组装       → audit_vqa
                                          └→ Embedding 组装 → audit_embedding
```

- **VQA 线**:question + answer(+image)→ SFT 样本
- **Embedding 线**:query + 正例 + 分层难负 → `samples_infonce.jsonl`,
  可直接 `swift sft --task_type embedding --loss_type infonce`

范围:只做样本构建。训练相关 → `train_kit/`。

## 关键分层(改任何东西前先看)

| 层 | 位置 | 内容 |
|---|---|---|
| 前置裁决 | [references/training_fit.md](references/training_fit.md) | 该不该微调 / 全量 vs LoRA / 硬件包络 / 数据量联动 → 决定后续一切 |
| 闭环方法论 | [references/methodology.md](references/methodology.md) | R 总账 / 观测金字塔 / 包络裁决 / 假负例与泄漏判据 / 检查清单 |
| 内部规范格式 | [references/schema.md](references/schema.md) | 两条线的 canonical 中间表示与约束 |
| 平台格式契约 | [schemas/](schemas/README.md) | canonical → 各训练平台映射、对齐校验、已知坑 |
| 领域 profile | [profiles/](.) | **R 指标、观测节奏、包络 gate、活/冻结标注、相似轴注册表** |
| 可选 eval / 飞轮 | [eval_fit.md](references/eval_fit.md) · [flywheel.md](references/flywheel.md) · [badcase_selection.md](references/badcase_selection.md) · [badcase_intake.md](references/badcase_intake.md) | 该不该 eval / 离线 vs 在线 / 选择方法论 / 回合治理 / 回归守卫;profile `eval.enabled` 开关 |

**换领域 = 写一个新的 profile YAML;换训练平台 = 在 `schemas/` 加一个 md。**
skill 正文与脚本不含任何领域词。

## Workflow(每圈一轮闭环)

0. **前置裁决(training_fit gate)**:需求不是“要数据”而是“要效果”。按 training_fit.md 三棵树分诊:
   先问任务形态/基线尝试/硬件(允许“未知”);判定**不微调**时(数据问题/reranker 可解/
   zero-shot 达标),skill 只产出评测集或不产出,后续步骤跳过。
   判定微调时,硬件决定 method(QLoRA/LoRA/全量),进而联动 pilot 样本量与质量红线,
   并锁定平台/schema 交付通道;结果写入 profile 的 `training_fit:` 快照。
1. **新领域**:复制 [profiles/tech_manual.yaml](profiles/tech_manual.yaml) 为起点。先定 R 总账(权重和=1),再过 capability_gates(语料能力包络裁决,meta-R gate)。
2. **Pilot**:不开 VLM、限页数跑通端到端,两条线各出一小批样本。
3. **审计(proxy O)**:运行两个 audit 脚本,对照 methodology.md §3 复核 `[manual]` 项;judge 层抽检一批。
4. **修复(A)**:按 audit 结果决定动作——重写/补难负/重平衡/**降权或弃用包络 gate 未过的轴**(数据救不了语料不足)。
5. **放量**:VLM + `--resume`,盯 `cost_log.txt`;放量后重跑 audit + judge。
6. **验收(true_r,里程碑)**:训练评测。暴露的**新失败模式 → 注册为新相似轴或新质量维度**(开放式注册表,呼应双螺旋),回到第 3 步;R→D 反馈只证伪指标和权重,不改 profile 规则之外的东西。

> **可选:eval 与数据飞轮**。step 6 的 true_r 若要工具化(而非手到里程碑),开 profile
> `eval.enabled: true`,走 [eval_fit.md](references/eval_fit.md) 裁决 eval 方案、
> [badcase_selection.md](references/badcase_selection.md) 选择杠杆最高的失败 case、
> [flywheel.md](references/flywheel.md) 治理回合 / 回归 / 沉淀。平台 eval 输出先用
> `convert_eval.py` 转 canonical,eval 集格式见 `schemas/<platform>/eval_*.md`,
> 回合记录用 `record_turn.py`,结果解析用 `parse_eval.py`。未开启时 skill 行为不变(只到 audit 层)。

## Commands

builder repo 默认在 `$HOME/auto-build-data`,可用环境变量 `BUILD_DATA_REPO` 覆盖
(或对其 `pip install -e` 后无需 cd);`SKILL_DIR` = 本 skill 所在目录。

```bash
export BD_REPO="${BUILD_DATA_REPO:-$HOME/auto-build-data}"
cd "$BD_REPO"

# pilot:文本兜底,每文档限 20 页(两条线的中间产物一次生成)
# 管线结构改动后,先用 --no-vlm 零成本 dry-run 验证 sample 量/质量提升,再烧 VLM
python -m builder.pipeline --src-dir SRC --out-dir out_pilot --no-vlm --max-pages 20

# 放量(OpenAI 兼容 VLM 端点)
VLM_API_KEY=sk-... python -m builder.pipeline --src-dir SRC --out-dir out \
  --vlm-model qwen-vl-max --vlm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --k-neg 5 --queries-per-page 2 --resume

# 审计产出(SKILL_DIR = 本 skill 所在目录);--report 产机器可读摘要(供 record_turn 收进 turn_log)
python3 "$SKILL_DIR/scripts/audit_embedding.py" out_pilot --report out/audit_emb.json
python3 "$SKILL_DIR/scripts/audit_vqa.py" out_pilot --report out/audit_vqa.json
# 多样性审计(零成本,配额在 profile diversity:;风格收窄/句式指纹/长度三档)
python3 "$SKILL_DIR/scripts/audit_diversity.py" out_pilot --report out/audit_div.json
# 换领域:--profile "$SKILL_DIR/profiles/<domain>.yaml"
# 换平台格式:按 schemas/<platform>/*.md 的契约交付,audit 校验该契约
# judge 层抽检(可选;对比 audit 判定,输出 proxy 漂移率;--no-vlm 仅出 proxy 分布)
python3 "$SKILL_DIR/scripts/judge_sample.py" out_pilot --n 30 \
  --endpoint https://... --api-key sk-... --model qwen-vl-max
# 可选:eval 与飞轮(profile eval.enabled=true 时)
python3 "$SKILL_DIR/scripts/convert_eval.py" out/raw_platform_eval.jsonl --format swift_ir --out out/eval_result.jsonl
python3 "$SKILL_DIR/scripts/parse_eval.py" out/eval_result.jsonl --profile "$SKILL_DIR/profiles/<domain>.yaml"
python3 "$SKILL_DIR/scripts/record_turn.py" out/turn_log.jsonl --eval-metrics out/metrics.json \
  --audit-report out/audit_emb.json --eval-result out/eval_result.jsonl --badcases out/badcases.jsonl --cost 2.44
python3 "$SKILL_DIR/scripts/diff_turn.py" out/turn_log.jsonl --profile "$SKILL_DIR/profiles/<domain>.yaml"
python3 "$SKILL_DIR/scripts/register_axis.py" out/badcases.jsonl --profile "$SKILL_DIR/profiles/<domain>.yaml"
```

关键参数:

| 参数 | 说明 |
|---|---|
| `--max-pages` | 每文档页数上限,pilot 用 |
| `--resume` | 复用已渲染页图与 caption 缓存,断点续跑 |
| `--queries-per-page` | 每页 query 数(放大样本量) |
| `--k-neg` | 每正例难负数(全数据集必须统一,见平台契约) |
| `--price-in/out` | 元/百万 token,驱动成本日志 |

audit 参数:`--profile`(领域规则)、`--fn-threshold`(默认 5%)、`--max-text-len`(默认 800)、
VQA 线另有 `--max-answer-len` / `--no-leak-check`。

产物:`out/samples_*.jsonl` + `.meta.jsonl`(逐样本溯源)+ `cost_log.txt`。

## 铁律(详见 methodology.md / training_fit.md)

- **先裁决,再构建**:没有 zero-shot 基线证据的“要微调”是高频误诊——检索不准先修数据,答案不对先测基座,
  都不行才训(training_fit 决策树 A)。
- **硬件是能力包络的一部分**:method 与硬件矛盾时重算 scale_target,而不是硬调数据;
  数据救不了硬件跑不动(与“数据救不了语料不足”同构的 meta-R)。
- **Proxy ≠ 目标**:闭环运行在代理观测上;audit 输出是信念(O)不是真值(S),唯一的真值来自训练后 eval。
- **数据救不了语料不足**(meta-R):相似轴在没有承载结构的语料上必然退化,包络 gate 未过的轴降权或弃用,不硬堆样本。
- **Embedding**:凡业务上实质等价的样本对(profile `fake_negative_rules`)**永不互为难负**——一条假负例污染整个 batch(audit 自动检测)。
- **VQA**:答案必须可验证、不泄漏进问题;声称来自图内的内容必须真实可见(audit 检测子串泄漏)。
- **VQA 拒答(不可答问题的正确行为是拒答)**:图中不可见/无法判断的问题,答案应为拒答风格或空
  (meta `sample_type: refusal/adversarial`),强行作答 = 教模型幻觉(audit_vqa 硬失败);
  拒答/对抗样本按 profile `mix:` 配额混入 —— 零拒答样本 → 模型必然学会"图里没有也要编"。
- **Shortcut 是默认行为**:与语义无关却一致出现的特征(profile `pollution_patterns`)禁止进入正例/问题文本(audit 自动检测)。
- **活/冻结边界**:确定性阶段(渲染/去重/组装/静态检查)用脚本冻结,智能阶段(caption/query/语义审计)才用 LLM;不要在冻结层引入智能,也不要在活层硬写规则。
- Query/question 必须覆盖真实用户问法;固定模板只作种子,多样性靠改写;
  风格分布(长短/口语/术语)必须覆盖真实输入,全长句训练会让短 query 成为 OOD。
- 失败 case 是罗盘:数据优化主线是"用失败 case 驱动定向补数据",别先调超参;
  低信息页(封面/空白/目录/章节首页)必须显式作负例,不能"留空"。
- 结构改动先 dry-run 再烧钱:`--no-vlm` 零成本验证增益后,再批量调用 VLM;
  上游文件名是隐式 schema(解析依赖命名约定),重构时不得随意重命名。
