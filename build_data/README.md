# build-data · 数据飞轮构建 Skill

构建 VLM 训练数据的技能：以**数据飞轮**为核心循环，把"造数据"从一次性工程变成可治理、
可防倒转、可沉淀教训的闭环。产出 VQA（SFT）与 VL-Embedding（对比学习）两条产线的训练样本，
覆盖 ms-swift / LLaMA-Factory 等训练平台。

---

## 一、数据飞轮：循环构建逻辑

### 1.1 为什么需要飞轮

数据集的**真实质量不可直接观测**（POMDP 模型中的 S）。你能看到的只是三层越来越贵、
越来越真的观测（O）：

| 观测层 | 成本 / 节奏 | 工具 | 能回答什么 |
|---|---|---|---|
| **proxy** | 免费 · 秒级 · 每批 | `audit_*.py` + `audit_diversity.py` 静态检查 | 这批样本格式/假负/泄漏/污染合规吗；风格/长度/轴源配额达标吗 |
| **judge** | 元级 · 分钟 · 每批次 | `judge_sample.py` VLM 抽检 | proxy 判定和事实偏离了吗（drift 率） |
| **true_r** | 千元 · 天级 · 每回合 | 训练后下游 eval → `convert_eval` / `parse_eval` | 模型真实表现：**唯一真值 R** |

飞轮的本质：**用失败 case 驱动定向补数据**，每转一圈就把教训写回配置，让下一圈不重蹈覆辙。

### 1.2 一圈的循环（turn）

```
                     ┌──────────────────────────────────────┐
                     │                                      │
                     ▼                                      │
   触发(坏例>阈值/里程碑)                                    │
        │                                                   │
        ▼                                                   │
  ┌─ 构建批次(builder.pipeline: 渲染→caption/query→组装)     │
  │        │                                                │
  │        ▼                                                │
  │  audit_*.py --report   ←── proxy 层:秒级拦截不合格样本    │
  │        │  (exit 1 则返工修复,不放行)                      │
  │        ▼                                                │
  │  judge_sample.py       ←── judge 层:抽检 proxy 漂移率    │
  │        │                                                │
  │        ▼                                                │
  │  交付训练 → 平台 eval → convert_eval.py(归一)            │
  │        │                                                │
  │        ▼                                                │
  │  parse_eval.py         ←── true_r 层:坏例按杠杆分排序,   │
  │        │                    路由到四种动作之一             │
  │        ▼                                                │
  │  ┌── 选择动作(按杠杆分从高到低)──────────────────┐        │
  │  │ 语料缺口 → downgrade_discard(降权弃轴)      │        │
  │  │ 新失败模式 → classify_register / register_axis │     │
  │  │ 表征缺口 → rebalance_resample(重平衡补样本)  │        │
  │  │ shortcut/幻觉 → rewrite_fix(回炉重写)        │        │
  │  └──────────────┬───────────────────────────┘        │
  │                 ▼                                     │
  │  record_turn.py(写入 turn_log: 指标+proxy+成本)        │
  │                 ▼                                     │
  │  diff_turn.py(回归守卫) ── exit 1/2 ──→ 回滚,不沉淀 ────┤
  │                 │ exit 0                              │
  │                 ▼                                     │
  │  register_axis.py --apply(产 patch,人审后合并 profile) │
  │                 │                                     │
  └─────────────────┴──── 下一圈带着新 profile 再转 ────────┘
```

### 1.3 让飞轮不倒转的三道守卫

1. **交付门**（proxy）：audit exit 1 → 批次不出门。
2. **考卷门**（eval 一致性）：`eval_set_sha` 变了 → diff_turn **exit 2 拒绝比对**——
   换了考卷的分数没有可比性。
3. **回归门**（指标级）：回合间 / 历史最佳的 recall、fn_ratio、**多样性指标**（distinct-n、短句占比等）劣化 → diff_turn **exit 1** → 该回合**不沉淀、动作回滚**。计数级信号（坏例次数上涨）只作 hint，
   **永远以指标级仲裁为准**。多样性守卫专拦"修坏例把风格修窄了"（总指标升但风格收窄，短句场景上线即崩）。

### 1.4 让飞轮会沉淀

每圈结束，人的确认把发现写回 profile（活层）：

