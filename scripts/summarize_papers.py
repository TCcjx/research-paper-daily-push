#!/usr/bin/env python3
"""验证 Codex 复核后的论文摘要数据，并生成中文每日报告。"""

from __future__ import annotations

import argparse
import math
from datetime import datetime, date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from common import ChineseArgumentParser, clean_text, load_json, normalize_doi, normalize_title, looks_like_preprint
from topic_scope import require_scope, validate_scope
from venue_ranking import reading_sort_key, venue_priority, venue_importance

REQUIRED_INSPIRATION = ("methods", "limitations_validation")


VENUE_STATUSES = {
    "ccf_listed",
    "preprint",
    "preprint_claimed_venue",
    "unlisted",
    "missing",
    "unknown",
}


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    if clean_text(value):
        return [clean_text(value)]
    return []


def _term_explanations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _venue_ranking(article: dict[str, Any]) -> dict[str, Any]:
    value = article.get("venue_ranking")
    return value if isinstance(value, dict) else {}


def _venue_line(article: dict[str, Any]) -> str:
    venue = _venue_ranking(article)
    label = clean_text(venue.get("label"))
    if not label:
        return ""
    notes = _list(venue.get("needs_verification"))[:1]
    return f"- 认可度/等级：{label}" + (f"（{notes[0]}）" if notes else "")


