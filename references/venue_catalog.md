# 发表场所等级目录

`config/venue_ranking.json` 是等级标注的唯一数据来源，由 `scripts/venue_ranking.py` 加载。它决定报告中每篇论文的“认可度/等级”一行、`quality` 分项的基础分，以及在 `authority_first` 等策略下是否进入复核队列。

## 数据来源与核实要求

等级取自中国计算机学会《推荐国际学术会议和期刊目录》第七版（2026），发布日期 2026-03-31，更正日期 2026-04-09。第七版只在官方 PDF 中完整给出，官网各领域页面在当时仍显示上一版内容，因此以 PDF 为准；期刊与会议全名取自各领域页面，简称与别名用于匹配出版商返回的实际字符串。

红线：任何条目都不允许凭印象填写。新增条目必须能指向官方 PDF 或领域页面的具体行；无法确认等级时不要写入目录，让程序把论文标成“需要核实”。

## 当前覆盖范围

目录有意不完整，目前收录以下领域；项目没有预设研究方向：

| 领域 | 期刊 A/B/C | 会议 A/B/C |
| --- | --- | --- |
| 人工智能 | 4 / 21 / 39 | 7 / 14 / 22 |
| 数据库、数据挖掘、内容检索 | 4 / 14 / 16 | 5 / 13 / 13 |
| 交叉、综合、新兴 | 3 / 5 / 0 | 1 / 0 / 2 |

体系结构、网络、安全、软件工程、理论、图形学、人机交互等领域，以及交叉领域的多数条目没有收录。未命中目录不等于论文质量低，也不等于不在 CCF 目录中，只能标注“正规出版物，未收录于内置目录（需要核实）”。换研究方向时应先补齐对应领域，而不是把未命中当成低等级。

## 文件结构

- `catalog`：目录名称、版本、发布与更正日期、官方页面、覆盖领域和完整性说明。这些信息会出现在报告表头，不要删改版本字段。
- `scoring`：等级基础分（`ccf.A`/`ccf.B`/`ccf.C`）、`formal_unlisted`、`venue_missing`、`preprint`、`preprint_with_reported_venue`、`citation_bonuses`（按引用数追加的 `[阈值, 加分]` 列表）、`review_article_cap` 和 `abstract_missing_penalty`。
- `policy`：`mode` 取 `label_only`（只标注）、`authority_first`（预印本和未核实场所必须达到相关性阈值）或 `strict_ccf_or_tier`（只保留命中目录或配置分层的论文）；`preprint_min_relevance`、`unlisted_venue_min_relevance` 是相关性分量阈值，不是总分；`strong_recommendation_requires_verified_venue` 控制等级未核实时能否升级为强烈推荐。
- `labels`：报告中展示的等级文案模板。
- `conflicts`：简称阻塞表。键是目录里的简称，值是规范化后的名称片段；只要 venue 字符串包含其中任一片段，就不允许再用该简称匹配等级。程序会先用目录自身的名称和别名自动推导这张表（例如 `sigmod`、`vldb` 出现在彼此名称中时自动阻塞），出现在目录之外的场所需要人工补一行：已知的例子是 `Proceedings of the AAAI/ACM Conference on AI, Ethics, and Society`（AIES）中含有 `AAAI`，不加阻塞会被误标为 AAAI 主会的 CCF-A。
- `venues`：条目数组，每项包含 `name`（官方全名）、`abbr`、`kind`（`journal` 或 `conference`）、`domain`、`ccf`（A/B/C）和可选 `aliases`（出版商字符串变体、改名前后的名称）。

## 匹配顺序与保守策略

`classify()` 依次尝试：规范化全名与别名精确匹配 → 去掉通用词后的关键集合重叠不低于 0.8 → 在规范化字符串中按词边界匹配简称。预印本身份优先于一切：期刊字段是 arXiv、bioRxiv、medRxiv 等预印本平台时，不给出 CCF 等级。

以下情况一律算“未核实”，不能作为等级依据；强烈推荐需依赖单篇证据，不能依靠未经核实的等级：只匹配到作者自述的录用信息（`journal_reference`、`comment` 等字段）、按简称匹配、名称或简称同时命中期刊与会议条目（此时按较低等级标注并置 `ambiguous` 为真）、题名含 Findings、Workshop 等附属卷标记。附属卷不继承主会等级。

`quality` 分项的取值顺序是：命中目录的等级分 → 作者自述场所的预印本分 → 预印本分 → 命中 `research_profile.json` 中 `quality_tiers` 的分层分 → 正规未收录分 → 场所信息缺失分。目录分与配置分层分同时存在时取较高者，因此人工维护的期刊分层可以覆盖未收录期刊。

## 扩充与校验

新增条目的顺序：先确认官方 PDF 中的具体行，再补 `name`、`kind`、`ccf`，然后按出版商实际写法补 `aliases`（例如带 “Proceedings of the” 前缀、带年份、改名前后的名称）。修改后至少跑一次针对性校验，确认真实 venue 字符串匹配到预期条目：

```bash
python -c "import sys; sys.path.insert(0,'scripts'); \
from venue_ranking import load_ranking, classify; \
r=load_ranking(); \
print(classify({'journal':'IEEE Transactions on Knowledge and Data Engineering','article_type':'journal-article'}, r))"
```

同时检查两条边界：新条目的简称不会在别的 venue 字符串里被误命中（必要时写进 `conflicts`），简称歧义时确实降级。等级目录只影响“认可度”，不能替代论文本身的证据复核。

## 默认策略

排除策略仍为 label_only，不按场所身份额外排除；阅读顺序独立由研究配置 reading_order 控制。新配置默认 venue_first，同主题内已核实 CCF A→用户配置一档期刊→CCF B→二档期刊→CCF C→三档期刊→未分级/待核实。用户期刊分层不冒充 CCF 等级，默认空白，需要依据后再维护。

场所优先模式下阅读重要性按场所60%+贡献40%计算，venue_importance 为个人代理分，不是客观科学质量。简报、正文与前三名都采用相同场所优先顺序。A类不足仍可推荐主题内其他等级或预印本；默认推荐目标10篇、复核池100篇，并查看30天补充。

作者自述、简称或部分名称匹配保留 candidate_ccf，verified_venue=false；不得继承为 A 类优先。最终已分级论文须提供 venue_evidence（正式论文 URL、full/regular/journal 类型）。按 CCF 官方范围，会议评级仅对应正式长文，短文、Demo、Findings、Workshop 等不继承主会等级。当前只从 arXiv 自动收集时，应在人工复核阶段访问官方论文集/期刊记录，不能凭 arXiv 页面推断尚未录用。

strong_recommendation_requires_verified_venue 默认为 false，允许证据充分的预印本重点阅读；依旧标注预印本身份。默认关闭累计引用加分。用户可自行选择 authority_first 或 strict_ccf_or_tier，但会减少前沿召回。
