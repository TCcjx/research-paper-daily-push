# 已复核论文数据

prepare 生成的是待填写草稿，不能直接交付。默认 brief；完整摘要后标记 evidence_reviewed。验证器检查结构、一致性和必要证据记录，不能核实链接内容是否支持结论。

根对象：date（YYYY-MM-DD）、report_style（brief/detailed）、term_explanation_mode（off/dual）、metadata、articles（默认不限；场所门槛启用时不按 metadata.maximum 截断）、research_ideas（0–3条）、radar 和 updates。使用 deliver --queue 可附加同次检索的真实元数据；缺失热点数据时报告明确说明，不能自行伪造。

配置了主题准入时须保留根对象的 `topic_scope` 和每篇的原始 `abstract`。prepare 自动传递范围；deliver 同时检查当前配置和队列范围，独立摘要脚本也检查随报告携带的范围。题名不足时需从实际阅读的原文补全摘要；阅读理由、标签和“可迁移”说明不能代替研究对象的直接证据。关键词门槛通过后仍须核对：时间机制和知识推理是否为论文实质任务，是否只是背景词。

每篇必填字段：

| 字段 | 约定 |
| --- | --- |
| title / chinese_title | 原题 / 忠实中文题名 |
| journal / publication_date / url / article_type | 场所、有效日期、http/https链接、类型 |
| doi / is_preprint / source_ids | DOI可空；保留来源身份和预印本状态以跟踪更新 |
| reading_lane | daily 或 supplementary，依据日期，不把旧论文当作今日新增 |
| recommendation_score | 0–100整数，与分项加权取整一致；场所门槛启用时无70分下限，旧配置保留旧阈值 |
| component_scores | relevance、novelty、quality、methodology、inspiration，各0–100整数 |
| score_status | 必须 evidence_reviewed |
| why_worth_reading | 100字符内，解释为什么值得研究人员阅读 |
| core_findings | 1–5条，不能超过可用证据支持的范围 |
| evidence | 非空数组，记录实际阅读的 URL、kind、supports |
| assessment | contribution、comparison、limitations、importance、confidence |
| venue_evidence | 官方记录：venue 规范场所、url 实际论文/录用记录、publication_type（full/regular/journal）、status（accepted/published）、verified=true；URL也须列入evidence |
| journal_quartiles | 期刊必需：体系、分区、年份、学科、场所和可核实来源，见下例 |
| agent_assessment | agent_first/latest_first必需：integrated=true/false；true时role说明具体作用，evidence_url指向本篇已读证据 |
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

根对象 `publication_filter` 从配置/队列传递。deliver将未知场所或分区放入 `pending_verification`，不达标放入 `publication_exclusions`，与最近报告重复的放入 `duplicates_not_recommended`，更新进入 updates。独立渲染器重算门槛，不能手填 publication_decision 绕过。待核实和过滤列表保留原因，最终推荐数只统计 articles。

期刊分区示例（占位符必须换成实际核实记录）：

```json
"journal_quartiles": [
  {"system": "jcr", "quartile": 2, "year": 2025, "category": "实际学科",
   "venue": "实际期刊规范名称", "source_url": "https://example.org/分区记录", "verified": true}
],
"venue_evidence": {
  "venue": "实际期刊规范名称", "url": "https://example.org/实际论文",
  "publication_type": "journal", "status": "published", "verified": true
},
"agent_assessment": {"integrated": false, "role": "", "evidence_url": ""}
```

system 为 cas 或 jcr，quartile 为1–4，year不晚于报告年份，category须非空，venue须与实际刊物对应。记录具体年份/学科，复核适用版本；CAS一区/二区或JCR Q1/Q2任一已核实达标即可。两套均核实低于门槛才判为不达标；一套未核实且另一套较低则继续待核实，不猜分区。CAS分区不应显示成JCR分区。

会议须核实CCF A/B主会 full/regular 长文。作者自述和简称不足为证，附属卷/短文不继承主会等级。arXiv载体保留is_preprint=true；官方录用/发表记录核实后可按真实场所准入，报告分别展示载体与录用状态。真实论文页面证据不能只用评级目录代替。程序只验证字段与一致性，复核者必须实际核对来源内容。

`latest_first` 按实际发表日期倒序，同日使用下述Agent与场所排序。`agent_first` 排序：经证据确认Agent结合优先，其次参考场所、贡献和日期；两组均无数量配额。填写Agent角色和本篇 evidence 中的URL，LLM或planning关键词本身不足以确认Agent。评分仍需诚实填写，但不再决定准入。兼容 `venue_first` 的场所60%+贡献40%个人重要性代理分，deliver自动计算；它不改变新场所门槛。

默认不要求术语解释。dual 模式的 term_explanations 每项含 term、academic_explanation、plain_explanation，解释各不超过180字符且不同。只解释理解所需关键术语。detailed 模式可额外填 experimental_design、key_entities_mechanisms、target_system、future_direction、paper_potential；不要为凑字段臆造基金或论文潜力。

updates 使用队列中有身份信息的更新记录；它是元数据通知，不是新论文推荐。成功交付后，history_status=update 的旧记录会推进，避免同一出版变化反复通知。推荐列表不得含重复DOI、arXiv ID或规范化题名。

## 人工复核审计

修改队列前保留原始自动队列（如 `review_queue.automated.json`）。可在 `review_audit.json` 中逐篇记录入选、待核实、日期超窗及理由；这些审计字段由复核者维护，不是脚本自动完成的证据核验。

- 日期核验保存原元数据日期、出版商首次在线日期及实际读到的证据URL。卷期日期不能覆盖更早的首次在线日期；缺失日级日期或有冲突时继续待核实。日期超窗可单列 `date_exclusions`，不要写入代表场所不达标的 `publication_exclusions`；报告中通过 `metadata.warnings` 说明原因。
- 官方页面索引可用 `evidence.retrieval_method=web_search_index`，接口记录可用 `source_api`，并在 supports 中限定可见信息。保留正文访问失败记录，不能仅因URL存在就设置verified；摘要级复核须在局限中明确未读全文。
- 日期修正后的样本可另存 `search_30d.reviewed.json`，保留原始检索数据，并重新计算同范围的radar与观测快照。程序主题初筛数量与人工日期复核数量应分别说明。
- 仅 `article_type` 同义标签变化的记录可留作 `metadata_only_differences` 审计，从报告updates中移除；不据此声称新增出版、录用或版本变化。
