# 研究配置结构

公开版本中的 `config/research_profile.json` 有意保持空白。只有必填字段已经填写完整时，才能把 `configured` 设置为 `true`。

## 必填字段

- `profile_name`：显示在运行元数据中的简短配置名称。
- `research_context`：用于帮助判断相关性的精简研究背景。
- `target_system`：生物、疾病、材料、技术、人群或其他主要研究对象。
- `priority_questions`：指导最终证据复核的核心科学问题。
- `queries`：默认 `all` 模式使用的布尔式 API 查询语句。
- `topic_groups`：用于确定性初筛的主题组。每一项包含 `label`、1–100的 `weight` 和非空 `terms` 数组。

至少需要一条查询和一个有效主题组。配置为空时，程序会提示配置错误并停止，不能返回无关论文。

## 可选字段

- `modes`：命名的聚焦模式。每个键对应一个包含 `queries` 数组的对象。
- `cross_topic_bonuses`：包含 `labels`、`bonus` 和可选 `reason` 的条目。只有列出的全部主题组都命中时才应用加分。
- `method_groups`：包含 `label` 和 `terms` 的条目，用于该领域的方法识别；默认不混入其他学科词表。
- `quality_tiers`：由 `tier_1`、`tier_2` 和 `tier_3` 组成的规范化期刊名称数组。它只覆盖人工指定的期刊，与 `config/venue_ranking.json` 的 CCF 目录等级相互独立；同一篇论文两者都有分时取较高者。等级目录和权威性筛选策略见 [发表场所等级目录](venue_catalog.md)。
- `exclusion_terms`：题名或摘要命中后触发初步排除的词语。该列表应保持保守。
- `mechanistic_terms`：提示机制性证据或高信息量证据的领域专用术语。
- `strong_recommendation_rules`：包含 `labels`、可选 `methods`、`min_score` 和 `reason` 的条目。所有已配置条件都必须同时命中。
- `roadmap_stages`：包含 `label` 和 `keywords` 的条目，只用于在研究记忆中归类已经确认的报告。
- `tags`：偏好的报告标签。
- `reading_order`：新模板/初始化采用 `agent_first`，主题和场所达标后Agent结合优先，其余完整保留；兼容 `venue_first`、`score`。候选及最终推荐默认不限篇数，`--candidate-limit 0 --max-results 0`；来源检索预算另计。
- `retrieval_queries`：非空时覆盖各模式的检索式，先广泛召回研究对象，再本地筛任务和方法。TKG示例所有模式共用对象查询，不预先限定Agent/LLM。
- `agent_priority_terms`：仅为候选排序标记Agent线索；正式推荐仍须 `agent_assessment` 原文复核。
- `deduplication_scope`：`last_report` 对最近实际交付论文的报告去重（零新增不清空参照批次）（新模板默认），`all_history` 为全历史。DOI/arXiv ID/题名识别同文，更新另列；不删除旧历史。
- `publication_filter`：最终场所门槛，新模板启用。会议按CCF等级；期刊按CAS/JCR任一达标，不用期刊CCF等级或自定义tier替代分区。未知待核实、已核实不达标过滤，不设评分门槛。见下例。

```json
"publication_filter": {
  "enabled": true,
  "conference_min_ccf": "B",
  "journal_systems": ["cas", "jcr"],
  "journal_max_quartile": 2
}
```

- `topic_scope`：可选硬性主题范围，见下例。缺省或 `{}` 表示不启用；配置后每个必要概念组至少命中一个短语，组间为 AND，组内为 OR。只匹配题名与摘要，英文按词边界匹配并兼容复数与连字符。

```json
"topic_scope": {
  "name": "temporal knowledge reasoning",
  "required_groups": [
    {"label": "时态知识图谱", "terms": ["temporal knowledge graph", "temporal kg", "TKGR", "TKGC", "TKGQA"]},
    {"label": "推理任务", "terms": ["reasoning", "forecasting", "question answering", "TKGR", "TKGC", "TKGQA"]}
  ]
}
```

准入应用于收集、独立排序、趋势统计和最终交付；topic_groups 的高分不能覆盖它。输出保留 `topic_scope_match` 的命中、缺失组、逐级 stages 与 first_failed_stage，检索元数据记录 scope_stage_counts。摘要缺失且题名不足时暂不纳入，先从可信原文补全摘要再重筛；关键词准入不等同于科学相关性已复核。默认查看完整30天并标注实际日期，不用相邻领域填充。完整同义词表示例见 [时态知识推理配置](../examples/research_profile.temporal-kg.json)。

## 聚焦模式查询写法

TKG示例用 `retrieval_queries` 统一检索时态知识图谱对象，按 `required_groups` 顺序先对象后推理，再按Agent优先。`modes` 保留命名入口，不用AND Agent/LLM提前缩小召回。其他领域未配置 retrieval_queries 时仍使用 modes/queries。自然语言主题说明与JSON须保持一致。

权重和关键词命中只用于初步排序，不是最终证据。Codex 仍必须阅读可用摘要或全文，并独立重新评价每篇候选论文。

## 来源和关注列表

source_preset：general / computer_science 默认 arxiv；biomedical 默认 europepmc、biorxiv。sources：来源标识数组，非空时覆盖预设；命令行 --sources 优先。可用标识为 europepmc、crossref、openalex、semanticscholar、biorxiv、arxiv。

disabled_sources：暂停来源名单，模板/初始化默认 `["openalex", "crossref"]`。暂停来源不得出现在最终启用列表，否则在联网前报错。命令行不能绕过；恢复适配器先移出名单再明确启用。旧配置未填写此字段仍兼容，但来源预设已移除这两个接口。

watchlist：对象数组，每项含真实 doi 或 arxiv 编号，优先检查显式关注，再检查最近交付记录；默认合计最多20篇。只使用本次启用来源，arxiv 负责版本，crossref 启用后才检查 DOI 出版信息。无法检查的记录明确提示。--watch-limit 可调整，0关闭。不能用占位符运行。

研究配置的 method_groups 应按领域填写，不提供时方法分只是缺信息的代理值，不应当作方法学质量结论。内置目录 policy=label_only 只负责基础标注；当前最终筛选由独立的 publication_filter 控制。未启用 publication_filter 的旧配置继续兼容旧评分/目录策略。

复核字段见 [reviewed_schema.md](reviewed_schema.md)，来源覆盖与预算见 [data_sources.md](data_sources.md)。
