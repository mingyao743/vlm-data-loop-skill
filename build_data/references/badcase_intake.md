# 生产 badcase intake 契约(可选)

飞轮的燃料不只能来自离线 eval,还能来自**真实用户流量**。本文件定义生产 query log /
在线 badcase 怎么归一成和 [`scripts/parse_eval.py`](../scripts/parse_eval.py) 同构的
canonical eval-result,从而走**同一条**选择管线([badcase_selection.md](badcase_selection.md))。

## 0. 边界

build_data 不拥有线上埋点 / 反馈系统。本文件只定**归一格式**:你的线上系统按此导出,
parse_eval 就能消费。线上系统的实现归外部。

## intake 行(canonical eval-result 的"在线"变体)

# channel 字段(P0-2)是唯一的离线/在线判定依据 —— 不靠 source 名(生产行常带真实 source 名):

```json
{"kind":"vqa","question":"<用户原话>","image":"<命中页>","source":"实拍照片","channel":"online",
 "expected_answer":null,"predicted_answer":"<模型/检索返回>","correct":false,
 "user_satisfied":false,"raw_query_id":"..."}
```

- `expected_answer` 生产侧通常没有 ground truth → 留 null;`correct` 由用户反馈
  (点踩 / 无转化)或人工抽判。
- embedding 线类比：
  `{"kind":"embedding","query":...,"axis":"unknown","channel":"online","hit_at_k":false,"predicted_topk":[...]}`
  ——`axis` 常为 unknown，parse_eval 会把它路由到 `register_axis`（新轴候选），由人确认归类。
- 离线与在线的失败行**结构一致**;唯一区别是 `channel:"online"` 字段 —— parse_eval
  仅对该字段的行在 `freq` 上打折（profile `selection.online_freq_discount`，默认 0.5），
  因为生产反馈稀疏且噪声大。**不要用 source 名推断 channel**（旧约定已废弃）。

## 已知坑

- 生产 query 有分布偏移(只有"没找到的"才被报),需配合离线 eval 防偏
  ([eval_fit.md](eval_fit.md) E2 两者皆可)。
- 用户反馈稀疏且噪声大:`user_satisfied` 缺失时按 `correct=false` 兜底,但 leverage 的
  `freq` 要打折(见上)。
- 隐私:query 原话导出前脱敏(机型序列号、个人信息)。
- 在线 badcase 的 `fixability` 判定与离线一致：仍交叉 `axis_corpus_gaps`——
  生产侧暴露的语料缺口同样"数据救不了"，直接进降权通道。
- VQA 生产侧 source 常不在 profile `vqa_sources` 里 → parse_eval 路由 `classify_register`
  （新失败模式候选），人确认后进 `vqa_failure_modes` 注册表（P2-3），而非笼统 rewrite_fix。