def valid_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_article(
    article: dict[str, Any], index: int, strict: bool, require_term_explanations: bool
) -> list[str]:
    errors: list[str] = []
    prefix = f"article[{index}]"
    for field in ("title", "chinese_title", "journal", "publication_date", "url", "article_type"):
        if not clean_text(article.get(field)):
            errors.append(f"{prefix}.{field} 为必填字段")
    score = article.get("recommendation_score")
    if type(score) is not int or not 0 <= score <= 100:
        errors.append(f"{prefix}.recommendation_score 必须是0到100之间的整数")
    if strict and type(score) is int and score < 70:
        errors.append(f"{prefix}.recommendation_score 低于70分推荐下限")
    try:
        date.fromisoformat(article.get("publication_date", ""))
    except (ValueError, TypeError):
        errors.append(f"{prefix}.publication_date 必须是有效的 YYYY-MM-DD 日期")
    if not valid_url(article.get("url")):
        errors.append(f"{prefix}.url 必须是 http/https 链接")
    component_scores = article.get("component_scores") or {}
    if not isinstance(component_scores, dict):
        errors.append(f"{prefix}.component_scores 必须是对象")
        component_scores = {}
    for field in ("relevance", "novelty", "quality", "methodology", "inspiration"):
        value = component_scores.get(field)
        if type(value) is not int or not 0 <= value <= 100:
            errors.append(f"{prefix}.component_scores.{field} 必须是0到100之间的整数")
    why = clean_text(article.get("why_worth_reading"))
    if not why:
        errors.append(f"{prefix}.why_worth_reading 为必填字段")
    elif len(why) > 100:
        errors.append(f"{prefix}.why_worth_reading 超过100个字符")
    findings = _list(article.get("core_findings"))
    if not 1 <= len(findings) <= 5:
        errors.append(f"{prefix}.core_findings 必须包含1–5条受证据约束的要点")
    terms = _term_explanations(article.get("term_explanations"))
    if require_term_explanations and not terms:
        errors.append(f"{prefix}.term_explanations 必须覆盖本篇论文中使用的专业术语")
    seen_terms: set[str] = set()
    for term_index, item in enumerate(terms):
        term_prefix = f"{prefix}.term_explanations[{term_index}]"
        term = clean_text(item.get("term"))
        academic = clean_text(item.get("academic_explanation"))
        plain = clean_text(item.get("plain_explanation"))
        if not term:
            errors.append(f"{term_prefix}.term 为必填字段")
        else:
            key = term.casefold()
            if key in seen_terms:
                errors.append(f"{term_prefix}.term 与本篇论文中的其他术语重复")
            seen_terms.add(key)
        if not academic:
            errors.append(f"{term_prefix}.academic_explanation 为必填字段")
        elif len(academic) > 180:
            errors.append(f"{term_prefix}.academic_explanation 超过180个字符")
        if not plain:
            errors.append(f"{term_prefix}.plain_explanation 为必填字段")
        elif len(plain) > 180:
            errors.append(f"{term_prefix}.plain_explanation 超过180个字符")
        if academic and plain and academic == plain:
            errors.append(f"{term_prefix} 的学术解释和大白话解释不能完全相同")
    inspiration = article.get("research_inspiration") or {}
    if not isinstance(inspiration, dict):
        errors.append(f"{prefix}.research_inspiration 必须是对象")
    else:
        for field in REQUIRED_INSPIRATION:
            if not _list(inspiration.get(field)):
                errors.append(f"{prefix}.research_inspiration.{field} 为必填字段")
    if not 1 <= len(_list(article.get("tags"))) <= 8:
        errors.append(f"{prefix}.tags 必须包含1–8个聚焦标签")
    if strict:
        if type(article.get("is_preprint")) is not bool:
            errors.append(f"{prefix}.is_preprint 必须明确为布尔值，保留预印本身份")
        elif not article["is_preprint"] and looks_like_preprint(article):
            errors.append(f"{prefix}.is_preprint 与预印本场所矛盾")
        evidence = article.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{prefix}.evidence 必须记录实际阅读的证据链接")
        else:
            for item in evidence:
                if not isinstance(item, dict) or not valid_url(item.get("url")) or item.get("kind") not in {"abstract", "full_text", "publisher", "repository"} or not _list(item.get("supports")):
                    errors.append(f"{prefix}.evidence 每项须包含有效 url、kind 与 supports")
        assessment = article.get("assessment") or {}
        if not isinstance(assessment, dict):
            assessment = {}
        for field in ("contribution", "comparison", "limitations"):
            if not clean_text(assessment.get(field)):
                errors.append(f"{prefix}.assessment.{field} 必填；未完成对比需如实说明")
        if type(assessment.get("importance")) is not int or not 0 <= assessment["importance"] <= 100:
            errors.append(f"{prefix}.assessment.importance 必须是0–100整数，与热度分开评价")
        if assessment.get("confidence") not in {"high", "medium", "low"}:
            errors.append(f"{prefix}.assessment.confidence 必须是 high/medium/low")
        if assessment.get("importance_basis") == "venue_60_contribution_40":
            contribution = assessment.get("contribution_importance")
            venue_score = assessment.get("venue_importance")
            if type(contribution) is not int or not 0 <= contribution <= 100 or venue_score != venue_importance(article):
                errors.append(f"{prefix}.assessment 的场所/贡献重要性分量无效")
            elif assessment.get("importance") != round(venue_score * .6 + contribution * .4):
                errors.append(f"{prefix}.assessment.importance 与场所60%+贡献40%不一致")
        weights = {"relevance": .4, "novelty": .2, "quality": .15, "methodology": .15, "inspiration": .1}
        if all(type(component_scores.get(k)) is int for k in weights):
            expected = round(sum(component_scores[k] * w for k, w in weights.items()))
            if score != expected:
                errors.append(f"{prefix}.recommendation_score 与分项加权结果 {expected} 不一致")
    if strict and article.get("score_status") != "evidence_reviewed":
        errors.append(f"Codex 核对摘要或全文后，{prefix}.score_status 必须设为 evidence_reviewed")
    if article.get("venue_ranking") is not None:
        venue = _venue_ranking(article)
        if not isinstance(article.get("venue_ranking"), dict):
            errors.append(f"{prefix}.venue_ranking 必须是对象")
        else:
            if not clean_text(venue.get("label")):
                errors.append(f"{prefix}.venue_ranking.label 不能为空")
            ccf = clean_text(venue.get("ccf"))
            if ccf and ccf not in {"A", "B", "C"}:
                errors.append(f"{prefix}.venue_ranking.ccf 只能是 A、B 或 C；不确定时留空并写进 needs_verification")
            status = clean_text(venue.get("status"))
            if status and status not in VENUE_STATUSES:
                errors.append(f"{prefix}.venue_ranking.status 只能是 {', '.join(sorted(VENUE_STATUSES))}")
            if ccf and not venue.get("verified_venue"):
                errors.append(f"{prefix}.venue_ranking 标注了 CCF 等级但 verified_venue 为假，需先核实出版场所")
    if article.get("strong_recommendation") and len(_list(article.get("strong_recommendation_reasons"))) < 1:
        errors.append(f"强烈推荐时必须填写 {prefix}.strong_recommendation_reasons")
    return errors


def stars(relevance: int) -> str:
    """把相关性映射到1–5颗星。

    这里必须向上取整：Python 内置的 round() 采用银行家舍入，会让 70、80、90 分
    都落在4颗星，导致星级随分数升高而“停滞”。
    """
    filled = max(1, min(5, math.ceil(relevance / 20)))
    return "★" * filled + "☆" * (5 - filled)


