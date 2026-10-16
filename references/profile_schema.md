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
- `reading_order`：`venue_first` 或 `score`。新模板/初始化采用 venue_first，同主题内已核实 CCF A 类优先，再比较其他等级及贡献；收集查看完整30天，仍区分当日/补充。旧配置不填时保持 score。场所等级排序不放宽 topic_scope；A类不足允许其他同主题论文补充。默认推荐目标10篇、候选上限100篇，命令行可调整。
- `topic_scope`：可选硬性主题范围，见下例。缺省或 `{}` 表示不启用；配置后每个必要概念组至少命中一个短语，组间为 AND，组内为 OR。只匹配题名与摘要，英文按词边界匹配并兼容复数与连字符。

```json
"topic_scope": {
  "name": "temporal knowledge reasoning",
  "required_groups": [
    {"label": "知识对象", "terms": ["knowledge graph", "temporal knowledge", "TKGR"]},
    {"label": "时间建模", "terms": ["temporal", "time-aware", "TKGR"]},
    {"label": "推理任务", "terms": ["reasoning", "forecasting", "question answering", "TKGR"]}
  ]
}
```

准入应用于收集、独立排序、趋势统计和最终交付；topic_groups 的高分不能覆盖它。输出保留 `topic_scope_match` 的命中与缺失组。摘要缺失且题名不足时暂不纳入，先从可信原文补全摘要再重筛；关键词准入不等同于科学相关性已复核。主题内候选不足才扩展日期窗口，不用相邻领域填充。完整同义词表示例见 [时态知识推理配置](../examples/research_profile.temporal-kg.json)。

## 聚焦模式查询写法

聚焦模式（`modes`）的查询建议采用「精确查询 + 兜底查询」的组合：

- 先写一到两条精确查询：用引号短语加 AND 限定词，保证命中结果与方向高度相关。
- 兜底查询仍保留核心研究对象，可去掉 Agent/LLM 等实现限定，不能退化为泛图学习或泛 Agent 查询。
- 召回结果须通过同一 topic_scope。初筛分数只是元数据排序信号，低于70分仍可进入主题内复核队列，入选与否必须以摘要或全文复核为准。

精确查询与兜底查询都应在自然语言主题文件中有对应描述，保持两个文件一致。

权重和关键词命中只用于初步排序，不是最终证据。Codex 仍必须阅读可用摘要或全文，并独立重新评价每篇候选论文。

## 来源和关注列表

source_preset：general / computer_science 默认 arxiv；biomedical 默认 europepmc、biorxiv。sources：来源标识数组，非空时覆盖预设；命令行 --sources 优先。可用标识为 europepmc、crossref、openalex、semanticscholar、biorxiv、arxiv。

disabled_sources：暂停来源名单，模板/初始化默认 `["openalex", "crossref"]`。暂停来源不得出现在最终启用列表，否则在联网前报错。命令行不能绕过；恢复适配器先移出名单再明确启用。旧配置未填写此字段仍兼容，但来源预设已移除这两个接口。

watchlist：对象数组，每项含真实 doi 或 arxiv 编号，优先检查显式关注，再检查最近交付记录；默认合计最多20篇。只使用本次启用来源，arxiv 负责版本，crossref 启用后才检查 DOI 出版信息。无法检查的记录明确提示。--watch-limit 可调整，0关闭。不能用占位符运行。

研究配置的 method_groups 应按领域填写，不提供时方法分只是缺信息的代理值，不应当作方法学质量结论。场所默认 label_only，不排除预印本/未收录场所；权威性过滤仅在用户明确选择时启用。

复核字段见 [reviewed_schema.md](reviewed_schema.md)，来源覆盖与预算见 [data_sources.md](data_sources.md)。
