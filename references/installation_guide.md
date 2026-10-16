# 安装与首次运行

1. 安装 Python 3.10 或更新版本，终端运行 `python --version` 确认可用。
2. 克隆或下载本仓库，终端切换到项目根目录。脚本只用标准库，无须 pip install。
3. 作为 Codex Skill 使用时，将整个目录安装到 Skills 目录，保持 scripts/config/references 相对位置。也可以直接在仓库运行命令。
4. 按 [README快速开始](../README.md) 用 init-profile 创建自己的 config/my.local.json，或自行填写正式配置。公开配置为空，不能直接开始检索。示例不会自动启用。
5. 执行 collect，打开控制台显示的 discovery.md。它是未复核发现简报，不是已验证推荐。
6. 使用此 Skill 让 AI 阅读证据并复核，或自行用 prepare 创建草稿并填写。按 [数据约定](reviewed_schema.md) 完成后 deliver --skip-push 生成日报。
7. 实际展示/阅读报告后，才用 --record-local-report 记录交付。需微信时按 [推送设置](wxpusher_setup.md) 配置环境变量并明确授权。

常见问题：

- 尚未配置研究主题：填写配置，或用 --profile 指向已配置文件。
- 返回很少：检查同义词、领域来源、source_counts 和 warnings，不为凑数降低证据要求。
- 接口429/鉴权错误：按官方渠道配置凭据、减少查询频率，或显式选用仍可用来源。报告须保留缺源说明，不能宣称完整覆盖。
- 存在 .lock：先确认旧进程已退出，再删除对应锁。不要在任务运行时删除。
- 缺少 evidence/assessment：草稿未完成阅读证据，不是网络故障。不能将 pending 状态直接改成 reviewed 规避复核。
- 周报没有观测：先用同一 profile、mode、sources collect；缺失日期不会凭空补全。
- Windows下测试临时目录无权限：设置 TEMP/TMP 到自己可写目录后重跑，避免使用受限系统临时目录。

仓库不会自动读取 .env，也不会自动创建计划任务。仅调度 collect 可以持续生成发现简报；完整日报还需要 AI/人工复核步骤。
