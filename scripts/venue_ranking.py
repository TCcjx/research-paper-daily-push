#!/usr/bin/env python3
"""按 CCF 官方目录标注论文发表场所的等级，并给出权威性判定。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from common import PREPRINT_JOURNAL_MARKERS, clean_text

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_RANKING = SCRIPT_DIR.parent / "config" / "venue_ranking.json"

PREPRINT_MARKERS = PREPRINT_JOURNAL_MARKERS
PUBLICATION_KEYWORDS = (
    "accepted", "to appear", "appear", "publish", "published", "presented",
    "proceedings", "camera-ready", "cameraready", "appearing",
)

STOP_WORDS = {
    "the", "of", "and", "on", "in", "for", "international", "conference",
    "journal", "proceedings", "workshop", "symposium",
}

# 同名简称也会出现在附属卷里（如 Findings of EMNLP），这些卷不等同于 CCF 目录中的主会。
COMPANION_MARKERS = (
    "findings", "workshop", "companion", "tutorial", "demo",
    "short paper", "doctoral", "student research", "tracker",
)

# 简称同时命中期刊与会议条目时无法自动区分，按较低等级标注，避免虚高。
CCF_RANK = {"": 0, "C": 1, "B": 2, "A": 3}


class VenueRankingError(ValueError):
    """等级目录缺失或格式无效时抛出。"""


def normalize_venue(value: Any) -> str:
    text = clean_text(value).casefold().replace("&", " and ")
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"^the ", "", text)


def _key_tokens(value: str) -> frozenset[str]:
    return frozenset(word for word in value.split() if word and word not in STOP_WORDS)


def load_ranking(path: str | Path = DEFAULT_RANKING) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise VenueRankingError(f"发表场所等级目录不存在：{target}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VenueRankingError(f"发表场所等级目录不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("venues"), list):
        raise VenueRankingError("发表场所等级目录必须包含 venues 数组")
    payload.setdefault("catalog", {})
    payload.setdefault("scoring", {})
    payload.setdefault("policy", {})
    payload.setdefault("labels", {})
    payload.setdefault("conflicts", {})
    payload["index"] = build_index(payload["venues"])
    derived = build_conflicts(payload["venues"])
    for alias, blockers in (payload.get("conflicts") or {}).items():
        key = normalize_venue(clean_text(alias))
        values = [normalize_venue(clean_text(item)) for item in (blockers or []) if clean_text(item)]
        derived.setdefault(key, []).extend(values)
    payload["index"]["conflict"] = derived
    return payload


def build_conflicts(venues: list[dict[str, Any]]) -> dict[str, list[str]]:
    """由目录自身的名称与别名生成简称冲突表。

    某个简称同时是另一个场所名称中的词时，不允许再用它跨场所匹配：例如
    “Proceedings of the AAAI/ACM Conference on AI, Ethics, and Society” 里的
    AAAI 并不是 AAAI 主会，命中简称会给出不存在的 CCF-A。
    """
    owners: dict[str, list[str]] = {}
    for entry in venues:
        if not isinstance(entry, dict):
            continue
        names = [clean_text(entry.get("name"))]
        names.extend(clean_text(alias) for alias in entry.get("aliases") or [])
        for name in names:
            normalized = normalize_venue(name)
            if not normalized:
                continue
            for token in normalized.split():
                if len(token) >= 3 and token.isascii():
                    owners.setdefault(token, []).append(normalized)
    conflicts: dict[str, list[str]] = {}
    for entry in venues:
        if not isinstance(entry, dict):
            continue
        alias = normalize_venue(clean_text(entry.get("abbr")))
        if len(alias) < 3:
            continue
        own = {normalize_venue(clean_text(entry.get("name")))}
        own.update(normalize_venue(clean_text(alias_item)) for alias_item in entry.get("aliases") or [])
        blockers = sorted({owner for owner in owners.get(alias, []) if owner not in own})
        if blockers:
            conflicts[alias] = blockers
    return conflicts


def build_index(venues: list[dict[str, Any]]) -> dict[str, Any]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    by_abbr: dict[str, list[dict[str, Any]]] = {}
    for entry in venues:
        if not isinstance(entry, dict):
            continue
        names = [clean_text(entry.get("name"))]
        names.extend(clean_text(alias) for alias in entry.get("aliases") or [])
        for name in names:
            if name:
                by_name.setdefault(normalize_venue(name), []).append(entry)
        abbr = clean_text(entry.get("abbr"))
        if abbr:
            by_abbr.setdefault(normalize_venue(abbr), []).append(entry)
    return {"name": by_name, "abbr": by_abbr, "token_set": {key: _key_tokens(key) for key in by_name}}


def _label(ranking: dict[str, Any], key: str, **kwargs: Any) -> str:
    template = clean_text((ranking.get("labels") or {}).get(key)) or key
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        return template


def _narrow(candidates: list[dict[str, Any]], kind: str | None) -> list[dict[str, Any]]:
    if not kind or len(candidates) == 1:
        return candidates
    hits = [item for item in candidates if clean_text(item.get("kind")) == kind]
    return hits or candidates


def _pick(candidates: list[dict[str, Any]], kind: str | None) -> tuple[dict[str, Any], bool]:
    """在同名条目中选出目录项，返回（条目，是否存在等级歧义）。"""
    pool = _narrow(candidates, kind)
    if len(pool) == 1:
        return pool[0], False
    distinct = {(clean_text(item.get("ccf")), clean_text(item.get("kind"))) for item in pool}
    if len(distinct) == 1:
        return pool[0], False
    ordered = sorted(pool, key=lambda item: CCF_RANK.get(clean_text(item.get("ccf")), 0))
    return ordered[0], True


def _match_name(
    ranking: dict[str, Any], text: str, kind: str | None
) -> tuple[dict[str, Any], str, bool] | None:
    index = ranking["index"]
    normalized = normalize_venue(text)
    if not normalized:
        return None
    candidates = index["name"].get(normalized)
    if candidates:
        entry, ambiguous = _pick(candidates, kind)
        return entry, "name", ambiguous
    tokens = _key_tokens(normalized)
    if len(tokens) >= 3:
        best: tuple[float, dict[str, Any], bool] | None = None
        for key, entry_list in index["name"].items():
            key_tokens = index["token_set"][key]
            if not key_tokens or len(key_tokens) < 3:
                continue
            overlap = len(tokens & key_tokens) / len(key_tokens)
            if overlap >= 0.8:
                entry, ambiguous = _pick(entry_list, kind)
                if best is None or overlap > best[0]:
                    best = (overlap, entry, ambiguous)
        if best:
            return best[1], "name", best[2]
    for alias, entry_list in index["abbr"].items():
        if len(alias) < 3:
            continue
        if not re.search(rf"(?:^|[^a-z0-9]){re.escape(alias)}(?:[^a-z0-9]|$)", normalized):
            continue
        if any(blocker and blocker in normalized for blocker in index.get("conflict", {}).get(alias, [])):
            return None
        entry, ambiguous = _pick(entry_list, kind)
        return entry, "abbreviation", ambiguous
    return None


def _expected_kind(paper: dict[str, Any]) -> str | None:
    article_type = clean_text(paper.get("article_type")).casefold()
    if paper.get("is_preprint"):
        return None
    if "journal" in article_type or "article" in article_type:
        return "journal"
    if "conference" in article_type or "proceedings" in article_type:
        return "conference"
    return None


def classify(paper: dict[str, Any], ranking: dict[str, Any]) -> dict[str, Any]:
    """给出一篇论文的发表场所等级；无法确认时只标记“需要核实”，不猜测等级。"""
    catalog = ranking.get("catalog") or {}
    result: dict[str, Any] = {
        "status": "missing",
        "ccf": None,
        "kind": None,
        "domain": None,
        "abbr": None,
        "venue": "",
        "matched_on": None,
        "match_method": None,
        "author_statement": None,
        "catalog_version": clean_text(catalog.get("version")),
        "label": "",
        "needs_verification": [],
        "verified_venue": False,
        "ambiguous": False,
    }
    journal = clean_text(paper.get("journal"))
    is_preprint = bool(paper.get("is_preprint")) or any(
        marker in journal.casefold() for marker in PREPRINT_MARKERS
    )
    kind = _expected_kind(paper)
    statement = clean_text(first_statement(paper))
    result["author_statement"] = statement or None
    combined = normalize_venue(f"{journal} {statement}")
    companion = next((marker for marker in COMPANION_MARKERS if marker in combined), None)

    def apply_hit(hit: tuple[dict[str, Any], str, bool], matched_on: str) -> None:
        entry, method, ambiguous = hit
        result.update(
            {
                "ccf": clean_text(entry.get("ccf")) or None,
                "kind": clean_text(entry.get("kind")) or None,
                "domain": clean_text(entry.get("domain")) or None,
                "abbr": clean_text(entry.get("abbr")) or None,
                "venue": clean_text(entry.get("name")),
                "matched_on": matched_on,
                "match_method": method,
                "ambiguous": ambiguous,
            }
        )
        if method == "abbreviation":
            result["needs_verification"].append("等级按简称匹配，需核对是否为同一出版场所")
        if ambiguous:
            result["needs_verification"].append(
                "该名称或简称同时命中期刊与会议条目，已按较低等级标注，需核实实际出版场所"
            )

    if companion and not is_preprint:
        result["status"] = "unlisted"
        result["venue"] = journal or statement
        result["label"] = _label(ranking, "companion")
        result["needs_verification"].append(
            f"出版场所名称含“{companion}”等附属卷标记，不等同于 CCF 目录中的主会或期刊，等级需人工核实"
        )
        return result

    if journal and not is_preprint:
        hit = _match_name(ranking, journal, kind)
        if hit:
            apply_hit(hit, "journal")
            result["status"] = "ccf_listed"
            result["verified_venue"] = not result["ambiguous"]
            result["label"] = (
                _label(ranking, "ambiguous")
                if result["ambiguous"]
                else _label(
                    ranking,
                    "ccf_journal" if result["kind"] != "conference" else "ccf_conference",
                    ccf=result["ccf"] or "?",
                )
            )
            return result

    if statement and not companion:
        hit = _match_name(ranking, statement, None)
        if hit:
            apply_hit(hit, "author_statement")
            result["status"] = "preprint_claimed_venue" if is_preprint else "unlisted"
            result["needs_verification"].append(
                "录用信息来自作者自述或预印本页面，需核对正式出版记录后才可作为等级依据"
            )
            result["label"] = (
                _label(ranking, "ambiguous")
                if result["ambiguous"]
                else (
                    _label(ranking, "preprint_claimed_venue", venue=result["venue"])
                    if is_preprint
                    else _label(
                        ranking,
                        "ccf_journal" if result["kind"] != "conference" else "ccf_conference",
                        ccf=result["ccf"] or "?",
                    )
                )
            )
            return result

    if is_preprint:
        result["status"] = "preprint"
        result["venue"] = journal or "arXiv"
        result["label"] = _label(ranking, "preprint")
        result["needs_verification"].append("预印本：尚未完成正式同行评议，不属于 CCF 目录评级对象")
        if companion:
            result["needs_verification"].append(
                f"自述出版去向含“{companion}”等附属卷标记，核实等级时不能按主会计算"
            )
        return result
    if journal:
        result["status"] = "unlisted"
        result["venue"] = journal
        result["label"] = _label(ranking, "unlisted")
        result["needs_verification"].append(
            "内置目录只覆盖部分领域，未命中不等于低质量；请按 venue 官网核实等级"
        )
        return result
    result["label"] = _label(ranking, "missing")
    result["needs_verification"].append("缺少期刊或会议信息，需从 DOI 落地页核实出版场所")
    return result


def first_statement(paper: dict[str, Any]) -> str:
    """取作者自述的出版去向（期刊登记号、录用评论等），原文保留以便复核。"""
    for field in ("journal_reference", "reported_venue", "comment"):
        value = clean_text(paper.get(field))
        if value:
            return value
    return ""


def quality_score(
    classification: dict[str, Any],
    paper: dict[str, Any],
    ranking: dict[str, Any],
    tier_score: int | None = None,
) -> tuple[int, list[str]]:
    scoring = ranking.get("scoring") or {}
    evidence: list[str] = []

    def number(key: str, default: int) -> int:
        try:
            return int(scoring.get(key))
        except (TypeError, ValueError):
            return default

    status = classification.get("status")
    ccf = classification.get("ccf")
    if status == "ccf_listed" and ccf:
        score = int((scoring.get("ccf") or {}).get(ccf, number("formal_unlisted", 65)))
        evidence.append(f"{classification.get('label')}：按 CCF {classification.get('catalog_version')} 目录给分")
    elif status == "preprint_claimed_venue":
        score = number("preprint_with_reported_venue", 72)
        evidence.append("预印本，但作者自述已被出版场所接收，等级证据待核实")
    elif status == "preprint":
        score = number("preprint", 58)
        evidence.append("预印本：尚未完成正式同行评议")
    elif tier_score is not None:
        score = tier_score
        evidence.append("未命中内置目录，但命中研究配置里人工维护的期刊分层")
    elif status == "unlisted":
        score = number("formal_unlisted", 65)
        evidence.append("正规出版物但未收录于内置目录，等级需人工核实")
    else:
        score = number("venue_missing", 48)
        evidence.append("出版场所信息缺失")

    if ccf and tier_score is not None and tier_score > score:
        evidence.append("配置分层的分数高于目录分数，按较高者取值")
        score = tier_score

    for threshold, bonus in sorted(
        (ranking.get("scoring") or {}).get("citation_bonuses") or [], key=lambda item: -int(item[0])
    ):
        if int(paper.get("citation_count") or 0) >= int(threshold):
            score += int(bonus)
            evidence.append(f"已被引用 {paper.get('citation_count')} 次（阈值 {threshold}）")
            break

    article_type = clean_text(paper.get("article_type")).casefold()
    if "review" in article_type:
        cap = number("review_article_cap", 82)
        if score > cap:
            score = cap
        evidence.append("综述按证据综合质量评价，设置上限")
    if not clean_text(paper.get("abstract")):
        score -= number("abstract_missing_penalty", 15)
        evidence.append("摘要缺失，无法可靠评价实验完整性")
    return max(0, min(100, score)), evidence


def apply_policy(
    paper: dict[str, Any],
    classification: dict[str, Any],
    relevance: int,
    ranking: dict[str, Any],
    *,
    has_configured_tier: bool = False,
) -> list[str]:
    """按配置的权威性策略给出排除理由。"""
    policy = ranking.get("policy") or {}
    mode = clean_text(policy.get("mode")) or "label_only"
    if mode == "label_only":
        return []
    reasons: list[str] = []
    status = classification.get("status")
    if mode == "strict_ccf_or_tier":
        if status in {"preprint", "preprint_claimed_venue"}:
            reasons.append("策略 strict_ccf_or_tier：只保留已在正式出版场所发表的论文")
        elif status in {"unlisted", "missing"} and not has_configured_tier:
            reasons.append("策略 strict_ccf_or_tier：出版场所既不在 CCF 目录也不在配置分层中")
        return reasons
    if mode == "authority_first":
        if status == "preprint" and relevance < int(policy.get("preprint_min_relevance") or 85):
            reasons.append(
                f"权威性优先：预印本相关性 {relevance} 低于 {policy.get('preprint_min_relevance')}，不进入复核队列"
            )
        if (
            status in {"unlisted", "missing"}
            and not has_configured_tier
            and relevance < int(policy.get("unlisted_venue_min_relevance") or 80)
        ):
            reasons.append(
                f"权威性优先：出版场所未经核实的论文相关性 {relevance} 低于 "
                f"{policy.get('unlisted_venue_min_relevance')}，不进入复核队列"
            )
    return reasons


def summarize_queue(papers: list[dict[str, Any]], ranking: dict[str, Any]) -> dict[str, Any]:
    """统计复核队列里的等级分布，供报告与队列元数据使用。"""
    counts = {"ccf_A": 0, "ccf_B": 0, "ccf_C": 0, "preprint": 0, "unlisted": 0, "missing": 0}
    for paper in papers:
        classification = paper.get("venue_ranking") or {}
        ccf = classification.get("ccf")
        if classification.get("status") == "ccf_listed" and ccf in {"A", "B", "C"}:
            counts[f"ccf_{ccf}"] += 1
        elif classification.get("status") in {"preprint", "preprint_claimed_venue"}:
            counts["preprint"] += 1
        elif classification.get("status") == "unlisted":
            counts["unlisted"] += 1
        else:
            counts["missing"] += 1
    catalog = ranking.get("catalog") or {}
    return {
        "counts": counts,
        "catalog_name": clean_text(catalog.get("name")),
        "catalog_version": clean_text(catalog.get("version")),
        "domains_included": catalog.get("domains_included") or [],
        "completeness_note": clean_text(catalog.get("completeness_note")),
    }