def line_items(values: Any, prefix: str = "- ") -> list[str]:
    return [f"{prefix}{item}" for item in _list(values)]


def render_article(article: dict[str, Any], rank: int, detailed: bool = False) -> str:
    strong = bool(article.get("strong_recommendation"))
    heading = f"### {rank}. {clean_text(article['chinese_title'])}"
    if strong:
        heading += "　🔥 **强烈推荐**"
    doi = normalize_doi(article.get("doi")) or "暂无/需核实"
    if not detailed:
        assessment = article.get("assessment") or {}
        confidence = {"high": "高", "medium": "中", "low": "低"}.get(assessment.get("confidence"), "待核实")
        lane = {"daily": "当日候选", "supplementary": "近期补充"}.get(article.get("reading_lane"), "时间归类待核对")
        preprint = "｜预印本，未经正式同行评议" if article.get("is_preprint") or "preprint" in article.get("article_type", "").lower() else ""
        lines = [heading, "", f"[{clean_text(article['title'])}]({article['url']})", "",
                 f"{article['journal']}｜{article['publication_date']}｜{lane}{preprint}",
                 *([_venue_line(article)] if _venue_line(article) else []),
                 f"推荐 {article['recommendation_score']}/100｜重要性 {assessment.get('importance', '待核实')}/100｜证据置信度：{confidence}",
                 "", f"**为什么值得读：** {article['why_worth_reading']}",
                 f"**主要贡献：** {assessment.get('contribution', '需要核实')}",
                 f"**相较已有工作：** {assessment.get('comparison', '尚未完成独立对比')}", ""]
        if assessment.get("importance_basis") == "venue_60_contribution_40":
            lines += [f"**阅读重要性依据：** 场所 {assessment['venue_importance']}×60% + 贡献 {assessment['contribution_importance']}×40%（个人优先级）。", ""]
        signal = article.get("attention_signals") or {}
        if signal.get("status") == "observed":
            changes = "; ".join(f"{source} {delta:+d}" for source, delta in signal.get("citation_deltas", {}).items())
            lines.append(f"**关注度辅助证据：** {signal['interval_days']}天内同源引用数变化：{changes}；存在索引延迟。")
        else:
            lines.append("**关注度辅助证据：** 暂无可比引用快照，热度未知。")
        lines += line_items(article.get("core_findings"))
        lines += ["", f"**局限：** {assessment.get('limitations', '需要核实')}",
                  "**可借鉴方法：** " + "; ".join(_list((article.get("research_inspiration") or {}).get("methods")))]
        if strong:
            lines += ["**强烈推荐依据：** " + "; ".join(_list(article.get("strong_recommendation_reasons")))]
        if isinstance(article.get("venue_evidence"), dict) and valid_url(article["venue_evidence"].get("url")):
            lines.append(f"- 发表场所证据：[正式论文记录]({article['venue_evidence']['url']})")
        for item in article.get("evidence", []):
            lines.append(f"- 证据（{item['kind']}）：[{'; '.join(_list(item['supports']))}]({item['url']})")
        lines += ["", "**重点标记：** " + " ".join(_list(article.get("tags")))]
        return "\n".join(lines)
    components = article.get("component_scores") or {}
    tags = " ".join(
        tag if str(tag).startswith("#") else f"#{clean_text(tag).replace(' ', '')}"
        for tag in _list(article.get("tags"))
    )
    venue_line = _venue_line(article)
    sections = [
        heading,
        "",
        f"**英文原题：** {clean_text(article['title'])}",
        "",
        f"- 期刊：{clean_text(article['journal'])}",
        *([venue_line] if venue_line else []),
        f"- 发表日期：{clean_text(article['publication_date'])}",
        f"- DOI：{doi}",
        f"- 论文链接：{clean_text(article['url'])}",
        f"- 论文类型：{clean_text(article['article_type'])}",
        "",
        f"**推荐指数：{article['recommendation_score']}/100**",
        f"**相关性：{stars(int(components.get('relevance') or 0))}**",
        (
            "分项：相关性 {relevance}｜创新性 {novelty}｜期刊/论文质量 {quality}｜"
            "方法学价值 {methodology}｜研究启发 {inspiration}"
        ).format(**components),
        "",
        f"**为什么值得看：** {clean_text(article['why_worth_reading'])}",
        "",
        "**核心发现**",
        "",
    ]
    sections.extend(f"{number}. {finding}" for number, finding in enumerate(_list(article["core_findings"]), 1))
    terms = _term_explanations(article.get("term_explanations"))
    if terms:
        sections.extend(["", "**学术名词双重解释**", ""])
        for number, item in enumerate(terms, 1):
            sections.extend([
                f"{number}. **{clean_text(item.get('term'))}**",
                f"   - 正常解释：{clean_text(item.get('academic_explanation'))}",
                f"   - 比喻/大白话：{clean_text(item.get('plain_explanation'))}",
            ])
    sections.extend(["", "**对我的研究有什么启发**", ""])
    labels = {
        "experimental_design": "可借鉴的实验设计",
        "methods": "值得采用的技术",
        "key_entities_mechanisms": "值得关注的对象/机制",
        "target_system": "向目标研究体系延伸",
        "future_direction": "潜在研究或基金方向",
        "paper_potential": "高水平论文潜力",
        "limitations_validation": "局限与验证需求",
    }
    inspiration = article["research_inspiration"]
    for field in labels:
        value = "；".join(_list(inspiration.get(field)))
        if value:
            sections.append(f"- **{labels[field]}：** {value}")
    if strong:
        sections.extend(["", "**🔥 强烈推荐理由**", ""])
        sections.extend(line_items(article.get("strong_recommendation_reasons")))
    verification = _list(article.get("needs_verification"))
    if verification:
        sections.extend(["", "**需核实：** " + "；".join(verification)])
    for evidence in article.get("evidence", []):
        sections.append(f"- 证据（{evidence['kind']}）：[{'; '.join(_list(evidence['supports']))}]({evidence['url']})")
    assessment = article.get("assessment") or {}
    sections += [f"**相较已有工作：** {assessment.get('comparison', '需要核实')}",
                 f"**证据置信度：** {assessment.get('confidence', '需要核实')}｜重要性：{assessment.get('importance', '待核实')}/100"]
    if assessment.get("importance_basis") == "venue_60_contribution_40":
        sections.append(f"**阅读重要性依据：** 场所 {assessment['venue_importance']}×60% + 贡献 {assessment['contribution_importance']}×40%（个人优先级）。")
    if isinstance(article.get("venue_evidence"), dict) and valid_url(article["venue_evidence"].get("url")):
        sections.append(f"- 发表场所证据：[正式论文记录]({article['venue_evidence']['url']})")
    sections.extend(["", f"**重点标记：** {tags}"])
    return "\n".join(sections)


