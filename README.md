# 科研论文雷达 · research-paper-daily-push

为研究人员提供可持续使用的论文工作流：**热点线索 → 广泛候选 → 证据复核 → 精选日报 → 更新跟踪与周报**。

项目既是 Codex Skill，也提供零第三方依赖的 Python 命令。脚本负责收集、去重、观测与校验；研究人员或使用此 Skill 的 AI 负责阅读证据、判断科学贡献。未配置模型 API，不会自动把搜索结果包装成已复核推荐。

## 主要产出

- 发现简报：直接查看候选论文、样本活跃方向和更新，明确标为未复核。
- 重点论文日报：默认精选约3–5篇，包含贡献、相对已有工作的变化、阅读理由、局限与证据链接，允许零推荐。
- 活跃度与关注度证据：近7天相对前23天的主题日均发文变化，以及同来源引用快照变化。
- 持续跟踪：关注和最近交付论文的出版/版本变化，已交付论文不重复当作新论文推送。
- 离线周报与可选微信通知。

发文增长只是热点线索，不代表全领域统计、社交热度或学界共识。当前没有接入 GitHub/社交热榜。

## 快速开始

需要 Python 3.10+。克隆本仓库后在项目目录执行命令，无需安装第三方包。新手见 [安装指南](references/installation_guide.md)。

### 1. 配置研究领域

公开配置保持空白，不擅自使用示例方向。下面仅演示配置语法，请替换成自己的方向：

```powershell
python scripts/run_daily.py init-profile --name "时态知识图谱" --query '"temporal knowledge graph"' --topic "时态知识图谱=temporal knowledge graph,temporal reasoning" --source-preset computer_science --output config/my.local.json
```

继续填写 JSON 的 research_context、priority_questions、查询同义词和领域方法词表。参考 [完整示例](examples/research_profile.temporal-kg.json) 与 [配置字段](references/profile_schema.md)。已配置文件不会被初始化命令覆盖。

| 来源预设 | 来源 |
| --- | --- |
| general | Crossref、OpenAlex、arXiv |
| computer_science | arXiv、Crossref、OpenAlex |
| biomedical | Europe PMC、Crossref、bioRxiv |

sources 非空时覆盖预设；命令行 `--sources arxiv,crossref` 优先。Semantic Scholar 可显式启用。任何预设都不保证穷尽覆盖，须按领域调整。

### 2. 收集并查看发现简报

```powershell
python scripts/run_daily.py collect --profile config/my.local.json
```

控制台显示输出目录，默认 `data/runs/<配置与检索范围标识>/YYYY-MM-DD/`：

| 文件 | 用途 |
| --- | --- |
| discovery.md | 直接阅读，明确标为未复核 |
| review_queue.json | 候选摘要、元数据、热点线索与历史上下文 |
| search_30d.json | 规范化样本及来源警告 |
| ranked_30d.json | 全量初筛与排除理由，便于检查漏筛 |

一次抓取30天样本，本地优先今天和昨天；不足时加入7、14、30天内补充并单独标注。默认复核池50篇，与最终目标5篇分离。`--candidate-limit 100` 扩大复核池；`--max-results 3` 设置精选目标。

初筛分只是元数据代理指标，低于70分仍可复核。默认场所策略 label_only，不因预印本或未进入目录而排除。[CCF目录](references/venue_catalog.md) 仅作背景，不替代单篇证据判断。

### 3. 复核并生成日报

在 Codex 中加载 Skill 后，可以说：“按我的配置运行论文雷达，复核重点论文并展示中文日报。”也可以自己填写复核数据：

```powershell
python scripts/run_daily.py prepare --queue <运行目录>/review_queue.json --output <运行目录>/reviewed_articles.json
```

这一步只预填待编辑草稿，**不会自动阅读或总结论文**。检查完整队列后挑选重点，填写实际阅读的证据 URL、贡献、与已有工作的比较、发现、局限和评分。见 [复核数据约定](references/reviewed_schema.md)。未复核、缺证据、低于70分、重复论文或总分不一致会被严格校验阻止。

```powershell
python scripts/run_daily.py deliver --profile config/my.local.json --reviewed <运行目录>/reviewed_articles.json --queue <运行目录>/review_queue.json --report <运行目录>/daily-report.md --skip-push
```

默认简报，精读用 `report_style: "detailed"`，关键术语双重解释用 `term_explanation_mode: "dual"`。不再强制七类研究启发或无依据的研究灵感。

`--skip-push` 只生成文件，不写交付历史。实际展示后增加 `--record-local-report` 确认本地交付，推进去重与研究记忆。再次把已交付论文作为新推荐会被阻止；重新生成旧报告仅用 `--skip-push`。

### 4. 跟踪与周报

在配置中增加 watchlist 数组，每项为真实的 `{"doi": "10.xxxx/..."}` 或 `{"arxiv": "编号"}`。每次 collect 最多检查20篇明确关注及最近交付论文，跨越30天窗口；`--watch-limit 0` 关闭。旧历史缺少 arXiv ID 时补进 watchlist 才能跟踪版本。改题且没有共同标识的版本关联需人工核对。

```powershell
python scripts/run_daily.py weekly --profile config/my.local.json --output data/runs/weekly-report.md
```

配置、mode、sources 需与 collect 相同；无同范围观测时明确报错，未观测日不补零。

## 可选微信推送

按 [WxPusher设置](references/wxpusher_setup.md) 注入环境变量，本地检查：

```powershell
python scripts/push_wechat.py --provider wxpusher --check-config
```

明确授权真实发送后，将 deliver 的 `--skip-push` 换为 `--provider auto`，按 WxPusher、Server酱、企业微信顺序选择。无渠道仍生成本地报告。凭据只放环境变量，见 [.env.example](.env.example)；项目不会自动加载 .env。

## 可靠性与边界

- Crossref 同时检索期刊与会议，Europe PMC/Crossref 有界翻页；抓取上限见 [数据来源](references/data_sources.md)。宽查询截断时需缩小主题或拆分查询。
- 缺源或截断会明确警告，且不判定升温。全部失败则退出，不能当成“今天无好论文”。
- 引用增长仅比较同来源快照；新论文没有可比引用数据是正常情况。计数存在索引延迟。
- 观测与交付分开保存；同日替换，保留90天，按配置/查询/来源隔离。
- 主入口用数据锁阻止并发覆盖。进程崩溃留下 .lock 时，先确认旧进程已退出再手动移除。
- 不自动创建定时任务。完整调度应包含 collect → 证据复核 → deliver；只调度 collect 仅产生发现简报。需明确时间、时区、在线设备和推送授权。
- 部分发送失败不能视为全部成功；不自动无限重发。

## 开发与验证

```powershell
python -m unittest discover -s tests -v
python -m compileall -q scripts
```

测试覆盖来源规范化、分页、部分/全部失败、广泛复核池、主题轮转、趋势基线、同源引用变化、更新跟踪、严格校验、去重与交付。测试不联网、不发消息。结构校验不能证明证据内容真实，最终仍需阅读。

本地配置推荐 `config/*.local.json`，已被忽略。data/observations.json、运行目录和缓存不提交；公开模板的交付历史与研究记忆保持为空，实际使用后的个人记录也不要提交。

采用 [MIT License](LICENSE)，[中文参考译文](LICENSE.zh-CN.md)。
