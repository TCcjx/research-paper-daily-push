#!/usr/bin/env python3
"""首次配置、复核草稿和离线周报的命令入口。"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from common import atomic_write_json, load_json
from profile_config import load_profile, profile_sources, profile_queries, SOURCE_PRESETS
from research_radar import scope_key
from summarize_papers import render_radar


def init_profile(args) -> None:
    path = Path(args.output)
    existing = load_json(path, {})
    if existing.get("configured"):
        raise ValueError("目标已配置，请选择新的 --output 路径，避免覆盖已有研究方向")
    groups = []
    for value in args.topic:
        label, separator, terms = value.partition("=")
        words = [t.strip() for t in terms.split(",") if t.strip()]
        if not separator or not label.strip() or not words:
            raise ValueError('--topic 格式为 "主题名称=英文短语,同义词"')
        groups.append({"label": label.strip(), "weight": 80, "terms": words})
    profile = {"configured": True, "profile_name": args.name, "source_preset": args.source_preset,
               "sources": [], "disabled_sources": ["openalex"], "topic_scope": {}, "reading_order": "latest_first",
               "deduplication_scope": "last_report", "agent_priority_terms": ["agent", "agentic", "multi-agent", "智能体"],
               "publication_filter": {"enabled": True, "conference_min_ccf": "B", "journal_systems": ["cas", "jcr"], "journal_max_quartile": 2},
               "research_context": args.context or "", "priority_questions": [],
               "queries": args.query, "topic_groups": groups, "method_groups": [], "watchlist": [],
               "quality_tiers": {"tier_1": [], "tier_2": [], "tier_3": []},
               "exclusion_terms": [], "modes": {}, "cross_topic_bonuses": [],
               "strong_recommendation_rules": [], "roadmap_stages": [], "tags": []}
    # 写入前复用运行时校验，避免生成无法使用的配置。
    if not args.name.strip() or not any(q.strip() for q in args.query):
        raise ValueError("名称和查询不能为空")
    atomic_write_json(path, profile)
    print(f"研究配置已生成：{path}；请补充 research_context、priority_questions 和领域方法词表。")


def prepare_review(args) -> None:
    queue = load_json(args.queue, {})
    if queue.get("status") not in {"ok", "partial"}:
        raise ValueError("只能从检索成功的队列生成复核草稿")
    target = Path(args.output)
    if target.exists():
        raise ValueError("草稿已存在，请选用新路径，避免覆盖复核结果")
    articles = []
    maximum = None if (queue.get("publication_filter") or {}).get("enabled") else queue.get("metadata", {}).get("maximum") or None
    for candidate in queue.get("candidates", [])[:maximum]:
        article = dict(candidate)
        article.update({"score_status": "pending_evidence_review", "chinese_title": "",
                        "why_worth_reading": "", "core_findings": [], "evidence": [],
                        "assessment": {"importance": 0, "confidence": "low", "contribution": "",
                                       "comparison": "", "limitations": ""},
                        "research_inspiration": {"methods": [], "limitations_validation": []},
                        "tags": candidate.get("matched_topics") or [], "strong_recommendation": False})
        if (queue.get("publication_filter") or {}).get("enabled"):
            article["agent_assessment"] = {"integrated": None, "role": "", "evidence_url": ""}
        articles.append(article)
    draft = {"date": queue["date"], "report_style": "brief", "term_explanation_mode": "off",
             "topic_scope": queue.get("topic_scope", {}),
             "publication_filter": queue.get("publication_filter", {}),
             "metadata": queue.get("metadata", {}), "radar": queue.get("radar", {}),
             "updates": queue.get("updates_not_for_new_push", []),
             "duplicates_not_recommended": queue.get("duplicates_not_recommended", []),
             "articles": articles, "research_ideas": []}
    atomic_write_json(target, draft)
    print(f"待填写复核草稿：{target}；预填 {len(articles)} 篇，默认不截断。请完成主题、场所与Agent证据复核后交付。")


def weekly_report(args) -> None:
    profile = load_profile(args.profile)
    key = scope_key(profile, profile_queries(profile, args.mode), profile_sources(profile, args.sources))
    today = date.fromisoformat(args.to_date) if args.to_date else date.today()
    cutoff = (today - timedelta(days=6)).isoformat()
    snapshots = [s for s in load_json(args.observations, {}).get("snapshots", [])
                 if s["scope"] == key and cutoff <= s["date"] <= today.isoformat()]
    if not snapshots:
        raise ValueError("过去7天没有相同研究配置、查询和来源的观测，请先执行 collect")
    snapshots.sort(key=lambda s: s["date"])
    latest = snapshots[-1]
    papers = {r["key"]: r for s in snapshots for r in s["records"]
              if cutoff <= r.get("publication_date", "") <= today.isoformat()}
    lines = ["# 科研文献周报（观测汇总）", "", f"区间：{cutoff} 至 {today.isoformat()}",
             f"实际观测 {len(snapshots)} 天；最近观测：{latest['date']}；样本中本周发表论文 {len(papers)} 篇。",
             "缺失观测日不补零；检索活跃度不代表全领域热度或论文质量。", ""]
    lines += render_radar({"topics": latest.get("topics", []), "limitations": "使用最近一次同范围样本的近7天/此前基线对比。"})
    lines += ["## 本周论文索引（未复核）", ""]
    for r in sorted(papers.values(), key=lambda r: r.get("publication_date", ""), reverse=True):
        lines.append(f"- [{r['title']}]({r['url']})｜{r['publication_date']}")
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"周报已生成：{target}")


def add_commands(subparsers, default_profile: str, default_observations: str) -> None:
    init = subparsers.add_parser("init-profile", help="根据明确提供的主题生成研究配置")
    init.add_argument("--name", required=True)
    init.add_argument("--query", action="append", required=True)
    init.add_argument("--topic", action="append", required=True)
    init.add_argument("--context")
    init.add_argument("--source-preset", choices=SOURCE_PRESETS, default="general")
    init.add_argument("--output", default=default_profile)
    prepare = subparsers.add_parser("prepare", help="从队列生成待填写的证据复核草稿")
    prepare.add_argument("--queue", required=True)
    prepare.add_argument("--output", required=True)
    weekly = subparsers.add_parser("weekly", help="离线汇总过去7天的观测")
    weekly.add_argument("--profile", default=default_profile)
    weekly.add_argument("--observations", default=default_observations)
    weekly.add_argument("--mode", default="all")
    weekly.add_argument("--sources", default="auto")
    weekly.add_argument("--to-date")
    weekly.add_argument("--output", required=True)
