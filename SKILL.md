---
name: research-paper-daily-push
description: 按研究领域收集近期论文、识别有证据的热点线索、复核精选重点论文、持续跟踪出版与版本更新，生成中文日报或周报并可选微信推送。适用于研究人员跟进进展，不用于穷尽式系统综述。
---

# 科研论文雷达

让研究人员快速知道哪些方向变活跃、哪些论文值得读，以及与已有工作的关系。默认简短日报，复核池与最终推荐不设篇数上限；只保留主题一致、场所达标且未与上次交付重复的论文。精读时再展开方法与术语。

## 配置

使用 `config/research_profile.json` 或用户指定的 `--profile`。`configured=false` 时根据用户明确提供的领域先配置，不猜主题、不自动套用示例。以实际配置的 `research_context`、`priority_questions` 为复核背景；存在配套主题说明时一并阅读并保持一致。字段见 [profile_schema.md](references/profile_schema.md)。

用户要求主题一致时，配置 `topic_scope` 的必要概念组，所有组都须在题名或摘要中有依据。排序加分不能覆盖主题准入；Agent、记忆等方法词不能单独成为研究主题。元数据不足时核对原文摘要后更新队列，不能把“可迁移、有启发”当成直接相关。无同主题论文时少推或零推荐，不扩大到相邻领域凑数。

可运行 `python scripts/run_daily.py init-profile --name "研究名称" --query "检索式" --topic "主题=短语,同义词" --source-preset computer_science --output config/my.local.json`。继续补充科学问题、同义词、领域方法词表。没有提供领域时先询问领域及核心问题。

## 检索与发现

运行 `python scripts/run_daily.py collect --profile <配置> --mode all`，控制台显示输出目录 `data/runs/<scope>/<日期>/`。阅读 `discovery.md` 与 `review_queue.json`，两者都是待复核数据。默认 `--candidate-limit 0 --max-results 0`，不截断；接口自身的检索预算仍须说明，不能声称穷尽全部文献。

当前研究方向按固定顺序处理：**先时态知识图谱 → 再推理任务 → 场所达标与上次交付去重 → Agent 结合优先**。`retrieval_queries` 只限定 TKG 对象，所有模式共用，不能预先 AND Agent/LLM 而漏掉传统推理工作。按 `topic_scope.required_groups` 顺序逐级筛选，记录各阶段数量；静态KG、泛Agent和单纯图谱存储不进入最终推荐。

统一查看30天；今天/昨天是日级日期近似，其余保留 `reading_lane=supplementary`，不得当作今日新增。`reading_order=agent_first` 先展示经原文证实的 Agent 结合论文，同组内参考场所、贡献与日期，其余达标推理论文全部保留。LLM方法不自动等同于Agent。

来源按预设选择：general 和 computer_science 使用 arXiv；biomedical 使用 Europe PMC/bioRxiv。OpenAlex、Crossref 暂不纳入默认检索，新配置在 `disabled_sources` 中暂停这两个来源。暂停同时适用于版本跟踪，显式 `--sources` 也不能绕过；需恢复时先修改配置。Semantic Scholar 仅显式启用，不自动替补。来源失败、分页上限必须展示；全部来源失败则停止，不能说今天没有好论文。API范围与上限见 [data_sources.md](references/data_sources.md)。

## 热点与重要性

程序比较同一查询/来源样本中近7天与前23天的主题日均发文数，并附论文链接。这是样本发文活跃度，不是全领域统计、社会热度或学界共识。缺源、截断时不判断升温；零基线仅提示样本新出现，不能报无限增长。

仅通过主题准入的论文进入主题统计。暂停来源不作为本次请求失败，但减少来源也不代表覆盖完整；不能因移除错误来源就声称领域升温。配置、主题或来源改变后使用独立观测范围，不沿用旧范围的引用增长。

同来源引用快照变化是辅助关注度证据，存在索引延迟。无可比快照时写未知，不写零热度。未经查询的 GitHub stars、下载量、社交讨论和排行榜不能补造。用户需要社区热点时另查相关领域可信的一手渠道，标明来源、时间及其与论文的关系。

重要性与热度分别判断。新基准、关键方法、可靠负结果、解决核心问题的工作可以重要而暂不热门。场所高、作者知名或摘要写 novel 都不能独立证明突破。

启用 `publication_filter` 后，会议只保留已核实 CCF A/B 主会 full/regular 长文；期刊按中科院一区/二区 **或** JCR Q1/Q2，满足任一体系即可。期刊的 CCF C 等级不代表分区不达标，不能据此排除。Findings/Workshop/Short/Demo 不继承主会等级。未确认等级或分区时留在 `pending_verification`，确认不达标进入 `publication_exclusions`，都不混入推荐。内置CCF目录不完整，未命中只能待核实。

