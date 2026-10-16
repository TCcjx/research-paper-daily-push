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

## 聚焦模式查询写法

聚焦模式（`modes`）的查询建议采用「精确查询 + 兜底查询」的组合：

- 先写一到两条精确查询：用引号短语加 AND 限定词，保证命中结果与方向高度相关。
- 再加一条裸短语兜底查询：把核心短语单独作为 OR 分支，或只配宽松限定词。它可以捕捉不含限定词、但确实属于本方向的相关论文（例如只写 "temporal graph representation learning" 而不含 reasoning/embedding 等词的工作）。
- 兜底查询召回更宽，会放大各来源的检索噪声：Crossref 会把布尔查询拆成普通词检索，宽词误命中尤其多。初筛分数只是元数据排序信号，低于70分仍可进入广泛复核队列，入选与否必须以人工摘要复核为准。

精确查询与兜底查询都应在自然语言主题文件中有对应描述，保持两个文件一致。

权重和关键词命中只用于初步排序，不是最终证据。Codex 仍必须阅读可用摘要或全文，并独立重新评价每篇候选论文。

## 来源和关注列表

source_preset：general / computer_science / biomedical，默认为general。sources：来源标识数组，非空时覆盖预设；命令行 --sources 最高优先级。可用标识为 europepmc、crossref、openalex、semanticscholar、biorxiv、arxiv。

watchlist：对象数组，每项含真实 doi 或 arxiv 编号，优先检查显式关注，再检查最近交付记录；默认合计最多20篇。--watch-limit 可调整，0关闭。不能用占位符运行。

研究配置的 method_groups 应按领域填写，不提供时方法分只是缺信息的代理值，不应当作方法学质量结论。场所默认 label_only，不排除预印本/未收录场所；权威性过滤仅在用户明确选择时启用。

复核字段见 [reviewed_schema.md](reviewed_schema.md)，来源覆盖与预算见 [data_sources.md](data_sources.md)。
