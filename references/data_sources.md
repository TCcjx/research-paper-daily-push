# 数据来源与覆盖限制

当前仅使用公开学术元数据接口。所有网络调用有超时和有界重试；来源逐一隔离。接口有配额，不能承诺无缺漏。

当前默认来源：general/computer_science 为 arXiv、Semantic Scholar、Crossref；biomedical 为 Europe PMC/bioRxiv。OpenAlex 仍暂停默认调用。旧配置显式来源名单不自动改写；用户启用多途径时应同时调整 sources 与 disabled_sources。各来源独立执行，部分失败仍交付候选并说明覆盖缺口；全部失败时停止。

| 来源 | 当前抓取策略 | 限制 |
| --- | --- | --- |
| Crossref | 每查询 × online/pub日期 × journal/proceedings类型，按score相关性倒序游标最多3页，每页100条；发表日期过滤并本地排序 | 达到300条预算时告警；检索语义是普通词检索，不等同原布尔式 |
| Europe PMC | 合并布尔查询，FIRST_PDATE，游标最多4页，每页500条 | 生命科学覆盖为主；超过2000条告警 |
| OpenAlex | 每查询最多600条，游标分页，按发表日期倒序 | 达上限告警；配额或鉴权错误会记录为来源失败 |
| arXiv | 每查询按首次提交时间倒序，最多400条，本地日期切窗 | 相邻请求至少3秒；未翻过窗口就到上限时告警；检索不用于发现所有旧论文修订 |
| bioRxiv | 日期区间最多600条，抓取后本地过滤主题 | 全平台按日期取样，超过预算告警，宽窗口容易截断 |
| Semantic Scholar | bulk每查询最多3页，每页至多1000条；按publicationDate倒序，日期范围过滤，再本地切窗 | 续页token、预算或循环明确告警；日级日期缺失的记录跳过，不能按年份猜成最新；索引可能延迟 |

程序报告 retrieved_count 为来源返回记录数，unique_keyword_count 为去重后进入主题筛选的数量（配置topic_scope时不再预先按评分词表漏筛），off_topic_count 为其中未通过 topic_scope 的数量，unique_relevant_count 为通过主题准入的数量。source_counts 不是各数据库全领域总量。题名/摘要缺少同义词会漏筛，应维护查询、主题词和必要概念词表。

热点线索对同一30天样本的主题近7天与前23天日均数做比值。近7天至少3篇、基线非零、比值至少1.5且无来源/预算警告时标为样本发文升温；零基线只标样本新出现。它不能校正数据库收录延迟或领域会议集中发布造成的偏差。

观测保留各来源 citation_counts，只对同来源的两次健康快照计算差值。引用计数下降可能是索引修订，不能当作负面评价。没有快照/来源缺值是未知，不是零热度。没有跨平台社交或代码热度推断。

watchlist 和最近交付历史的最多20篇仅调用本次启用来源：arXiv ID 查询版本，Crossref 启用后可查询 DOI 出版信息，不受30天发表窗口限制。只有 arXiv 时仅 DOI 的论文会提示无法检查，不自动调用 Crossref。版本变化不证明结论变化；后续支持/反驳与代码更新尚需研究人员或 AI 查证。

官方接口说明：

- [Crossref REST API](https://github.com/CrossRef/rest-api-doc)
- [Europe PMC REST API](https://europepmc.org/RestfulWebService)
- [OpenAlex API](https://developers.openalex.org/)
- [arXiv API](https://info.arxiv.org/help/api/user-manual.html)
- [bioRxiv API](https://api.biorxiv.org/)
- [Semantic Scholar API](https://api.semanticscholar.org/api-docs/graph)

Crossref 游标和 Europe PMC cursorMark 已按官方文档实现；本地单元测试覆盖分页、循环游标和失败场景。实际服务可用性仍取决于运行环境与当时配额。

Crossref 不支持 published 排序与 cursor 同时使用；实现使用 score 相关性排序保持游标兼容，发表范围仍由日期过滤器限定。

候选与最终推荐默认不限篇数（0表示不限），不影响上表的来源分页/请求预算。TKG配置先检索对象，再按主题必要组逐级筛推理；scope_stage_counts保留各阶段数量。场所和分区由复核阶段查官方记录，不通过暂停接口推断。

## 失败重试与最新性

GET检索临时错误（429/500/502/503/504、网络超时等）有界重试。429无Retry-After时约15/30/60秒指数退避，其他临时错误从约3秒起；加入少量随机抖动。尊重Retry-After秒数或HTTP日期；服务器要求超过单次60秒等待预算时停止该请求，不提前重试。arXiv最多4次尝试，其他接口默认3次（均含首次）；非临时4xx直接失败。命令期间自动重试，不创建后台定时任务。

新配置latest_first将实际发表日期放在排序首位，同日Agent结合优先；今天/昨天只是日级近似，较早同主题论文保留为supplementary。arXiv按首次提交日期，Crossref优先online-first日期，不能用数据库入库日期冒充论文发表日期。Semantic Scholar使用布尔bulk查询而非旧的100条相关性检索，避免旧高引用工作挤占新论文。多库覆盖仍可能漏收，不承诺穷尽。
