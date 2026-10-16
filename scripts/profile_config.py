#!/usr/bin/env python3
"""读取并验证由用户维护的研究配置。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common import clean_text, load_json, unique_strings
from topic_scope import validate_scope

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_PROFILE = SKILL_DIR / "config" / "research_profile.json"

SOURCE_PRESETS = {
    "general": ["arxiv"],
    "computer_science": ["arxiv"],
    "biomedical": ["europepmc", "biorxiv"],
}
SOURCE_NAMES = {"crossref", "openalex", "arxiv", "europepmc", "biorxiv", "semanticscholar"}


def profile_sources(profile: dict[str, Any], override: str = "auto") -> list[str]:
    if override != "auto":
        sources = unique_strings(override.casefold().split(","))
    elif profile.get("sources"):
        sources = profile["sources"]
    else:
        preset = profile.get("source_preset", "general")
        if preset not in SOURCE_PRESETS:
            raise ProfileError(f"未知来源预设：{preset}")
        sources = SOURCE_PRESETS[preset]
    if not isinstance(sources, list) or not sources or any(value not in SOURCE_NAMES for value in sources):
        raise ProfileError("sources 必须是非空来源标识数组，且不能包含未知来源")
    disabled = profile.get("disabled_sources", [])
    if not isinstance(disabled, list) or any(value not in SOURCE_NAMES for value in disabled):
        raise ProfileError("disabled_sources 必须是有效来源标识数组")
    paused = sorted(set(sources) & set(disabled))
    if paused:
        raise ProfileError(f"来源已暂停：{', '.join(paused)}；恢复前请先修改 disabled_sources")
    return unique_strings(sources)


class ProfileError(ValueError):
    """研究配置不足以安全执行任务时抛出。"""


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def load_profile(path: str | Path = DEFAULT_PROFILE, *, require_configured: bool = True) -> dict[str, Any]:
    profile = load_json(path, None)
    if not isinstance(profile, dict):
        raise ProfileError(f"研究配置不存在或格式无效：{path}")
    if require_configured and not profile.get("configured"):
        raise ProfileError(
            "尚未配置研究主题。请填写 config/research_profile.json，并将 configured 设为 true。"
        )

    profile.setdefault("profile_name", "")
    profile.setdefault("research_context", "")
    profile.setdefault("target_system", "")
    profile.setdefault("priority_questions", [])
    profile.setdefault("queries", [])
    profile.setdefault("modes", {})
    profile.setdefault("topic_groups", [])
    profile.setdefault("cross_topic_bonuses", [])
    profile.setdefault("method_groups", [])
    profile.setdefault("quality_tiers", {})
    profile.setdefault("exclusion_terms", [])
    profile.setdefault("mechanistic_terms", [])
    profile.setdefault("strong_recommendation_rules", [])
    profile.setdefault("roadmap_stages", [])
    profile.setdefault("tags", [])
    if profile.get("reading_order", "score") not in {"score", "venue_first"}:
        raise ProfileError("reading_order 必须是 score 或 venue_first")
    try:
        validate_scope(profile.get("topic_scope"))
    except ValueError as exc:
        raise ProfileError(str(exc)) from exc

    if require_configured:
        profile_sources(profile)
        watchlist = profile.get("watchlist", [])
        if not isinstance(watchlist, list) or any(not isinstance(item, dict) or not (item.get("doi") or item.get("arxiv")) for item in watchlist):
            raise ProfileError("watchlist 必须是包含 doi 或 arxiv 编号的对象数组")
        if not clean_text(profile.get("profile_name")):
            raise ProfileError("configured=true 时必须填写 profile_name")
        if not profile_queries(profile, "all"):
            raise ProfileError("至少需要配置一条默认查询语句")
        groups = topic_groups(profile)
        if not groups:
            raise ProfileError("至少需要配置一个有效的 topic_group")
    return profile


def profile_queries(profile: dict[str, Any], mode: str) -> list[str]:
    if mode == "all":
        queries = list(profile.get("queries") or [])
        configured_mode = (profile.get("modes") or {}).get("all", {})
        if isinstance(configured_mode, dict):
            queries.extend(configured_mode.get("queries") or [])
        elif isinstance(configured_mode, list):
            queries.extend(configured_mode)
        return unique_strings(queries)

    configured_mode = (profile.get("modes") or {}).get(mode)
    if configured_mode is None:
        available = ", ".join(sorted((profile.get("modes") or {}).keys())) or "无"
        raise ProfileError(f"未知的研究配置模式“{mode}”。可用聚焦模式：{available}")
    queries = configured_mode.get("queries") if isinstance(configured_mode, dict) else configured_mode
    return unique_strings(queries or [])


def topic_groups(profile: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for item in _list_of_dicts(profile.get("topic_groups")):
        label = clean_text(item.get("label"))
        terms = unique_strings(item.get("terms") or [])
        try:
            weight = int(item.get("weight") or 0)
        except (TypeError, ValueError):
            continue
        if label and terms and 1 <= weight <= 100:
            groups.append({"label": label, "weight": weight, "terms": terms})
    return groups


def profile_terms(profile: dict[str, Any]) -> list[str]:
    return unique_strings(
        term
        for group in topic_groups(profile)
        for term in group.get("terms") or []
    )


def named_term_groups(profile: dict[str, Any], field: str) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for item in _list_of_dicts(profile.get(field)):
        label = clean_text(item.get("label"))
        terms = unique_strings(item.get("terms") or item.get("keywords") or [])
        if label and terms:
            groups.append({"label": label, "terms": terms})
    return groups
