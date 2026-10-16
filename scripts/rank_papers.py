#!/usr/bin/env python3
"""按照研究配置对规范化论文执行透明的初步评分。"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Any

from common import (
    ChineseArgumentParser,
    atomic_write_json,
    clean_text,
    extract_papers,
    integer_range,
    load_json,
    normalize_doi,
    unique_strings,
    utc_now_iso,
)
from profile_config import DEFAULT_PROFILE, ProfileError, load_profile, named_term_groups, topic_groups
from topic_scope import term_matches
from venue_ranking import (
    DEFAULT_RANKING,
    VenueRankingError,
    apply_policy,
    load_ranking,
    quality_score as venue_quality_score,
    summarize_queue,
)
from venue_ranking import classify as classify_venue
from venue_ranking import venue_priority, reading_sort_key, CCF_RANK

WEIGHTS = {
    "relevance": 0.40,
    "novelty": 0.20,
    "quality": 0.15,
    "methodology": 0.15,
    "inspiration": 0.10,
}

# 方法词表由领域配置提供；不把某一学科的实验手段默认用于所有领域。
DEFAULT_METHOD_GROUPS: list[dict[str, Any]] = []
DEFAULT_QUALITY_TIERS = {"tier_1": set(), "tier_2": set(), "tier_3": set()}

EXCLUDE_TYPES = (
    "editorial", "correction", "erratum", "retraction", "conference abstract", "letter to the editor"
)


def contains_any(text: str, terms: list[str] | tuple[str, ...] | set[str]) -> bool:
    return any(term_matches(text, term) for term in terms if clean_text(term))


def normalize_journal(journal: str) -> str:
    return re.sub(r"\s+", " ", journal.casefold().replace("the ", "", 1)).strip(" .")


def method_groups(profile: dict[str, Any]) -> list[dict[str, Any]]:
    custom = named_term_groups(profile, "method_groups")
    labels = {item["label"].casefold() for item in custom}
    return custom + [item for item in DEFAULT_METHOD_GROUPS if item["label"].casefold() not in labels]


def matched_methods(text: str, profile: dict[str, Any]) -> list[str]:
    return [group["label"] for group in method_groups(profile) if contains_any(text, group["terms"])]


def score_relevance(text: str, profile: dict[str, Any]) -> tuple[int, list[str], list[str]]:
    groups = topic_groups(profile)
    labels = [group["label"] for group in groups if contains_any(text, group["terms"])]
    weights = {group["label"]: int(group["weight"]) for group in groups}
    raw = sum(weights[label] for label in labels)
    evidence = [f"匹配主题：{label}" for label in labels]

    for item in profile.get("cross_topic_bonuses") or []:
        if not isinstance(item, dict):
            continue
        required = [clean_text(value) for value in item.get("labels") or [] if clean_text(value)]
        try:
            bonus = int(item.get("bonus") or 0)
        except (TypeError, ValueError):
            continue
        if required and all(label in labels for label in required):
            raw += max(0, min(50, bonus))
            evidence.append(clean_text(item.get("reason")) or f"交叉主题加分：{' + '.join(required)}")
    if len(labels) >= 2:
        raw += min(10, (len(labels) - 1) * 3)
    return min(100, raw), labels, evidence


def score_novelty(text: str, methods: list[str], topics: list[str]) -> tuple[int, list[str]]:
    evidence: list[str] = []
    score = 40
    if methods:
        score += min(30, 7 * len(methods))
        evidence.append(f"识别到 {len(methods)} 类可能具有信息量的方法")
    if len(topics) >= 2:
        score += min(15, 5 * (len(topics) - 1))
        evidence.append("连接多个已配置研究主题")
    if contains_any(text, ("first", "novel", "new mechanism", "previously unknown")):
        score += 5
        evidence.append("摘要提出新颖性主张；仍需原文核实")
    return min(100, score), evidence


def configured_quality_tiers(profile: dict[str, Any]) -> dict[str, set[str]]:
    configured = profile.get("quality_tiers") or {}
    tiers: dict[str, set[str]] = {}
    for tier in ("tier_1", "tier_2", "tier_3"):
        values = {normalize_journal(clean_text(item)) for item in configured.get(tier, []) if clean_text(item)}
        tiers[tier] = values or set(DEFAULT_QUALITY_TIERS[tier])
    return tiers


def configured_tier_score(paper: dict[str, Any], profile: dict[str, Any]) -> int | None:
    """只有命中研究配置里人工维护的期刊分层时才返回分层分，供目录评分回退使用。"""
    journal = normalize_journal(clean_text(paper.get("journal")))
    if not journal:
        return None
    tiers = configured_quality_tiers(profile)
    for tier, score in (("tier_1", 93), ("tier_2", 84), ("tier_3", 74)):
        if journal in tiers[tier]:
            return score
    return None


def score_quality(paper: dict[str, Any], profile: dict[str, Any]) -> tuple[int, list[str]]:
    journal = normalize_journal(clean_text(paper.get("journal")))
    article_type = clean_text(paper.get("article_type")).casefold()
    tiers = configured_quality_tiers(profile)
    evidence: list[str] = []
    if paper.get("is_preprint") or journal in {"biorxiv", "medrxiv"}:
        score = 58
        evidence.append("预印本：尚未完成正式同行评议")
    elif journal in tiers["tier_1"]:
        score = 93
        evidence.append("命中用户配置的一档期刊")
    elif journal in tiers["tier_2"]:
        score = 84
        evidence.append("命中用户配置的二档期刊")
    elif journal in tiers["tier_3"]:
        score = 74
        evidence.append("命中用户配置的三档期刊")
    elif journal:
        score = 65
        evidence.append("期刊未分层，需根据单篇设计人工核实")
    else:
        score = 48
        evidence.append("期刊信息缺失")
    if "review" in article_type:
        score = min(score, 82)
        evidence.append("综述需按证据综合质量而非原始实验完整性评价")
    if paper.get("abstract"):
        score += 3
    else:
        score -= 15
        evidence.append("摘要缺失，无法可靠评价实验完整性")
    return max(0, min(100, score)), evidence


def score_methodology(paper: dict[str, Any], text: str, methods: list[str]) -> tuple[int, list[str]]:
    evidence = [f"识别到方法：{', '.join(methods)}"] if methods else ["未从元数据识别到已配置或通用高信息量方法"]
    score = 30 + min(50, len(methods) * 12)
    if contains_any(text, ("replicate", "time course", "time-series", "multiple sites", "multiple cohorts", "multiple tissues")):
        score += 7
        evidence.append("摘要提示重复、时间序列或多中心/多组织设计")
    if not paper.get("abstract"):
        score = min(score, 40)
    return min(100, score), evidence


def score_inspiration(relevance: int, methods: list[str], topics: list[str]) -> tuple[int, list[str]]:
    score = 28 + round(relevance * 0.50)
    evidence: list[str] = []
    if topics:
        evidence.append("可连接已配置的核心科学问题")
    if methods:
        score += min(18, len(methods) * 4)
        evidence.append("包含可能迁移到后续研究的技术或设计")
    return min(100, score), evidence


def exclusion_reasons(paper: dict[str, Any], text: str, relevance: int, profile: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    article_type = clean_text(paper.get("article_type")).casefold()
    title = clean_text(paper.get("title")).casefold()
    if contains_any(article_type, EXCLUDE_TYPES) or re.match(r"^(?:retraction|retracted|correction|erratum|editorial)\s*:", title):
        reasons.append("非研究性文章、勘误或撤稿类内容")
    if relevance < 35:
        reasons.append("与已配置研究方向关系过弱")
    exclusions = [clean_text(item).casefold() for item in profile.get("exclusion_terms") or []]
    matched = [term for term in exclusions if term and term in text]
    if matched:
        reasons.append("命中配置的排除词：" + ", ".join(matched[:5]))
    if not paper.get("title"):
        reasons.append("题名缺失")
    return reasons


def strong_recommendation(
    topics: list[str], methods: list[str], score: int, profile: dict[str, Any]
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    for item in profile.get("strong_recommendation_rules") or []:
        if not isinstance(item, dict):
            continue
        required_topics = [clean_text(value) for value in item.get("labels") or [] if clean_text(value)]
        required_methods = [clean_text(value) for value in item.get("methods") or [] if clean_text(value)]
        try:
            minimum = int(item.get("min_score") or 80)
        except (TypeError, ValueError):
            minimum = 80
        if (
            score >= minimum
            and required_topics
            and all(label in topics for label in required_topics)
            and all(label in methods for label in required_methods)
        ):
            reasons.append(clean_text(item.get("reason")) or "满足配置的强烈推荐条件")
    return bool(reasons), reasons


def rank_one(paper: dict[str, Any], profile: dict[str, Any], ranking: dict[str, Any] | None = None) -> dict[str, Any]:
    from topic_scope import assess_scope
    scope_match = assess_scope(paper, profile.get("topic_scope"))
    text = f" {paper.get('title', '')} {paper.get('abstract', '')} ".casefold()
    relevance, topics, relevance_evidence = score_relevance(text, profile)
    methods = matched_methods(text, profile)
    novelty, novelty_evidence = score_novelty(text, methods, topics)
    classification = classify_venue(paper, ranking) if ranking else None
    tier = configured_tier_score(paper, profile)
    if classification is not None:
        quality, quality_evidence = venue_quality_score(classification, paper, ranking, tier)
    else:
        quality, quality_evidence = score_quality(paper, profile)
    methodology, methodology_evidence = score_methodology(paper, text, methods)
    inspiration, inspiration_evidence = score_inspiration(relevance, methods, topics)
    component_scores = {
        "relevance": relevance,
        "novelty": novelty,
        "quality": quality,
        "methodology": methodology,
        "inspiration": inspiration,
    }
    score = round(sum(component_scores[key] * WEIGHTS[key] for key in WEIGHTS))
    reasons = exclusion_reasons(paper, text, relevance, profile)
    if not scope_match["in_scope"]:
        reasons.append("不符合核心主题；缺少：" + "、".join(scope_match["missing_groups"]))
    if classification is not None:
        reasons.extend(apply_policy(paper, classification, relevance, ranking, has_configured_tier=tier is not None))
    if not paper.get("abstract"):
        score = min(score, 72)
    strong, strong_reasons = strong_recommendation(topics, methods, score, profile)
    if (
        strong
        and classification is not None
        and (ranking.get("policy") or {}).get("strong_recommendation_requires_verified_venue")
        and not classification.get("verified_venue")
    ):
        strong = False
        strong_reasons = [
            "强烈推荐条件已满足，但发表场所等级未经核实；核实 CCF 或期刊分层后再升级为强烈推荐"
        ]
    ranked = dict(paper)
    ranked["doi"] = normalize_doi(ranked.get("doi"))
    if classification is not None:
        ranked["needs_verification"] = unique_strings(
            (ranked.get("needs_verification") or []) + list(classification.get("needs_verification") or [])
        )
    ranked.update(
        {
            "recommendation_score": score,
            "score_status": "preliminary_metadata_score",
            "score_limitations": ["创新性尚未与已有工作比较", "方法关键词缺失不等于方法差", "场所等级不等于单篇论文质量"],
            "component_scores": component_scores,
            "score_weights": WEIGHTS,
            "matched_topics": topics,
            "matched_methods": methods,
            "venue_ranking": classification
            if classification is not None
            else {"status": "unknown", "label": "等级目录未加载", "ccf": None, "needs_verification": []},
            "score_evidence": {
                "relevance": relevance_evidence,
                "novelty": novelty_evidence,
                "quality": quality_evidence,
                "methodology": methodology_evidence,
                "inspiration": inspiration_evidence,
            },
            "excluded": bool(reasons),
            "exclusion_reasons": reasons,
            "topic_scope_match": scope_match,
            "configured_journal_tier": next((name for name, value in (("tier_1", 93), ("tier_2", 84), ("tier_3", 74))
                                             if value == tier and not paper.get("is_preprint")), None),
            "strong_recommendation_candidate": strong and not reasons,
            "strong_recommendation_reasons": strong_reasons,
        }
    )
    return ranked



def select_review_candidates(ranked: list[dict[str, Any]], limit: int, *, reading_order: str = "score") -> list[dict[str, Any]]:
    """按日期优先，以主题轮转保证小主题有复核机会；不使用总分硬截断。"""
    eligible = [p for p in ranked if not p["excluded"]]
    if reading_order == "venue_first":
        # 先按场所分层，再在每层内轮转主题，低等级不能因轮转抢占 A 类位置。
        selected = []
        for priority in sorted({venue_priority(p) for p in eligible}, reverse=True):
            band = [p for p in eligible if venue_priority(p) == priority]
            # 待核实录用只影响“先核实谁”，不提升最终阅读等级。
            band.sort(key=lambda p: (CCF_RANK.get((p.get("venue_ranking") or {}).get("candidate_ccf"), 0),
                                     reading_sort_key(p)), reverse=True)
            selected += _round_robin_candidates(band, limit - len(selected))
            if len(selected) >= limit:
                break
        return selected
    eligible.sort(key=lambda p: (p.get("publication_date", ""), p.get("component_scores", {}).get("relevance", 0), p["recommendation_score"]), reverse=True)
    return _round_robin_candidates(eligible, limit)


def _round_robin_candidates(eligible: list[dict], limit: int) -> list[dict]:
    buckets: dict[str, list[dict]] = {}
    for p in eligible:
        labels = p.get("matched_topics") or ["其他"]
        for label in labels:
            buckets.setdefault(label, []).append(p)
    selected, seen = [], set()
    while buckets and len(selected) < limit:
        for label in list(buckets):
            bucket = buckets[label]
            while bucket and id(bucket[0]) in seen:
                bucket.pop(0)
            if not bucket:
                del buckets[label]
                continue
            p = bucket.pop(0)
            selected.append(p)
            seen.add(id(p))
            if len(selected) >= limit:
                break
    return selected

def rank_payload(
    payload: Any,
    profile: dict[str, Any],
    *,
    ranking: dict[str, Any] | None = None,
    threshold: int = 70,
    maximum: int = 10,
    candidate_limit: int = 100,
) -> dict[str, Any]:
    papers = extract_papers(payload)
    ranked = [rank_one(paper, profile, ranking) for paper in papers]
    order = profile.get("reading_order", "score")
    ranked.sort(key=reading_sort_key if order == "venue_first" else lambda item: item["recommendation_score"], reverse=True)
    ranked.sort(key=lambda item: item["excluded"])
    recommendations = [
        item for item in ranked if not item["excluded"] and item["recommendation_score"] >= threshold
    ][:maximum]
    return {
        "generated_at": utc_now_iso(),
        "profile_name": clean_text(profile.get("profile_name")),
        "threshold": threshold,
        "maximum": maximum,
        "screened_count": len(ranked),
        "recommended_count": len(recommendations),
        "formula": "relevance*0.40 + novelty*0.20 + quality*0.15 + methodology*0.15 + inspiration*0.10",
        "important_note": "分数仅是元数据代理指标，不是创新性、重要性或热度的证据；低于70分仍可进入广泛复核池。",
        "authority_policy": (ranking.get("policy") or {}) if ranking else {},
        "venue_distribution": summarize_queue(recommendations, ranking) if ranking else {},
        "recommendations": recommendations,
        "reading_order": order,
        "review_candidates": select_review_candidates(ranked, candidate_limit, reading_order=order),
        "ranked_papers": ranked,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = ChineseArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--ranking", default=str(DEFAULT_RANKING))
    parser.add_argument(
        "--threshold",
        type=integer_range(0, 100, "分"),
        default=70,
        metavar="0-100",
        help="初筛推荐阈值；默认为70分",
    )
    parser.add_argument(
        "--max-results",
        type=integer_range(1, 50, "篇"),
        default=10,
        metavar="1-50",
        help="最多保留的候选数；默认为10篇",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        profile = load_profile(args.profile)
    except ProfileError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        ranking = load_ranking(args.ranking)
    except VenueRankingError as exc:
        # 等级目录缺失时评分仍可运行，但必须让使用者看到退化提示，不能假装已完成等级标注。
        ranking = None
        print(f"警告：发表场所等级目录不可用，本次只按配置分层评分：{exc}", file=sys.stderr)
    result = rank_payload(
        load_json(args.input, {}),
        profile,
        ranking=ranking,
        threshold=args.threshold,
        maximum=args.max_results,
    )
    atomic_write_json(args.output, result)
    print(
        f"共初筛 {result['screened_count']} 篇论文；"
        f"其中 {result['recommended_count']} 篇达到初步阈值。"
    )
    distribution = (result.get("venue_distribution") or {}).get("counts") or {}
    if distribution:
        print(
            "候选等级分布："
            + "、".join(f"{key} {value} 篇" for key, value in distribution.items() if value)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