def render_venue_distribution(distribution: Any) -> str:
    if not isinstance(distribution, dict):
        return ""
    counts = distribution.get("counts")
    if not isinstance(counts, dict):
        return ""

    def value(key: str) -> int:
        try:
            return int(counts.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    order = (
        ("ccf_A", "CCF A"),
        ("ccf_B", "CCF B"),
        ("ccf_C", "CCF C"),
        ("preprint", "预印本"),
        ("unlisted", "正规出版但未收录目录"),
        ("missing", "出版场所待核实"),
    )
    pieces = [f"{name} {value(key)} 篇" for key, name in order if value(key)]
    if not pieces:
        return ""
    line = "等级分布：" + "、".join(pieces)
    catalog = clean_text(distribution.get("catalog_name"))
    version = clean_text(distribution.get("catalog_version"))
    if catalog and version:
        line += f"（依据：{catalog} {version}）"
    note = clean_text(distribution.get("completeness_note"))
    return line + (f"  \n说明：{note}" if note else "")


def render_radar(radar: dict) -> list[str]:
    lines = ["## 研究热点线索", "", radar.get("limitations", "尚无可比趋势样本；不根据论文题名推断领域热度。"), ""]
    names = {"rising": "样本发文升温", "emerging_sample": "样本新出现，需继续观察",
             "no_clear_rise": "未见明确升温", "insufficient_data": "数据不足/样本不完整"}
    for topic in radar.get("topics", [])[:8]:
        ratio = topic.get("daily_rate_ratio")
        lines.append(f"- **{topic['topic']}**：{names[topic['status']]}。近7天 {topic['recent_count']} 篇，前 {topic['baseline_days']} 天 {topic['baseline_count']} 篇；日均比值 {ratio if ratio is not None else '不可计算'}。")
        for evidence in topic.get("evidence", [])[:2]:
            lines.append(f"  - [{evidence['title']}]({evidence['url']})")
    return lines + [""]


def render_updates(updates: list) -> list[str]:
    lines = ["## 持续跟踪", ""]
    if not updates:
        return lines + ["本次检索样本中暂无已识别的更新；未覆盖的旧论文仍可能有新进展。", ""]
    seen = set()
    for item in updates:
        marker = (item.get("title"), item.get("url"))
        if marker in seen:
            continue
        seen.add(marker)
        changes = item.get("changes") or item.get("updated_fields") or ["元数据更新"]
        lines.append(f"- [{item.get('title')}]({item.get('url')})：{', '.join(changes)}（元数据信号，实质结论需复核）。")
    return lines + [""]


def render_discovery(queue: dict) -> str:
    metadata = queue["metadata"]
    lines = ["# 科研论文发现简报（未复核）", "", f"日期：{queue['date']}",
             f"检索窗口：{metadata['window']}", f"趋势样本：{metadata['trend_window']}", "",
             "以下为检索元数据和待复核候选，不能视为重点论文推荐或已验证研究结论。", ""]
    lines += [f"> {w}" for w in metadata.get("warnings", [])]
    if metadata.get("topic_scope_name"):
        lines += [f"核心主题：{metadata['topic_scope_name']}；已排除 {metadata.get('off_topic_count', 0)} 篇关键词相关但不满足主题准入的记录。", ""]
    lines += render_radar(queue.get("radar") or {})
    lines += render_updates(queue.get("updates_not_for_new_push", []) + (queue.get("radar") or {}).get("observed_updates", []))
    for lane, name in (("daily", "当日待复核"), ("supplementary", "近期补充待复核")):
        lines += [f"## {name}", ""]
        papers = [p for p in queue.get("candidates", []) if p.get("reading_lane") == lane]
        if not papers:
            lines += ["暂无符合初筛条件的候选。", ""]
        for p in papers:
            review = "预印本，未经正式同行评议" if p.get("is_preprint") else "正式出版记录，单篇质量待复核"
            lines += [f"- [{p['title']}]({p.get('url', '')})｜{p.get('publication_date', '')}｜{review}",
                      f"  - 初筛代理分 {p['recommendation_score']}；主题：{', '.join(p.get('matched_topics', []))}"]
    return "\n".join(lines).rstrip() + "\n"


def render_report(payload: dict[str, Any], strict: bool = True) -> str:
    validate_scope(payload.get("topic_scope"))
    articles = payload.get("articles", [])
    if not isinstance(articles, list):
        raise TypeError("articles 必须是数组")
    errors: list[str] = []
    if not isinstance(payload.get("metadata", {}), dict):
        raise TypeError("metadata 必须是对象")
    if payload.get("report_style", "brief") not in {"brief", "detailed"}:
        errors.append("report_style 必须是 brief/detailed")
    if strict:
        try:
            date.fromisoformat(payload.get("date", ""))
        except (TypeError, ValueError):
            errors.append("date 必须是有效的 YYYY-MM-DD 日期")
    require_term_explanations = clean_text(payload.get("term_explanation_mode")).casefold() == "dual"
    reading_order = (payload.get("metadata") or {}).get("reading_order", "score")
    if reading_order not in {"score", "venue_first"}:
        errors.append("metadata.reading_order 必须是 score/venue_first")
    for index, article in enumerate(articles):
        if not isinstance(article, dict):
            errors.append(f"article[{index}] 必须是对象")
        else:
            require_scope(article, payload.get("topic_scope"))
            if strict and reading_order == "venue_first":
                if (article.get("assessment") or {}).get("importance_basis") != "venue_60_contribution_40":
                    errors.append(f"article[{index}] 请通过 deliver 生成场所优先的重要性评分")
                if venue_priority(article) > 0:
                    evidence = article.get("venue_evidence") or {}
                    kind = (article.get("venue_ranking") or {}).get("kind")
                    allowed_types = {"full", "regular"} if kind == "conference" else {"journal"}
                    if not isinstance(evidence, dict) or not valid_url(evidence.get("url")) or evidence.get("publication_type") not in allowed_types:
                        errors.append(f"article[{index}].venue_evidence 需记录正式论文页面 url 和 publication_type（full/regular/journal），不能只引用目录或录用自述")
            errors.extend(validate_article(article, index, strict, require_term_explanations))
    ideas = _list(payload.get("research_ideas"))
    if len(ideas) > 3:
        errors.append("research_ideas 最多包含3个以证据为基础的研究思路")
    maximum = (payload.get("metadata") or {}).get("maximum", 10)
    if type(maximum) is not int or not 1 <= maximum <= 10:
        errors.append("metadata.maximum 必须是1–10整数")
    elif strict and len(articles) > maximum:
        errors.append(f"articles 超出本次推荐上限 {maximum}")
    if strict and not 0 <= len(articles) <= 10:
        errors.append("articles 最多只能包含10篇论文")
    seen_dois, seen_titles = set(), set()
    for article in articles:
        if not isinstance(article, dict):
            continue
        doi, title = normalize_doi(article.get("doi")), normalize_title(article.get("title"))
        if (doi and doi in seen_dois) or (title and title in seen_titles):
            errors.append("articles 含有重复 DOI 或规范化题名")
        if doi:
            seen_dois.add(doi)
        if title:
            seen_titles.add(title)
    if errors:
        raise ValueError("已复核数据验证失败：\n- " + "\n- ".join(errors))

    report_date = clean_text(payload.get("date")) or datetime.now().astimezone().date().isoformat()
    metadata = payload.get("metadata") or {}
    retrieved = int(metadata.get("retrieved_count") or 0)
    screened = int(metadata.get("screened_count") or 0)
    articles = sorted(articles, key=reading_sort_key if reading_order == "venue_first"
                      else lambda item: int(item.get("recommendation_score") or 0), reverse=True)
    parts = [
        "# 【今日科研文献雷达】",
        "",
        f"日期：{report_date}  ",
        f"检索论文：{retrieved}篇  ",
        f"筛选论文：{screened}篇  ",
        f"最终推荐：{len(articles)}篇",
        "",
    ]
    parts += [f"检索窗口：{metadata.get('window', '未提供，需核实')}", ""]
    if payload.get("topic_scope"):
        parts += [f"核心主题：{payload['topic_scope']['name']}", ""]
    if reading_order == "venue_first":
        parts += ["阅读顺序：同主题内已核实 CCF A 类优先，再参考其他场所等级、相关性和贡献；未核实录用不按 A 类计算。重要性按场所60%与贡献40%计算，为个人阅读优先级。", ""]
    parts += render_radar(payload.get("radar") or {})
    parts += render_updates(payload.get("updates") or [])
    venue_summary = render_venue_distribution(metadata.get("venue_distribution"))
    if venue_summary:
        parts.extend([venue_summary, ""])
    warnings = _list(metadata.get("warnings"))
    if warnings:
        parts.extend(["> 检索提示：" + "；".join(warnings), ""])
    if not articles:
        parts.extend([
            "今天没有达到推荐阈值且证据充分的论文。宁缺毋滥，不用低相关性论文填充名额。",
            "",
            "## 今日最值得阅读前三名",
            "",
            "今日没有符合条件的前三名论文。",
            "",
            "## 今日研究灵感",
            "",
            "今日暂无足够文献证据形成可靠研究灵感。",
        ])
        return "\n".join(parts).rstrip() + "\n"

    parts.extend(["## 今日重点论文（含单独标注的近期补充）", ""])
    parts.append("\n\n---\n\n".join(render_article(article, rank, detailed=payload.get("report_style") == "detailed" or require_term_explanations) for rank, article in enumerate(articles, 1)))
    parts.extend(["", "## 今日最值得阅读前三名", ""])
    medals = ("🥇", "🥈", "🥉")
    ordinals = ("第一篇", "第二篇", "第三篇")
    for index, article in enumerate(articles[:3]):
        reason = clean_text(article.get("top3_reason")) or clean_text(article.get("why_worth_reading"))
        parts.extend([
            f"{medals[index]} **{ordinals[index]}：{clean_text(article['chinese_title'])}**",
            "",
            reason,
            "",
        ])
    parts.extend(["## 今日研究灵感", ""])
    parts.extend(f"{index}. {idea}" for index, idea in enumerate(ideas, 1))
    return "\n".join(parts).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = ChineseArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="经过 Codex 复核的 JSON")
    parser.add_argument("--output", required=True, help="Markdown 报告路径")
    parser.add_argument("--allow-draft", action="store_true", help="仅调试时允许使用初步 score_status")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = load_json(args.input, {})
    report = render_report(payload, strict=not args.allow_draft)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8", newline="\n")
    print(f"已将 {len(payload.get('articles') or [])} 篇论文写入报告：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
