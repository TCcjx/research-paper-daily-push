#!/usr/bin/env python3
"""编排每日科研文献雷达的检索与交付流程。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from common import (
    ChineseArgumentParser,
    atomic_write_json,
    integer_range,
    load_json,
    utc_now_iso,
)
from profile_config import DEFAULT_PROFILE, ProfileError, load_profile, profile_queries, profile_terms, profile_sources
from search_papers import collect
from deduplicate import filter_history, load_history, history_reference
from rank_papers import rank_payload, select_review_candidates
from venue_ranking import load_ranking, DEFAULT_RANKING, classify, apply_venue_importance, summarize_queue
from research_radar import analyze
from common import paper_key, deduplicate_papers, workflow_lock, normalize_doi, normalize_title
from watch_papers import check_watchlist
from project_cli import add_commands, init_profile, prepare_review, weekly_report
from update_research_memory import build_context, load_memory
from topic_scope import require_scope, assess_scope
from publication_policy import partition_publications, validate_agent_assessment

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_HISTORY = SKILL_DIR / "data" / "pushed_articles.json"
DEFAULT_MEMORY = SKILL_DIR / "data" / "research_memory.json"
DEFAULT_RUNS = SKILL_DIR / "data" / "runs"


def run_script(name: str, *arguments: str) -> None:
    command = [sys.executable, str(SCRIPT_DIR / name), *arguments]
    subprocess.run(command, check=True)


def collect_stage(args: argparse.Namespace) -> Path:
    today = args.to_date or datetime.now().astimezone().date().isoformat()
    end = datetime.fromisoformat(today).date()
    profile = load_profile(args.profile)
    sources = profile_sources(profile, args.sources)
    queries = profile_queries(profile, args.mode)
    from research_radar import scope_key
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_RUNS / scope_key(profile, queries, sources) / today
    output_dir.mkdir(parents=True, exist_ok=True)
    queue_path = output_dir / "review_queue.json"
    try:
        # 一次取30天样本，再本地切窗。避免每次扩窗重复请求同一批论文。
        raw = collect(queries=queries, start=end - timedelta(days=29), end=end,
                      sources=sources, relevance_terms=profile_terms(profile), topic_scope=profile.get("topic_scope"))
    except Exception as exc:
        atomic_write_json(queue_path, {"status": "failed", "date": today,
                                      "error": str(exc), "candidates": []})
        (output_dir / "discovery.md").write_text(f"# 本次检索失败\n\n日期：{today}\n\n{exc}\n\n没有生成成功的零推荐报告，请排查后重试。\n", encoding="utf-8")
        raise
    raw["profile_name"] = profile.get("profile_name", "")
    atomic_write_json(output_dir / "search_30d.json", raw)
    ranking = load_ranking(args.ranking)
    history = load_history(args.history)
    reference = history_reference(history, load_memory(args.memory), profile.get("deduplication_scope", "all_history"))
    watched, watch_errors = check_watchlist((profile.get("watchlist") or []) + list(reversed(history)), args.watch_limit, sources=sources)
    watched = [p for p in watched if assess_scope(p, profile.get("topic_scope"))["in_scope"]]
    fresh = filter_history(raw["papers"], reference)
    watched_updates = filter_history(watched, history)["updates"]
    fresh["updates"] = deduplicate_papers(fresh["updates"] + watched_updates)
    observation_raw = {**raw, "papers": deduplicate_papers(raw["papers"] + [{**p, "_watch_only": True} for p in watched])}
    radar, state = analyze(observation_raw, profile, load_json(args.observations, {}), today=today)
    all_ranked = rank_payload({"papers": fresh["new"]}, profile, ranking=ranking,
                              threshold=args.threshold, maximum=args.max_results,
                              candidate_limit=args.candidate_limit)
    eligible = [p for p in all_ranked["ranked_papers"] if not p["excluded"] and (p.get("publication_decision") or {}).get("status") != "excluded"]
    recent_start = (end - timedelta(days=1)).isoformat()
    recent = [p for p in eligible if recent_start <= p.get("publication_date", "") <= today]
    used_days = 2
    pool = recent
    for days in (7, 14, 30):
        if len(pool) >= args.min_results:
            break
        used_days = days
        cutoff = (end - timedelta(days=days-1)).isoformat()
        pool = [p for p in eligible if cutoff <= p.get("publication_date", "") <= today]
    reading_order = profile.get("reading_order", "score")
    if reading_order in {"venue_first", "agent_first", "latest_first"} or not args.candidate_limit:
        # 场所优先时查看完整30天，避免当日预印本占满候选而漏掉近期 A 类论文。
        used_days, pool = 30, eligible
        candidates = select_review_candidates(pool, args.candidate_limit, reading_order=reading_order)
    else:
        candidates = select_review_candidates(recent, args.candidate_limit)
        recent_keys = {paper_key(p) for p in candidates}
        candidates += select_review_candidates([p for p in pool if paper_key(p) not in recent_keys],
                                               args.candidate_limit - len(candidates))
    for p in candidates:
        p["reading_lane"] = "daily" if p.get("publication_date", "") >= recent_start else "supplementary"
        p["attention_signals"] = radar["paper_signals"].get(paper_key(p), {})
    warnings = [f"{e['source']}: {e['error']}" for e in raw.get("errors", [])]
    warnings += raw.get("coverage_warnings", []) + watch_errors
    if len(eligible) > len(candidates):
        warnings.append(f"30天内有 {len(eligible)} 篇主题内可复核论文，本次窗口与候选上限内展示 {len(candidates)} 篇；其余见 ranked_30d.json")
    used_window = ("近24小时优先（按日级日期，候选池覆盖今天和昨天）" if used_days == 2
                   else f"最近{used_days}天（相关候选不足{args.min_results}篇，近期补充单独标注）")
    if reading_order in {"venue_first", "agent_first", "latest_first"} or not args.candidate_limit:
        used_window = "最近30天（同主题内场所等级优先，当日与近期补充按实际日期标注）"
    if reading_order in {"agent_first", "latest_first"}:
        stages = "、再".join(g["label"] for g in (profile.get("topic_scope") or {}).get("required_groups", []))
        used_window = f"最近30天（先{stages or '满足研究主题'}，最后优先Agent结合；不按篇数补足）"
    if reading_order == "latest_first":
        used_window = "最近30天（最新发表优先，今天/昨天按日级日期近似，较早论文标为近期补充；同日Agent结合优先）"
    queue = {
        "status": raw.get("status", "ok"), "generated_at": utc_now_iso(), "date": today,
        "mode": args.mode, "profile_name": profile.get("profile_name", ""),
        "topic_scope": profile.get("topic_scope", {}),
        "publication_filter": profile.get("publication_filter", {}),
        "metadata": {"window": used_window, "trend_window": f"{raw['date_from']} 至 {today}",
                     "retrieved_count": raw["retrieved_count"], "unique_relevant_count": raw["unique_relevant_count"],
                     "off_topic_count": raw.get("off_topic_count", 0),
                     "topic_scope_name": (profile.get("topic_scope") or {}).get("name", ""),
                     "disabled_sources": profile.get("disabled_sources", []),
                     "reading_order": reading_order,
                     "scope_stage_counts": raw.get("scope_stage_counts", []),
                     "deduplication_scope": profile.get("deduplication_scope", "all_history"),
                     "dedup_reference_count": len(reference),
                     "new_after_history_count": fresh["new_count"],
                     "duplicate_count": fresh["duplicate_count"],
                     "screened_count": all_ranked["screened_count"],
                     "preliminary_recommended_count": all_ranked["recommended_count"],
                     "candidate_count": len(candidates), "threshold": args.threshold,
                     "maximum": args.max_results or None, "source_counts": raw["source_counts"],
                     "sources_requested": sources, "warnings": warnings,
                     "authority_policy": all_ranked["authority_policy"]},
        "review_requirement": "这些是广泛复核候选，不是最终推送结果。阅读摘要或全文，记录证据链接及与已有工作的比较；重要性与热度分开判断。",
        "research_memory_context": build_context(load_memory(args.memory), recent=10),
        "radar": radar, "candidates": candidates,
        "pending_verification": all_ranked.get("pending_verification", []),
        "publication_exclusions": all_ranked.get("publication_exclusions", []),
        "duplicates_not_recommended": fresh["duplicates"],
        "updates_not_for_new_push": fresh["updates"],
    }
    atomic_write_json(output_dir / "ranked_30d.json", all_ranked)
    atomic_write_json(queue_path, queue)
    atomic_write_json(args.observations, state)
    # 同时输出可直接阅读的简报；它不是经过证据复核的精选推荐。
    from summarize_papers import render_discovery
    (output_dir / "discovery.md").write_text(render_discovery(queue), encoding="utf-8")
    print(f"复核队列已生成：{queue_path}")
    print(f"检索窗口：{used_window}；候选论文：{len(candidates)} 篇；未复核简报：{output_dir / 'discovery.md'}")
    return queue_path


def deliver_stage(args: argparse.Namespace) -> Path:
    reviewed_path = Path(args.reviewed)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    reviewed_payload = load_json(reviewed_path, {})
    if not isinstance(reviewed_payload, dict):
        raise ValueError("已复核报告必须是 JSON 对象")
    try:
        report_year = datetime.fromisoformat(reviewed_payload.get("date", "")).year
    except (TypeError, ValueError) as exc:
        raise ValueError("已复核报告date必须是有效日期") from exc
    active_profile = load_profile(args.profile, require_configured=False)
    profile_scope = active_profile.get("topic_scope")
    for paper in reviewed_payload.get("articles", []):
        require_scope(paper, profile_scope)
    if profile_scope:
        reviewed_payload["topic_scope"] = profile_scope
    strict_publication = (active_profile.get("publication_filter") or {}).get("enabled", False)
    queue = {}
    if args.queue:
        queue = load_json(args.queue, {})
        if queue.get("status") not in {"ok", "partial"} or queue.get("date") != reviewed_payload.get("date"):
            raise ValueError("复核队列必须检索成功，且日期与已复核报告一致")
        allowed = queue.get("candidates", [])
        allowed_dois = {normalize_doi(p.get("doi")) for p in allowed if p.get("doi")}
        allowed_titles = {normalize_title(p.get("title")) for p in allowed}
        for p in reviewed_payload.get("articles", []):
            if normalize_doi(p.get("doi")) not in allowed_dois and normalize_title(p.get("title")) not in allowed_titles:
                raise ValueError("推荐论文不在对应复核队列中，请先将补充检索的论文纳入队列")
            require_scope(p, queue.get("topic_scope"))
            candidate = next(c for c in allowed if
                             (normalize_doi(p.get("doi")) and normalize_doi(p.get("doi")) == normalize_doi(c.get("doi")))
                             or normalize_title(p.get("title")) == normalize_title(c.get("title")))
            # 复核文字不能把原本偏题的论文重新包装为核心主题。
            original = {**candidate, "abstract": candidate.get("abstract") or p.get("abstract", "")}
            require_scope(original, profile_scope)
            require_scope(original, queue.get("topic_scope"))
            cutoff = (datetime.fromisoformat(queue["date"]).date() - timedelta(days=1)).isoformat()
            p["reading_lane"] = "daily" if p.get("publication_date", "") >= cutoff else "supplementary"
            p["attention_signals"] = (queue.get("radar") or {}).get("paper_signals", {}).get(paper_key(p), {})
        reviewed_payload["radar"] = queue.get("radar", {})
        if queue.get("topic_scope"):
            reviewed_payload["topic_scope"] = queue["topic_scope"]
        reviewed_payload["updates"] = queue.get("updates_not_for_new_push", []) + (queue.get("radar") or {}).get("observed_updates", [])
        reviewed_payload["metadata"] = {**reviewed_payload.get("metadata", {}), **queue.get("metadata", {})}
        if not active_profile.get("publication_filter") and queue.get("publication_filter"):
            active_profile["publication_filter"] = queue["publication_filter"]
            strict_publication = bool(queue["publication_filter"].get("enabled"))
        reviewed_path = report_path.with_suffix(".reviewed.json")
    elif profile_scope:
        reviewed_path = report_path.with_suffix(".reviewed.json")
    # 当前配置优先，旧队列不能覆盖已经更新的阅读偏好。
    reading_order = active_profile.get("reading_order", (reviewed_payload.get("metadata") or {}).get("reading_order", "score"))
    reviewed_payload.setdefault("metadata", {})["reading_order"] = reading_order
    if not strict_publication and (not args.skip_push or args.record_local_report):
        check = filter_history(reviewed_payload.get("articles", []), load_history(args.history))
        if check["duplicates"] or check["updates"]:
            raise ValueError("推荐列表包含已交付论文，请移到 updates 栏目；仅重新生成报告可用 --skip-push")
    if strict_publication:
        ranking = load_ranking(args.ranking)
        policy = active_profile["publication_filter"]
        grouped = partition_publications(reviewed_payload.get("articles", []), policy, ranking,
                                         year=report_year)
        reviewed_payload["publication_filter"] = policy
        for field, group in (("publication_exclusions", "excluded"), ("pending_verification", "pending")):
            # 原待核实候选已有本轮复核结果时，用新结果替换；其余保留完整审计轨迹。
            inherited = (reviewed_payload.get(field) or []) + (queue.get(field) or [])
            inherited = filter_history(inherited, reviewed_payload.get("articles", []))["new"]
            reviewed_payload[field] = deduplicate_papers(grouped[group] + inherited)
        reference = history_reference(load_history(args.history), load_memory(args.memory),
                                      active_profile.get("deduplication_scope", "all_history"))
        deduped = filter_history(grouped["eligible"], reference)
        inherited_duplicates = (reviewed_payload.get("duplicates_not_recommended") or []) + (queue.get("duplicates_not_recommended") or [])
        inherited_duplicates = filter_history(inherited_duplicates, reference)["duplicates"]
        reviewed_payload["duplicates_not_recommended"] = deduplicate_papers(deduped["duplicates"] + inherited_duplicates)
        reviewed_payload["updates"] = reviewed_payload.get("updates", []) + deduped["updates"]
        reviewed_payload["articles"] = deduped["new"]
        reviewed_payload["metadata"].update(maximum=None, pending_verification_count=len(reviewed_payload["pending_verification"]),
                                            publication_exclusion_count=len(reviewed_payload["publication_exclusions"]),
                                            duplicate_count=len(reviewed_payload["duplicates_not_recommended"]),
                                            deduplication_scope=active_profile.get("deduplication_scope", "all_history"))
        for paper in reviewed_payload["articles"]:
            paper["venue_ranking"] = paper["publication_decision"]["classification"]
            if reading_order in {"agent_first", "latest_first"}:
                validate_agent_assessment(paper)
        reviewed_path = report_path.with_suffix(".reviewed.json")
    if reading_order == "venue_first":
        ranking = load_ranking(args.ranking)
        from rank_papers import configured_tier_score
        for paper in reviewed_payload.get("articles", []):
            # 重新匹配实际发表场所，不沿用可编辑 JSON 中手填的 A 类标签。
            paper["venue_ranking"] = (paper.get("publication_decision") or {}).get("classification") or classify(paper, ranking)
            tier_score = configured_tier_score(paper, active_profile)
            paper["configured_journal_tier"] = next((name for name, value in (("tier_1", 93), ("tier_2", 84), ("tier_3", 74))
                                                     if value == tier_score and not paper.get("is_preprint")), None)
            apply_venue_importance(paper)
        reviewed_payload["metadata"]["venue_distribution"] = summarize_queue(reviewed_payload.get("articles", []), ranking)
        reviewed_path = report_path.with_suffix(".reviewed.json")
    if args.queue or profile_scope or reading_order in {"venue_first", "agent_first", "latest_first"} or strict_publication:
        atomic_write_json(reviewed_path, reviewed_payload)
    run_script("summarize_papers.py", "--input", str(reviewed_path), "--output", str(report_path), "--ranking", str(args.ranking))

    delivery = "local-report"
    should_commit = bool(args.record_local_report)
    if not args.skip_push:
        status_path = report_path.with_suffix(".delivery.json")
        run_script(
            "push_wechat.py",
            "--report", str(report_path),
            "--provider", args.provider,
            "--result-json", str(status_path),
        )
        status = load_json(status_path, {})
        if status.get("configured") and status.get("success"):
            delivery = str(status.get("provider"))
            should_commit = True
        elif not status.get("configured"):
            print("微信推送尚未配置。报告已正常生成；在当前对话展示报告后，可按 local-report 提交历史。")

    if should_commit:
        run_script(
            "deduplicate.py",
            "commit",
            "--input", str(reviewed_path),
            "--history", str(args.history),
            "--delivery", delivery,
        )
        try:
            run_script(
                "update_research_memory.py",
                "learn",
                "--input", str(reviewed_path),
                "--memory", str(args.memory),
                "--profile", str(args.profile),
                "--delivery", delivery,
            )
        except subprocess.CalledProcessError as exc:
            print(
                f"警告：报告交付和历史提交已成功，但研究记忆更新失败：{exc}",
                file=sys.stderr,
            )
    else:
        print("未确认成功交付，因此没有写入推送历史。")
    print(f"报告已生成：{report_path}")
    return report_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = ChineseArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_commands(subparsers, str(DEFAULT_PROFILE), str(SKILL_DIR / "data" / "observations.json"))

    collect_parser = subparsers.add_parser("collect", help="检索、去重、评分并生成复核队列")
    collect_parser.add_argument("--mode", default="all", help="研究配置中定义的模式名称")
    collect_parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    # 来源由领域预设或显式列表决定，失败情况会写入报告。
    collect_parser.add_argument("--sources", default="auto", help="auto 按研究配置选择来源，或逗号分隔的来源列表")
    collect_parser.add_argument("--watch-limit", type=integer_range(0, 50), default=20, help="跟踪明确关注及最近交付的论文数，0关闭")
    collect_parser.add_argument("--ranking", default=str(DEFAULT_RANKING))
    collect_parser.add_argument("--observations", default=str(SKILL_DIR / "data" / "observations.json"))
    collect_parser.add_argument("--to-date", help="报告日期 YYYY-MM-DD，默认为本地今天")
    collect_parser.add_argument("--candidate-limit", type=integer_range(0, 1000000), default=0, help="默认0不截断复核池；非零为用户显式上限")
    collect_parser.add_argument("--history", default=str(DEFAULT_HISTORY))
    collect_parser.add_argument("--memory", default=str(DEFAULT_MEMORY))
    collect_parser.add_argument("--output-dir")
    collect_parser.add_argument(
        "--threshold",
        type=integer_range(0, 100, "分"),
        default=70,
        metavar="0-100",
        help="元数据代理分统计阈值，不作为复核池硬门槛；默认为70分",
    )
    collect_parser.add_argument(
        "--min-results",
        type=integer_range(1, 50, "篇"),
        default=1,
        metavar="1-50",
        help="兼容旧窗口策略；不作为最终推荐数量目标",
    )
    collect_parser.add_argument(
        "--max-results",
        type=integer_range(0, 1000000, "篇"),
        default=0,
        metavar="N",
        help="默认0不限制推荐数；启用严格场所门槛时交付不按篇数截断",
    )

    deliver_parser = subparsers.add_parser("deliver", help="生成已复核 JSON 报告，并可选择推送")
    deliver_parser.add_argument("--reviewed", required=True)
    deliver_parser.add_argument("--queue", help="对应的 review_queue.json，用于附加可追溯热点与更新栏目")
    deliver_parser.add_argument("--report", required=True)
    deliver_parser.add_argument("--history", default=str(DEFAULT_HISTORY))
    deliver_parser.add_argument("--memory", default=str(DEFAULT_MEMORY))
    deliver_parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    deliver_parser.add_argument("--ranking", default=str(DEFAULT_RANKING))
    deliver_parser.add_argument("--provider", choices=("auto", "wxpusher", "serverchan", "wecom"), default="auto")
    deliver_parser.add_argument("--skip-push", action="store_true")
    deliver_parser.add_argument("--record-local-report", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "init-profile":
        init_profile(args)
    elif args.command == "prepare":
        prepare_review(args)
    elif args.command == "weekly":
        weekly_report(args)
    elif args.command == "collect":
        # 先校验参数本身，再校验研究配置，避免配置缺失掩盖明显的用法错误。
        if args.candidate_limit and args.min_results > args.candidate_limit:
            raise SystemExit("参数必须满足 --min-results <= --candidate-limit；最终推荐数独立设置")
        try:
            load_profile(args.profile)
        except ProfileError as exc:
            raise SystemExit(str(exc)) from exc
        with workflow_lock(Path(args.observations).with_suffix(".lock")):
            collect_stage(args)
    else:
        with workflow_lock(Path(args.history).with_suffix(".lock")):
            deliver_stage(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(f"运行失败：{exc}") from exc
