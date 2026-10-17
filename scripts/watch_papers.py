#!/usr/bin/env python3
"""对明确关注的 DOI/arXiv ID 查询最新元数据；有界执行，不做内容结论推断。"""
from __future__ import annotations

import time
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

from common import clean_text, json_request, normalize_doi, text_request
from search_papers import _arxiv_entry, _crossref_date, ATOM_NS


def check_watchlist(items: list[dict], limit: int = 20, *, sources: list[str] | None = None) -> tuple[list[dict], list[str]]:
    active = set(sources if sources is not None else ["arxiv"])
    papers, errors, seen, arxiv = [], [], set(), []
    unavailable = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        doi = normalize_doi(item.get("doi"))
        arxiv_id = clean_text(item.get("arxiv") or (item.get("source_ids") or {}).get("arxiv"))
        identity = doi or arxiv_id
        if not identity or identity in seen:
            continue
        if len(seen) >= limit:
            break
        seen.add(identity)
        if arxiv_id and "arxiv" in active:
            arxiv.append(arxiv_id)
        if not doi or "crossref" not in active:
            if not (arxiv_id and "arxiv" in active):
                unavailable += 1
            continue
        try:
            item = json_request(f"https://api.crossref.org/works/{quote(doi, safe='')}")["message"]
            if item.get("type") not in {"journal-article", "proceedings-article", "posted-content"}:
                continue
            papers.append({"doi": normalize_doi(item.get("DOI")), "title": clean_text((item.get("title") or [""])[0]),
                           "journal": clean_text((item.get("container-title") or [""])[0]),
                           "publication_date": _crossref_date(item), "url": f"https://doi.org/{doi}",
                           "article_type": item.get("type", ""), "is_preprint": item.get("type") == "posted-content",
                           "abstract": clean_text(item.get("abstract")), "sources": ["Crossref"],
                           "source_ids": {}, "citation_counts": {"crossref": int(item.get("is-referenced-by-count") or 0)}})
        except Exception as exc:
            errors.append(f"关注论文 DOI {doi} 查询失败：{type(exc).__name__}")
        time.sleep(.15)
    if arxiv:
        try:
            # 与主检索的最后一次 arXiv 调用保持间隔。
            time.sleep(3)
            root = ElementTree.fromstring(text_request(
                "https://export.arxiv.org/api/query?" + urlencode({"id_list": ",".join(arxiv), "max_results": len(arxiv)}),
                timeout=30, retries=4))
            papers.extend(_arxiv_entry(e) for e in root.findall(f"{ATOM_NS}entry"))
        except Exception as exc:
            errors.append(f"关注论文 arXiv 查询失败：{type(exc).__name__}")
    if unavailable:
        errors.append(f"{unavailable} 篇关注论文在当前启用来源中无法检查版本/出版更新；未调用暂停或未选用的来源")
    return papers, errors