- 新失败模式 → `similarity_axes` / `vqa_failure_modes` 注册
- 语料撑不起的轴 → `axis_corpus_gaps` 声明，此后自动路由"弃轴"而非空转补数据
- 历史最佳指标 → `regression_baselines` 回填（`diff_turn --update` 产 patch 供人审）

**沉淀 = 教训只发现一次**。下一圈 parse_eval 面对同样的坏例会直接说"known"，不再浪费注意力。

### 1.5 什么时候停

- **plateau**：连续 N 轮 true_r_delta ≤ 0（`flywheel.plateau_turns`，默认 2）→ 自动 `[STOP?]`
- **ROI 触底**：边际 true_r 增量 / 元 < `flywheel.roi_floor` → 停
- 飞轮停 ≠ 失败：说明当前语料/动作空间已榨干，收益在别处（换语料、换轴或收工）

---

## 二、安装

### 2.1 本体（必装）

把 skill 目录放进 pi 的技能目录即可：

```bash
# 单用户安装
git clone <本仓库> ~/.pi/agent/skills/build-data
# 或已有目录时软链
ln -s /path/to/build_data ~/.pi/agent/skills/build-data
```

依赖（首次使用前一次性装好）：

```bash
pip install pyyaml          # profile 解析(全部脚本唯一第三方依赖,标准库之外)
```

### 2.2 外部 builder repo（跑实际管线时需要）

skill 自身**不含**数据构建管线代码，`builder.pipeline` 在外部仓库：

```bash
# 默认位置 $HOME/auto-build-data;或用环境变量覆盖
export BUILD_DATA_REPO=/path/to/your/builder-repo
```

只做 audit / eval 分析（不跑构建管线）时**不需要**这个 repo。

### 2.3 VLM 端点（仅放量/judge 层需要）

OpenAI 兼容端点即可（如 DashScope qwen-vl-max）：

```bash
export VLM_API_KEY=sk-...
export VLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export VLM_MODEL=qwen-vl-max
```

### 2.4 验证安装

```bash
SKILL_DIR=~/.pi/agent/skills/build-data
python3 "$SKILL_DIR/scripts/audit_vqa.py" --help        # 应打印 usage
python3 "$SKILL_DIR/scripts/parse_eval.py" --help
python3 -c "import yaml; yaml.safe_load(open('$SKILL_DIR/profiles/tech_manual.yaml'))" && echo OK
```

---

## 三、使用

### 3.1 最短上手路径（新领域）

```bash
# 0. 前置裁决:先确认该不该微调(reading references/training_fit.md),别急着造数据
# 1. 复制一份领域 profile,改 R 总账 / capability_gates / 相似轴
cp "$SKILL_DIR/profiles/tech_manual.yaml" "$SKILL_DIR/profiles/my_domain.yaml"

# 2. Pilot:不开 VLM,零成本跑通两条线
python -m builder.pipeline --src-dir SRC --out-dir out_pilot --no-vlm --max-pages 20

# 3. 审计(proxy 层,交付门)
python3 "$SKILL_DIR/scripts/audit_embedding.py" out_pilot --report out/audit_emb.json
python3 "$SKILL_DIR/scripts/audit_vqa.py"       out_pilot --report out/audit_vqa.json
python3 "$SKILL_DIR/scripts/audit_diversity.py" out_pilot --report out/audit_div.json   # 多样性配额(长度/口语/distinct-n/每轴每源)
python3 "$SKILL_DIR/scripts/audit_mix.py"       out_pilot --report out/audit_mix.json   # 样本类型配额(拒答/对抗占比,防幻觉)

# 4. 修复 → 放量(--resume 复用缓存) → 再审计 → 交付训练
```

### 3.2 开启飞轮（可选层，默认关闭）

在 profile 里打开：

```yaml
eval:
  enabled: true          # 默认 false;false 时 skill 行为与纯 audit 层一致
```

每圈的标准动作序列：

