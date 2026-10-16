"""最终推荐的场所门槛；待核实与不达标分开保留，不从分数推断等级。"""
from __future__ import annotations

from urllib.parse import urlparse
from common import clean_text
from venue_ranking import classify, normalize_venue, reading_sort_key
from topic_scope import term_matches


def valid_url(value) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_policy(policy) -> None:
    if policy is None or policy == {}:
        return
    if not isinstance(policy, dict) or type(policy.get("enabled")) is not bool:
        raise ValueError("publication_filter 必须包含布尔 enabled")
    if policy.get("conference_min_ccf", "B") not in {"A", "B", "C"}:
        raise ValueError("conference_min_ccf 必须是 A/B/C")
    systems = policy.get("journal_systems", ["cas", "jcr"])
    if not isinstance(systems, list) or not systems or any(x not in {"cas", "jcr"} for x in systems):
        raise ValueError("journal_systems 必须为 cas/jcr 的非空数组")
    if type(policy.get("journal_max_quartile", 2)) is not int or not 1 <= policy.get("journal_max_quartile", 2) <= 4:
        raise ValueError("journal_max_quartile 必须是1–4整数")


def _decision(status, reason, **details):
    return {"status": status, "reason": reason, **details}


def evaluate_publication(paper: dict, policy: dict | None, ranking: dict, *, year: int) -> dict:
    validate_policy(policy)
    if not policy or not policy.get("enabled"):
        return _decision("eligible", "未启用最终场所门槛")
    record = paper.get("venue_evidence") or {}
    if not isinstance(record, dict):
        record = {}
    proven = (record.get("verified") is True and valid_url(record.get("url"))
              and record.get("publication_type") in {"full", "regular", "journal"}
              and record.get("status") in {"accepted", "published"})
    # arXiv只是载体。官方录用/发表记录核实后，可按真实场所评级，保留载体身份。
    venue = clean_text(record.get("venue")) if proven else ""
    venue = venue or clean_text(paper.get("journal"))
    effective = dict(paper)
    if proven:
        effective.update(journal=venue, is_preprint=False,
                         article_type="journal-article" if record["publication_type"] == "journal" else "proceedings-article")
    c = classify(effective, ranking)
    kind = c.get("kind")
    article_type = clean_text(effective.get("article_type")).casefold()
    if not kind:
        if "conference" in article_type or "proceedings" in article_type:
            kind = "conference"
        elif not effective.get("is_preprint") and ("journal" in article_type or "article" in article_type or article_type == "研究论文"):
            kind = "journal"
    details = {"kind": kind, "venue": venue, "classification": c}
    # 预印本可通过官方记录补充真实场所；已有正式场所则须对应同一刊会。
    original = classify(paper, ranking)
    original_venue = original.get("venue") or paper.get("journal")
    if proven and not paper.get("is_preprint") and normalize_venue(original_venue) != normalize_venue(c.get("venue") or venue):
        return _decision("pending", "官方记录场所与原论文场所不一致，需人工核实", **details)
    if kind == "conference":
        grade = c.get("ccf") if c.get("verified_venue") else None
        order = {"A": 3, "B": 2, "C": 1}
        if grade and order[grade] < order[policy.get("conference_min_ccf", "B")]:
            return _decision("excluded", f"会议CCF-{grade}低于门槛", **details)
        if c.get("status") == "unlisted" and any(t in normalize_venue(venue + ' ' + clean_text(paper.get('paper_track'))) for t in ("workshop", "findings", "short paper", "demo")):
            return _decision("excluded", "附属卷或非正式长文不能继承主会等级", **details)
        if not grade or not proven or record.get("publication_type") not in {"full", "regular"}:
            return _decision("pending", "需核实CCF等级及主会长文的官方录用/发表记录", **details)
        return _decision("eligible", f"已核实CCF-{grade}主会长文", **details)
    if kind != "journal":
        return _decision("pending", "发表场所或文章类型未核实；不把arXiv载体当成场所评级", **details)
    records = paper.get("journal_quartiles") or []
    if not isinstance(records, list):
        return _decision("pending", "journal_quartiles 必须是分区证据数组", **details)
    accepted, checked = [], set()
    for item in records:
        if not isinstance(item, dict):
            continue
        system, quartile, edition = item.get("system"), item.get("quartile"), item.get("year")
        if (system not in policy.get("journal_systems", ["cas", "jcr"])
                or item.get("verified") is not True or not valid_url(item.get("source_url"))
                or type(quartile) is not int or not 1 <= quartile <= 4
                or type(edition) is not int or not 1900 <= edition <= year
                or not clean_text(item.get("category"))
                or normalize_venue(item.get("venue")) != normalize_venue(venue)):
            continue
        checked.add(system)
        if quartile <= policy.get("journal_max_quartile", 2):
            accepted.append(item)
    if accepted and proven and record.get("publication_type") == "journal":
        return _decision("eligible", "中科院一区/二区或JCR Q1/Q2至少一套已核实达标", quartile_evidence=accepted, **details)
    if set(policy.get("journal_systems", ["cas", "jcr"])) <= checked and not accepted:
        return _decision("excluded", "已核实的分区均低于期刊门槛", **details)
    return _decision("pending", "期刊正式记录或中科院/JCR达标分区尚未核实", **details)


def agent_match(paper: dict, terms: list[str]) -> dict:
    text = f"{paper.get('title', '')} {paper.get('abstract', '')}"
    matches = [t for t in terms if term_matches(text, t)]
    return {"candidate": bool(matches), "matched_terms": matches}


def validate_agent_assessment(paper: dict) -> None:
    value = paper.get("agent_assessment")
    if not isinstance(value, dict) or type(value.get("integrated")) is not bool:
        raise ValueError("agent_assessment.integrated 须在复核后明确填写 true/false")
    if value["integrated"]:
        urls = {item.get("url") for item in (paper.get("evidence") or []) if isinstance(item, dict)}
        if not clean_text(value.get("role")) or not valid_url(value.get("evidence_url")) or value["evidence_url"] not in urls:
            raise ValueError("Agent结合须说明具体作用，并引用本篇已阅读的证据URL")


def agent_sort_key(paper: dict, *, reviewed: bool = False) -> tuple:
    integrated = ((paper.get("agent_assessment") or {}).get("integrated") is True if reviewed
                  else (paper.get("agent_match") or {}).get("candidate", False))
    rest = reading_sort_key(paper)
    decision = paper.get("publication_decision") or {}
    if decision.get("status") == "eligible":
        if decision.get("kind") == "conference":
            priority = {"A": 6, "B": 4, "C": 2}.get((decision.get("classification") or {}).get("ccf"), rest[0])
        else:
            quartiles = [v["quartile"] for v in decision.get("quartile_evidence", [])]
            priority = {1: 5, 2: 3, 3: 1, 4: 0}.get(min(quartiles), rest[0]) if quartiles else rest[0]
        rest = (priority, *rest[1:])
    return (integrated, *rest)


def partition_publications(papers: list[dict], policy: dict, ranking: dict, *, year: int):
    groups = {"eligible": [], "excluded": [], "pending": []}
    for source in papers:
        paper = dict(source)
        decision = evaluate_publication(paper, policy, ranking, year=year)
        paper["publication_decision"] = decision
        groups[decision["status"]].append(paper)
    return groups
