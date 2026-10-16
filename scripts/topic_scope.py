"""可配置的主题准入：所有必要概念均须在题名或摘要中出现。"""
from __future__ import annotations

import re
from common import clean_text


def validate_scope(scope: dict | None) -> None:
    if scope is None or scope == {}:
        return
    if not isinstance(scope, dict) or not clean_text(scope.get("name")):
        raise ValueError("topic_scope 必须是包含 name 的对象")
    groups = scope.get("required_groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("topic_scope.required_groups 必须包含必要概念组")
    labels = set()
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("label"), str) or not group["label"].strip():
            raise ValueError("topic_scope 概念组必须填写 label")
        terms = group.get("terms")
        if not isinstance(terms, list) or not terms or any(not isinstance(t, str) or not t.strip() for t in terms):
            raise ValueError("topic_scope 概念组 terms 必须是非空短语数组")
        if group["label"] in labels:
            raise ValueError("topic_scope 概念组 label 不能重复")
        labels.add(group["label"])


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[-‐‑–—]", " ", clean_text(text).casefold())).strip()


def term_matches(text: str, term: str) -> bool:
    text, term = _normalized(text), _normalized(term)
    if not term:
        return False
    # 英文按词边界匹配，避免 agent 命中 reagent；兼容 graph(s) 等复数。
    left = r"(?<![a-z0-9])" if term[0].isascii() and term[0].isalnum() else ""
    right = r"(?:s)?(?![a-z0-9])" if term[-1].isascii() and term[-1].isalnum() else ""
    return bool(re.search(left + re.escape(term) + right, text))


def assess_scope(paper: dict, scope: dict | None) -> dict:
    validate_scope(scope)
    if not scope:
        return {"in_scope": True, "name": "", "matched_groups": {}, "missing_groups": []}
    text = f"{paper.get('title', '')} {paper.get('abstract', '')}"
    matched = {g["label"]: [t for t in g["terms"] if term_matches(text, t)]
               for g in scope["required_groups"]}
    missing = [label for label, terms in matched.items() if not terms]
    return {"in_scope": not missing, "name": scope["name"],
            "matched_groups": matched, "missing_groups": missing}


def require_scope(paper: dict, scope: dict | None) -> None:
    result = assess_scope(paper, scope)
    if not result["in_scope"]:
        raise ValueError(f"论文不符合主题 {result['name']}：{paper.get('title', '')}；"
                         f"题名/摘要缺少：{'、'.join(result['missing_groups'])}。请核实原文摘要，不能以可迁移为由纳入。")
