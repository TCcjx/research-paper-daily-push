# 已复核论文数据

prepare 生成的是待填写草稿，不能直接交付。默认 brief；完整摘要后标记 evidence_reviewed。验证器检查结构、一致性和必要证据记录，不能核实链接内容是否支持结论。

根对象：date（YYYY-MM-DD）、report_style（brief/detailed）、term_explanation_mode（off/dual）、metadata、articles（最多10篇且不超过 metadata.maximum）、research_ideas（0–3条）、radar 和 updates。使用 deliver --queue 可附加同次检索的真实元数据；缺失热点数据时报告明确说明，不能自行伪造。

每篇必填字段：

| 字段 | 约定 |
| --- | --- |
| title / chinese_title | 原题 / 忠实中文题名 |
| journal / publication_date / url / article_type | 场所、有效日期、http/https链接、类型 |
| doi / is_preprint / source_ids | DOI可空；保留来源身份和预印本状态以跟踪更新 |
| reading_lane | daily 或 supplementary，依据日期，不把旧论文当作今日新增 |
| recommendation_score | 70–100整数，与分项加权取整一致 |
| component_scores | relevance、novelty、quality、methodology、inspiration，各0–100整数 |
| score_status | 必须 evidence_reviewed |
| why_worth_reading | 100字符内，解释为什么值得研究人员阅读 |
| core_findings | 1–5条，不能超过可用证据支持的范围 |
| evidence | 非空数组，记录实际阅读的 URL、kind、supports |
| assessment | contribution、comparison、limitations、importance、confidence |
| research_inspiration | methods、limitations_validation，均为非空数组；其他启发可选 |
| tags | 1–8个主题标签 |
| strong_recommendation | 可选；true时必须有 strong_recommendation_reasons |

总分 = round(relevance*.40 + novelty*.20 + quality*.15 + methodology*.15 + inspiration*.10)。这些是个人阅读优先级，不是客观学术评价。

证据示例（占位符，必须替换为实际读过的页面）：

```json
"evidence": [
  {"url": "https://example.org/实际论文", "kind": "abstract", "supports": ["主要贡献概述", "报告中的第1条发现"]}
],
"assessment": {
  "contribution": "用证据支持的具体贡献",
  "comparison": "对比了哪些工作，改变在哪里；只读摘要时明确尚未独立验证优势",
  "limitations": "实验范围、评测或证据的具体局限",
  "importance": 80,
  "confidence": "medium"
}
```

kind 取 abstract/full_text/publisher/repository；supports 必须非空。confidence 取 high/medium/low，描述对当前解读的证据信心，不是论文录用概率。importance 取0–100，与热度分开；未热门也可以重要。不得把摘要中作者声称的提升表述成独立验证。

默认不要求术语解释。dual 模式的 term_explanations 每项含 term、academic_explanation、plain_explanation，解释各不超过180字符且不同。只解释理解所需关键术语。detailed 模式可额外填 experimental_design、key_entities_mechanisms、target_system、future_direction、paper_potential；不要为凑字段臆造基金或论文潜力。

updates 使用队列中有身份信息的更新记录；它是元数据通知，不是新论文推荐。成功交付后，history_status=update 的旧记录会推进，避免同一出版变化反复通知。推荐列表不得含重复DOI或规范化题名。