```bash
# ① 平台 eval 输出 → canonical(两种常用格式已内置)
python3 "$SKILL_DIR/scripts/convert_eval.py" raw_swift_eval.jsonl \
  --format swift_ir --k 10 --axis-map meta.jsonl --out out/eval_result.jsonl

# ② 坏例排序与路由(杠杆分,四种动作)
python3 "$SKILL_DIR/scripts/parse_eval.py" out/eval_result.jsonl \
  --profile "$SKILL_DIR/profiles/my_domain.yaml"

# ③ 记录本回合(自动算 eval_set_sha / true_r_delta / 累计 cost)
python3 "$SKILL_DIR/scripts/record_turn.py" out/turn_log.jsonl \
  --eval-metrics out/metrics.json --audit-report out/audit_emb.json \
  --eval-result out/eval_result.jsonl --badcases out/badcases.jsonl --cost 2.44

# ④ 回归守卫(回合间 + 历史最佳 + proxy 三重;exit 1/2 = 拦截)
python3 "$SKILL_DIR/scripts/diff_turn.py" out/turn_log.jsonl \
  --profile "$SKILL_DIR/profiles/my_domain.yaml"

# ⑤ 沉淀建议(新轴/基线回填,产 patch 供人审,不自动改 profile)
python3 "$SKILL_DIR/scripts/register_axis.py" out/badcases.jsonl \
  --profile "$SKILL_DIR/profiles/my_domain.yaml" --apply patch_t3.yaml
```

### 3.3 生产 badcase 接入（可选）

线上坏例按 `references/badcase_intake.md` 的契约导出——关键是带 `channel:"online"`
字段，parse_eval 会自动降噪打折并和离线坏例走同一条选择管线。

### 3.4 换领域 / 换平台

- **换领域** = 写一个新的 profile YAML（相似轴、假负规则、污染词表、失败模式全在 profile）
- **换训练平台** = 在 `schemas/` 加一个 md（canonical → 平台映射契约）
- skill 正文与脚本**零领域词**——所有领域知识隔离在 profile 里

---

## 四、目录结构

```
build_data/
├── SKILL.md                     # 技能入口:模型/分层/Workflow/铁律
├── README.md                    # 本文件:飞轮逻辑 + 安装使用
├── profiles/
│   └── tech_manual.yaml         # 领域 profile(唯一可变的领域知识层)
├── references/                  # 方法论(7 篇)
│   ├── training_fit.md          #   前置裁决:该不该微调
│   ├── methodology.md           #   闭环方法论/POMDP/观测金字塔
│   ├── schema.md                #   canonical 中间表示
│   ├── eval_fit.md              #   [飞轮] eval 方案裁决
│   ├── badcase_selection.md     #   [飞轮] 杠杆评分与路由
│   ├── flywheel.md              #   [飞轮] 回合/守卫/沉淀/ROI
│   └── badcase_intake.md        #   [飞轮] 生产坏例接入契约
├── schemas/                     # 平台交付契约(train + eval)
│   ├── ms_swift/                #   embedding_infonce / vqa_sft / eval_*
│   └── llama_factory/           #   vqa_sft / eval_vqa
└── scripts/                     # [auto] 工具(8 个)
    ├── audit_embedding.py       #   proxy 层:embedding 样本审计
    ├── audit_vqa.py             #   proxy 层:VQA 样本审计
    ├── audit_diversity.py       #   proxy 层:多样性配额审计(长度/口语/distinct-n/每轴每源)
    ├── audit_mix.py             #   proxy 层:样本类型配额(拒答/对抗占比,防幻觉/保鲁棒)
    ├── judge_sample.py          #   judge 层:VLM 抽检 + proxy 漂移率
    ├── convert_eval.py          #   平台 eval 输出 → canonical
    ├── parse_eval.py            #   坏例杠杆排序 + 动作路由
    ├── record_turn.py           #   回合记录(turn_log 生成器)
    ├── diff_turn.py             #   回归守卫(考卷/回合间/历史最佳)
    └── register_axis.py         #   新轴/回归去重建议
```

---

## 五、三条铁律（读方法论文档前先记住）

1. **先裁决，再构建**——没有 zero-shot 基线证据的"要微调"是高频误诊；
   检索不准先修数据，答案不对先测基座，都不行才训。
2. **数据救不了语料不足**——包络 gate 没过的轴降权/弃用，不硬堆样本；
   飞轮在这类 case 上自动路由"弃轴"而非空转。
3. **Proxy ≠ 目标**——audit/judge 都是信念不是真值，唯一真值来自训练后 eval；
   所以飞轮的最后一环永远是 diff_turn 的指标级仲裁。
