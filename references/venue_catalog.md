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

## 当前筛选策略

内置目录的 `label_only` 用于基础等级标注，最终准入由研究配置的 `publication_filter` 控制：会议CCF A/B主会长文；期刊CAS一区/二区或JCR Q1/Q2任一体系达标。CCF期刊等级和CAS/JCR分区独立，CCF C期刊可能分区达标，不能据CCF字母排除。未启用publication_filter的旧配置继续兼容目录策略和评分阈值。

项目不附带猜测的期刊分区库；复核每篇期刊时记录具体体系、年份、学科、刊名及来源链接。任一套分区达标可准入；两套均核实不达标才排除；未知留待核实。内置CCF目录未覆盖的会议也留待核实，先依官方目录补齐后再推荐。

作者自述、简称或部分匹配保留candidate_ccf，不能直接准入。每篇须有官方论文页面或正式录用记录venue_evidence（规范场所、URL、类型、accepted/published、verified）。会议只接受full/regular，Findings/Workshop/Short/Demo不继承主会评级。arXiv是载体，不等同于尚未录用；核实正式场所后保留载体标识并单独展示真实录用状态。

新模板采用agent_first：满足主题与场所后优先展示Agent结合；其余达标论文全部保留，不限篇数或评分门槛。兼容venue_first的场所60%+贡献40%重要性代理分，它不代表科学突破，也不能代替证据复核。
