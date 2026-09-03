from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import profile_config  # noqa: E402


def valid_profile() -> dict:
    """一份满足全部必填条件的研究配置。"""
    return {
        "configured": True,
        "profile_name": "低温转录组",
        "research_context": "植物低温胁迫下的转录调控",
        "target_system": "拟南芥",
        "priority_questions": ["哪些转录因子率先响应低温"],
        "queries": ["cold stress AND RNA-seq"],
        "topic_groups": [
            {"label": "低温", "weight": 80, "terms": ["cold stress", "low temperature"]},
            {"label": "转录组", "weight": 60, "terms": ["RNA-seq", "transcriptome"]},
        ],
        "modes": {"cold": {"queries": ["cold acclimation"]}},
        "roadmap_stages": [{"label": "机制解析", "keywords": ["knockout"]}],
    }


class ProfileFixtureMixin:
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def write_profile(self, payload: object, name: str = "profile.json") -> str:
        path = self.tmp / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return str(path)


class LoadProfileTests(ProfileFixtureMixin, unittest.TestCase):
    def test_missing_file_is_rejected(self) -> None:
        with self.assertRaises(profile_config.ProfileError) as raised:
            profile_config.load_profile(str(self.tmp / "nope.json"))
        self.assertIn("研究配置不存在或格式无效", str(raised.exception))

    def test_unconfigured_profile_is_rejected(self) -> None:
        path = self.write_profile({"configured": False})
        with self.assertRaises(profile_config.ProfileError) as raised:
            profile_config.load_profile(path)
        self.assertIn("尚未配置研究主题", str(raised.exception))

    def test_non_object_json_is_rejected(self) -> None:
        path = self.write_profile(["not", "an", "object"])
        with self.assertRaises(profile_config.ProfileError):
            profile_config.load_profile(path)

    def test_configured_profile_requires_name(self) -> None:
        payload = valid_profile()
        payload["profile_name"] = "   "
        path = self.write_profile(payload)
        with self.assertRaises(profile_config.ProfileError) as raised:
            profile_config.load_profile(path)
        self.assertIn("profile_name", str(raised.exception))

    def test_configured_profile_requires_at_least_one_query(self) -> None:
        payload = valid_profile()
        payload["queries"] = []
        path = self.write_profile(payload)
        with self.assertRaises(profile_config.ProfileError) as raised:
            profile_config.load_profile(path)
        self.assertIn("查询语句", str(raised.exception))

    def test_configured_profile_requires_valid_topic_group(self) -> None:
        payload = valid_profile()
        payload["topic_groups"] = [{"label": "低温", "weight": 80, "terms": []}]
        path = self.write_profile(payload)
        with self.assertRaises(profile_config.ProfileError) as raised:
            profile_config.load_profile(path)
        self.assertIn("topic_group", str(raised.exception))

    def test_valid_profile_is_accepted_and_defaults_are_filled(self) -> None:
        profile = profile_config.load_profile(self.write_profile(valid_profile()))
        self.assertEqual(profile["profile_name"], "低温转录组")
        for field in ("exclusion_terms", "mechanistic_terms", "tags", "strong_recommendation_rules"):
            self.assertEqual(profile[field], [], f"{field} 应被填充为空列表")

    def test_blank_template_loads_when_configuration_not_required(self) -> None:
        template = Path(SCRIPTS).parent / "config" / "research_profile.json"
        profile = profile_config.load_profile(str(template), require_configured=False)
        self.assertFalse(profile["configured"])
        self.assertEqual(profile["topic_groups"], [])


class QueryTests(unittest.TestCase):
    def test_all_mode_merges_default_queries_and_mode_queries(self) -> None:
        profile = valid_profile()
        profile["modes"]["all"] = {"queries": ["extra query"]}
        queries = profile_config.profile_queries(profile, "all")
        self.assertEqual(queries, ["cold stress AND RNA-seq", "extra query"])

    def test_all_mode_accepts_plain_list(self) -> None:
        profile = valid_profile()
        profile["modes"]["all"] = ["list query"]
        self.assertIn("list query", profile_config.profile_queries(profile, "all"))

    def test_duplicate_queries_are_collapsed(self) -> None:
        profile = valid_profile()
        profile["modes"]["all"] = {"queries": ["cold stress AND RNA-seq"]}
        self.assertEqual(profile_config.profile_queries(profile, "all"), ["cold stress AND RNA-seq"])

    def test_focused_mode_uses_only_its_own_queries(self) -> None:
        queries = profile_config.profile_queries(valid_profile(), "cold")
        self.assertEqual(queries, ["cold acclimation"])

    def test_unknown_mode_lists_available_modes(self) -> None:
        with self.assertRaises(profile_config.ProfileError) as raised:
            profile_config.profile_queries(valid_profile(), "heat")
        self.assertIn("未知的研究配置模式", str(raised.exception))
        self.assertIn("cold", str(raised.exception))


class TermGroupTests(unittest.TestCase):
    def test_topic_group_weight_must_be_between_1_and_100(self) -> None:
        profile = valid_profile()
        profile["topic_groups"] = [
            {"label": "过低", "weight": 0, "terms": ["a"]},
            {"label": "过高", "weight": 101, "terms": ["b"]},
            {"label": "正常", "weight": 50, "terms": ["c"]},
        ]
        groups = profile_config.topic_groups(profile)
        self.assertEqual([group["label"] for group in groups], ["正常"])

    def test_topic_group_ignores_non_object_entries(self) -> None:
        profile = valid_profile()
        profile["topic_groups"] = ["not a dict", {"label": "有效", "weight": 10, "terms": ["x"]}]
        self.assertEqual(len(profile_config.topic_groups(profile)), 1)

    def test_profile_terms_flattens_all_topic_terms(self) -> None:
        terms = profile_config.profile_terms(valid_profile())
        self.assertEqual(
            terms, ["cold stress", "low temperature", "RNA-seq", "transcriptome"]
        )

    def test_named_term_groups_accept_keywords_alias(self) -> None:
        profile = {"roadmap_stages": [{"label": "机制解析", "keywords": ["knockout"]}]}
        groups = profile_config.named_term_groups(profile, "roadmap_stages")
        self.assertEqual(groups, [{"label": "机制解析", "terms": ["knockout"]}])

    def test_named_term_groups_requires_label_and_terms(self) -> None:
        profile = {"roadmap_stages": [{"label": "", "terms": ["a"]}, {"label": "有效", "terms": []}]}
        self.assertEqual(profile_config.named_term_groups(profile, "roadmap_stages"), [])

    def test_unknown_field_returns_empty_list(self) -> None:
        self.assertEqual(profile_config.named_term_groups(valid_profile(), "missing"), [])


if __name__ == "__main__":
    unittest.main()
