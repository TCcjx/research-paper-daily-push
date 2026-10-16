#!/usr/bin/env python3
"""按 DOI 优先、题名兜底的身份规则筛选或提交论文历史。"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from common import (
    ChineseArgumentParser,
    atomic_write_json,
    clean_text,
    extract_papers,
    load_json,
    normalize_doi,
    normalize_title,
    looks_like_preprint,
    paper_key,
    utc_now_iso,
)

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_HISTORY = SCRIPT_DIR.parent / "data" / "pushed_articles.json"


def load_history(path: str | Path) -> list[dict[str, Any]]:
    payload = load_json(path, [])
    if isinstance(payload, dict):
        payload = payload.get("articles", [])
    if not isinstance(payload, list):
        raise TypeError("历史文件必须是 JSON 数组，或包含 articles 数组的对象")
    return [item for item in payload if isinstance(item, dict)]


def build_indexes(history: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_doi: dict[str, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    for item in history:
        doi = normalize_doi(item.get("doi"))
        title = normalize_title(item.get("title"))
        if doi:
            by_doi[doi] = item
        if title:
            by_title[title] = item
    return by_doi, by_title


def meaningful_update(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    # 已知正式版的论文仍可能由 arXiv 返回旧预印本元数据，不能回退成预印本。
    metadata_fields = () if not looks_like_preprint(old) and looks_like_preprint(new) else ("publication_date", "journal", "article_type")
    for field in metadata_fields:
        before = clean_text(old.get(field)).casefold()
        after = clean_text(new.get(field)).casefold()
        if before and after and before != after:
            changed.append(field)
    if not old.get("doi") and new.get("doi"):
        changed.append("doi")
    old_version = (old.get("source_ids") or {}).get("arxiv_version")
    new_version = (new.get("source_ids") or {}).get("arxiv_version")
    if old_version and new_version and old_version != new_version:
        changed.append("arxiv_version")
    if looks_like_preprint(old) and not looks_like_preprint(new):
        changed.append("formal_publication")
    return changed


def filter_history(papers: list[dict[str, Any]], history: list[dict[str, Any]]) -> dict[str, Any]:
    by_doi, by_title = build_indexes(history)
    new_items: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for paper in papers:
        doi = normalize_doi(paper.get("doi"))
        title = normalize_title(paper.get("title"))
        previous = by_doi.get(doi) if doi else None
        previous = previous or (by_title.get(title) if title else None)
        item = dict(paper)
        if previous is None:
            item["history_status"] = "new"
            new_items.append(item)
            continue
        changes = meaningful_update(previous, item)
        if changes:
            if not looks_like_preprint(previous) and looks_like_preprint(item):
                for field in ("doi", "journal", "article_type", "publication_date", "url", "is_preprint"):
                    if field in previous:
                        item[field] = previous[field]
            item["history_status"] = "update"
            item["updated_fields"] = changes
            item["previous_record"] = {
                "doi": previous.get("doi", ""),
                "publication_date": previous.get("publication_date", ""),
                "pushed_date": previous.get("pushed_date", ""),
            }
            updates.append(item)
        else:
            item["history_status"] = "duplicate"
            item["previous_pushed_date"] = previous.get("pushed_date", "")
            duplicates.append(item)
    return {
        "generated_at": utc_now_iso(),
        "history_count": len(history),
        "input_count": len(papers),
        "new_count": len(new_items),
        "update_count": len(updates),
        "duplicate_count": len(duplicates),
        "new": new_items,
        "updates": updates,
        "duplicates": duplicates,
    }


def commit_history(
    papers: list[dict[str, Any]],
    history: list[dict[str, Any]],
    *,
    delivery: str,
    pushed_date: str,
) -> tuple[list[dict[str, Any]], int]:
    by_doi, by_title = build_indexes(history)
    committed = 0
    for paper in papers:
        doi = normalize_doi(paper.get("doi"))
        title_key = normalize_title(paper.get("title"))
        previous = (by_doi.get(doi) if doi else None) or by_title.get(title_key)
        if previous:
            # 交付了更新通知后推进旧记录，避免每天重复通知同一变化。
            if meaningful_update(previous, paper):
                preserve_formal = not looks_like_preprint(previous) and looks_like_preprint(paper)
                for field in (() if preserve_formal else ("doi", "journal", "article_type", "publication_date", "url")):
                    if paper.get(field):
                        previous[field] = paper[field]
                previous["source_ids"] = {**(previous.get("source_ids") or {}), **(paper.get("source_ids") or {})}
                if "is_preprint" in paper and not preserve_formal:
                    previous["is_preprint"] = bool(paper["is_preprint"])
                previous["last_update_delivered"] = pushed_date
                if doi:
                    by_doi[doi] = previous
            continue
        record = {
            "doi": doi,
            "title": clean_text(paper.get("title")),
            "publication_date": clean_text(paper.get("publication_date")),
            "pushed_date": pushed_date,
            "relevance_score": int((paper.get("component_scores") or {}).get("relevance") or 0),
            "recommendation_score": int(paper.get("recommendation_score") or 0),
            "journal": clean_text(paper.get("journal")),
            "article_type": clean_text(paper.get("article_type")),
            "is_preprint": bool(paper.get("is_preprint")),
            "delivery": delivery,
            "url": paper.get("url", ""),
            "source_ids": paper.get("source_ids") or {},
            "identity_key": paper_key(paper),
        }
        history.append(record)
        if doi:
            by_doi[doi] = record
        if title_key:
            by_title[title_key] = record
        committed += 1
    history.sort(key=lambda item: (item.get("pushed_date", ""), item.get("title", "")))
    return history, committed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = ChineseArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    filter_parser = subparsers.add_parser("filter", help="区分新论文、更新论文和重复论文")
    filter_parser.add_argument("--input", required=True)
    filter_parser.add_argument("--output", required=True)
    filter_parser.add_argument("--history", default=str(DEFAULT_HISTORY))

    commit_parser = subparsers.add_parser("commit", help="以原子方式追加已成功交付的论文")
    commit_parser.add_argument("--input", required=True)
    commit_parser.add_argument("--history", default=str(DEFAULT_HISTORY))
    commit_parser.add_argument("--delivery", choices=("wxpusher", "serverchan", "wecom", "local-report"), required=True)
    commit_parser.add_argument("--pushed-date", help="推送日期 YYYY-MM-DD；默认为本地日期")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    history = load_history(args.history)
    payload = load_json(args.input, {})
    papers = extract_papers(payload)
    if args.command == "commit" and isinstance(payload, dict):
        papers += [p for p in payload.get("updates", []) if isinstance(p, dict) and p.get("history_status") == "update"]
    if args.command == "filter":
        result = filter_history(papers, history)
        atomic_write_json(args.output, result)
        print(
            f"历史筛选完成：新论文 {result['new_count']} 篇，更新 {result['update_count']} 篇，"
            f"重复 {result['duplicate_count']} 篇。"
        )
        return 0

    pushed_date = args.pushed_date or datetime.now().astimezone().date().isoformat()
    updated, committed = commit_history(
        papers, history, delivery=args.delivery, pushed_date=pushed_date
    )
    atomic_write_json(args.history, updated)
    print(f"已通过 {args.delivery} 将 {committed} 篇论文写入推送历史。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