逐篇访问官方论文集、会议正式录用名单或出版商记录；`venue_evidence` 填规范场所、实际论文 URL、publication_type、status 和 verified，URL 必须出现在实际阅读的 evidence 中。期刊额外填写 `journal_quartiles` 的体系、分区、年份、学科、期刊名与来源链接，不猜测、不用自定义 tier 代替分区。arXiv 仅是载体，官方录用已核实的论文仍可按真实场所准入；保留预印本身份，不笼统声称都未同行评议。字段见 [reviewed_schema.md](references/reviewed_schema.md)。

## 证据复核

可运行 `python scripts/run_daily.py prepare --queue <队列> --output <reviewed_articles.json>` 生成待填写草稿。检查完整队列后调整入选，不只复核草稿预填的前几篇。草稿的空证据和 pending 状态会阻止交付。

阅读摘要；摘要缺失或不足时核对 DOI、出版商、可信论文库或可访问全文。记录实际阅读的 URL、证据类型及其支持的结论。题名不能支撑实验发现；未读全文不能声称已读全文。区分作者主张、独立比较、相关性与因果。不补造实验数值、代码、机制或结论。

按 [reviewed_schema.md](references/reviewed_schema.md) 完成 JSON。每篇给中文与英文题名、日期、链接、预印本状态、100字内阅读理由、1–5条发现、主要贡献、与已有工作的比较、重要性与置信度、局限和可借鉴方法。未完成独立比较时如实说明，不能凭空写领先SOTA。

复核后重评五项0–100分：相关性、创新性、单篇质量、方法价值、研究启发。总分 `round(relevance*.40 + novelty*.20 + quality*.15 + methodology*.15 + inspiration*.10)` 仅供阅读参考；启用场所门槛时不设70分下限，不能用评分排除已通过主题和场所复核的论文。只有检查证据后才设 `score_status=evidence_reviewed`。同一 DOI、arXiv ID 或规范化题名不能重复入选。填写 `agent_assessment.integrated=true/false`；true 时说明 Agent 具体作用并引用本篇已阅读证据，不凭方法关键词确认。

默认 `report_style=brief`、`term_explanation_mode=off`。精读用 detailed，关键术语双重解释用 dual；只解释理解所需术语，不强制解释全部术语。研究思路可0–3条，无证据不填。预印本保留载体标识，录用/评议状态按官方证据另行标注。未核实达标场所的预印本仅留待核实。陌生场所不等于低质量。目录规则见 [venue_catalog.md](references/venue_catalog.md)。

## 交付和跟踪

本地生成：`python scripts/run_daily.py deliver --reviewed <已复核JSON> --queue <对应队列> --report <报告.md> --skip-push`。先展示报告；实际展示后才增加 `--record-local-report` 写历史。文件生成不等于用户已阅读。

用户或定时任务明确授权外部发送后才可运行 `deliver ... --provider auto`。凭据仅从环境变量读取；推送设置见 [wxpusher_setup.md](references/wxpusher_setup.md)。无渠道时保留报告并提示“微信推送尚未配置”。部分发送失败不能声称完整交付，也不能无限重发。

`--queue` 追加对应检索的热点、警告与更新，并校验推荐来源与主题。保留 `topic_scope` 和原始摘要，不能用复核说明重写研究对象。当前 `deduplication_scope=last_report`：按最近一次实际交付论文的报告去重（零新增日报不清空参照批次）；缺少记忆时退回历史中的最近交付日期。原始历史保留；`all_history` 可切回全历史。相同 DOI/arXiv ID/题名视为同文，版本和正式出版变化进入 updates，不再次作为新推荐。watchlist 可关注 DOI/arXiv ID；每次默认检查明确关注和最近交付的最多20篇，仅调用本次启用的来源，跨越30天窗口，`--watch-limit 0` 关闭。当前只有 arXiv 时无法检查仅 DOI 的出版变化，会明确提示。更新栏目只说明元数据证实的出版/版本变化；不把版本变化当成新结论。后续工作的支持或反驳仍需实际阅读。

观测 `data/observations.json` 与交付记忆分离：按配置、查询、来源隔离，同日替换，保留90天；交付历史和研究记忆仅在确认成功后推进。记忆不能证明用户偏好。语义改题且无共同标识的版本关联需人工核对，不自动模糊合并。

运行 `python scripts/run_daily.py weekly --profile <配置> --output <周报.md>` 汇总同范围最近7天实际观测；无观测日不补零，无数据时不能虚构趋势。项目不预设定时任务，仅用户明确要求时创建，并注明时区、设备和外部推送授权。
