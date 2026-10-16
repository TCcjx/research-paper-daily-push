# 科研论文雷达 · research-paper-daily-push

帮助研究人员持续跟进同一方向的论文：**收集候选 → 核实证据与发表场所 → 推荐阅读 → 跟踪更新**。

既可作为 Codex Skill 使用，也提供零第三方依赖的 Python 命令。脚本处理检索、去重和报告校验；研究人员或 Codex 负责阅读论文、核实贡献。

## 工作流程

```mermaid
flowchart TD
    A["配置研究方向与主题边界"] --> B["检索近30天论文"]
    B --> C["去重与主题准入"]
    C --> D["候选池：最多100篇"]
    D --> E["研究人员 / Codex 复核<br/>摘要、全文、正式发表记录"]
    E --> F["同主题内已核实 CCF A 类优先"]
    F --> G["中文日报：推荐目标10篇<br/>贡献、局限、阅读理由、证据链接"]
    C --> T["同主题样本活跃度观测"]
    T --> G
    T --> W["观测周报"]
    G --> H["本地展示 / 可选微信推送"]
    H -->|确认交付| I["交付历史与研究记忆"]
    I -.->|下次去重与版本跟踪| B
```

- **主题一致**：先满足研究对象与任务边界，再比较场所和贡献；不以相邻领域论文凑数。
- **A 类优先**：核实会议/期刊等级，阅读重要性按场所60%＋贡献40%计算；仍需原文支持贡献判断。
- **推荐更多**：默认复核100篇、推荐目标10篇，区分当日论文和近期补充；证据不足时允许少推或零推荐。

## 快速开始

需要 **Python 3.10+**。在仓库目录执行；公开配置保持空白，以下以 **Temporal Knowledge Reasoning，重点 Agent × TKG** 为例。其他领域见[配置说明](references/profile_schema.md)。

**1. 复制示例并收集候选**

```powershell
Copy-Item examples/research_profile.temporal-kg.json config/my.local.json
python scripts/run_daily.py collect --profile config/my.local.json --mode agent_tkg
```

控制台会显示 `data/runs/<scope>/<日期>/`。先读 `discovery.md`（未复核简报），再查看 `review_queue.json`（含摘要的候选队列）。

**2. 复核论文**

在已安装 Skill 的 Codex 中可直接说：

> 使用 research-paper-daily-push，按 config/my.local.json 的 agent_tkg 模式收集并复核论文。同主题内 A 类优先，推荐目标10篇，并展示中文日报。

也可手动生成复核草稿：

```powershell
$runDir = "替换为控制台输出的运行目录"
python scripts/run_daily.py prepare --queue "$runDir/review_queue.json" --output "$runDir/reviewed_articles.json"
```

阅读摘要或全文，按[复核数据约定](references/reviewed_schema.md)填写贡献、局限、评分与证据链接，并核对正式发表记录。**草稿不会自动完成论文阅读，未复核数据不能交付。**

**3. 生成日报**

```powershell
python scripts/run_daily.py deliver --profile config/my.local.json --reviewed "$runDir/reviewed_articles.json" --queue "$runDir/review_queue.json" --report "$runDir/daily-report.md" --skip-push
```

报告实际展示后，可增加 `--record-local-report` 确认交付、写入去重历史。微信发送需配置渠道并明确授权，见[推送设置](references/wxpusher_setup.md)。

## 来源与边界

计算机方向默认使用 **arXiv**；生物医学预设使用 Europe PMC、bioRxiv。**OpenAlex、Crossref 当前暂停默认使用**，版本跟踪也遵守来源选择。arXiv 无法覆盖全部会议和期刊，正式发表状态需另行核实。

热点仅表示同主题检索样本的发文活跃度；缺源或截断时不判断升温。项目不自动创建定时任务，仅运行 `collect` 会生成待复核候选。

## 更多说明

[安装指南](references/installation_guide.md) · [研究主题示例](examples/research_topics.temporal-kg.md) · [场所评级](references/venue_catalog.md) · [来源覆盖](references/data_sources.md) · [完整 Skill 工作流](SKILL.md)

开发验证：`python -m unittest discover -s tests -v`

[MIT License](LICENSE) · [中文参考译文](LICENSE.zh-CN.md)
