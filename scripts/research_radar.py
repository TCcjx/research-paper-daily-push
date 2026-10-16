#!/usr/bin/env python3
"""在同一检索样本内比较主题活跃度，并保存独立于交付历史的观测快照。"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from typing import Any

from common import clean_text, normalize_title, paper_key
from profile_config import topic_groups
from topic_scope import assess_scope, term_matches


def scope_key(profile: dict, queries: list[str], sources: list[str]) -> str:
    # 主题、查询、来源发生变化后不再对比之前的引用快照。
    value = {"profile": profile, "queries": sorted(queries), "sources": sorted(sources)}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


def analyze(raw: dict, profile: dict, state: dict, *, today: str) -> tuple[dict, dict]:
    """纯函数。趋势是样本中的发文活跃度，不是社会热度或论文质量。"""
    end = date.fromisoformat(today)
    start = date.fromisoformat(raw["date_from"])
    key = scope_key(profile, raw.get("queries", []), raw.get("sources_requested", []))
    snapshots = list(state.get("snapshots") or [])
    previous = next((s for s in reversed(sorted(snapshots, key=lambda s: s["date"]))
                     if s["scope"] == key and s["date"] < today and s.get("healthy")), None)
    healthy = not raw.get("errors") and not raw.get("coverage_warnings")
    old_records = {r["key"]: r for r in previous["records"]} if previous else {}
    old_titles = {r["normalized_title"]: r for r in previous["records"]} if previous else {}
    groups = topic_groups(profile)
    records, paper_signals, updates = [], {}, []
    for paper in raw.get("papers", []):
        if not assess_scope(paper, profile.get("topic_scope"))["in_scope"]:
            continue
        identity = paper_key(paper)
        text = f"{paper.get('title', '')} {paper.get('abstract', '')}".casefold()
        labels = [g["label"] for g in groups if any(term_matches(text, t) for t in g["terms"])]
        record = {"key": identity, "normalized_title": normalize_title(paper.get("title")),
                  "title": paper.get("title"), "url": paper.get("url"),
                  "publication_date": paper.get("publication_date", ""), "topics": labels,
                  "sampled": not paper.get("_watch_only", False),
                  "citation_counts": paper.get("citation_counts") or {},
                  "journal": paper.get("journal", ""), "is_preprint": bool(paper.get("is_preprint")),
                  "version": clean_text((paper.get("source_ids") or {}).get("arxiv_version")),
                  "source_updated_at": paper.get("source_updated_at", "")}
        records.append(record)
        old = old_records.get(identity) or old_titles.get(record["normalized_title"])
        signal: dict[str, Any] = {"status": "unknown", "note": "无可比引用快照；未知不代表不热门"}
        if old and previous and healthy:
            days = (end - date.fromisoformat(previous["date"])).days
            deltas = {source: count - old["citation_counts"][source]
                      for source, count in record["citation_counts"].items()
                      if source in old.get("citation_counts", {})}
            if deltas:
                signal = {"status": "observed", "interval_days": days, "citation_deltas": deltas,
                          "note": "同来源累计引用数变化；存在索引延迟，不等于当日传播热度"}
            changes = []
            if old.get("is_preprint") and not record["is_preprint"]:
                changes.append("正式出版")
            if old.get("version") and record["version"] and old["version"] != record["version"]:
                changes.append("arXiv 版本更新")
            if changes:
                updates.append({"title": record["title"], "url": record["url"],
                                "changes": changes, "evidence_level": "metadata"})
        paper_signals[identity] = signal
    trends = []
    recent_start = end - timedelta(days=6)
    baseline_days = max(0, (recent_start - start).days)
    for group in groups:
        matching = [r for r in records if r["sampled"] and group["label"] in r["topics"]]
        recent = [r for r in matching if recent_start.isoformat() <= r["publication_date"] <= today]
        baseline = [r for r in matching if start.isoformat() <= r["publication_date"] < recent_start.isoformat()]
        ratio = round((len(recent) / 7) / (len(baseline) / baseline_days), 2) if baseline and baseline_days else None
        status = "insufficient_data"
        if healthy and baseline_days >= 14:
            if len(recent) >= 3 and baseline and ratio >= 1.5:
                status = "rising"
            elif len(recent) >= 3 and not baseline:
                status = "emerging_sample"
            else:
                status = "no_clear_rise"
        trends.append({"topic": group["label"], "status": status, "recent_count": len(recent),
                       "baseline_count": len(baseline), "recent_days": 7, "baseline_days": baseline_days,
                       "daily_rate_ratio": ratio,
                       "evidence": [{"title": r["title"], "url": r["url"]} for r in recent[:5]]})
    trends.sort(key=lambda t: (t["status"] != "rising", -t["recent_count"], t["topic"]))
    snapshot = {"scope": key, "date": today, "healthy": healthy, "records": records, "topics": trends}
    cutoff = (end - timedelta(days=90)).isoformat()
    snapshots = [s for s in snapshots if s["date"] >= cutoff and not (s["scope"] == key and s["date"] == today)]
    snapshots.append(snapshot)
    radar = {"scope": key, "sample_healthy": healthy,
             "date_from": start.isoformat(), "date_to": today, "topics": trends,
             "paper_signals": paper_signals, "observed_updates": updates,
             "limitations": "仅反映当前查询、来源与抓取上限内的发文活跃度；不是全领域统计、社交热度或质量评价。缺源或截断时不判定升温。"}
    return radar, {"version": 1, "snapshots": snapshots}
