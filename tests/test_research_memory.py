from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import update_research_memory as memory_module  # noqa: E402

PROFILE = {
    "roadmap_stages": [
        {"label": "机制解析", "terms": ["knockout", "gene editing"]},
        {"label": "组学筛选", "terms": ["RNA-seq"]},
    ]
}


def reviewed_article(**overrides) -> dict:
    """一份符合已复核 JSON 约定的最小论文。"""
    base = {
        "title": "Cold stress transcriptome of Arabidopsis",
        "chinese_title": "拟南芥低温胁迫转录组",
        "doi": "10.1000/test",
        "publication_date": "2026-09-01",
        "recommendation_score": 86,
        "tags": ["低温", "转录组"],
        "term_explanations": [{"term": "RNA-seq"}],
        "strong_recommendation": True,
        "why_worth_reading": "直接回答低温下的转录调控问题。",
        "needs_verification": ["样本量需要核实"],
        "core_findings": ["发现A", "发现B", "发现C"],
        "research_inspiration": {
            "experimental_design": ["用 knockout 株系做验证"],
            "methods": ["RNA-seq"],
            "key_entities_mechanisms": ["CBF 通路"],
            "target_system": ["可迁移到水稻"],
            "future_direction": ["申请相关课题"],
            "paper_potential": ["具备高水平期刊潜力"],
            "limitations_validation": ["仅单一时间点"],
        },
    }
    base.update(overrides)
    return base


def report_payload(date: str, articles: list[dict], ideas: list[str] | None = None) -> dict:
    return {"date": date, "articles": articles, "research_ideas": ideas or []}


class LoadMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def write(self, name: str, payload: object) -> str:
        path = self.tmp / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def test_missing_file_returns_empty_memory(self) -> None:
        memory = memory_module.load_memory(str(self.tmp / "nope.json"))
        self.assertEqual(memory["papers"], [])
        self.assertEqual(memory["learned_reports"], [])
        self.assertIn("不等于用户明确表示喜欢", memory["scope_note"])

    def test_non_object_json_falls_back_to_empty_memory(self) -> None:
        memory = memory_module.load_memory(self.write("bad.json", ["not", "an", "object"]))
        self.assertEqual(memory["version"], 1)
        self.assertEqual(memory["research_ideas"], [])

    def test_partial_memory_is_backfilled(self) -> None:
        memory = memory_module.load_memory(self.write("partial.json", {"version": 1}))
        self.assertEqual(memory["papers"], [])
        self.assertEqual(memory["explicit_preferences"]["liked_topics"], [])

    def test_tampered_scope_note_is_restored(self) -> None:
        memory = memory_module.load_memory(
            self.write("tampered.json", {"version": 1, "scope_note": "被改写了"})
        )
        self.assertEqual(memory["scope_note"], memory_module.SCOPE_NOTE)


class LearnReportTests(unittest.TestCase):
    def test_first_learn_records_report_paper_and_signals(self) -> None:
        memory = memory_module.empty_memory()
        memory, stats = memory_module.learn_report(
            report_payload("2026-09-01", [reviewed_article()]),
            memory,
            delivery="wxpusher",
            profile=PROFILE,
        )
        self.assertTrue(stats["report_added"])
        self.assertEqual(stats["new_papers"], 1)
        self.assertEqual(stats["total_reports"], 1)
        self.assertEqual(memory["papers"][0]["roadmap_stages"], ["机制解析", "组学筛选"])
        self.assertEqual(
            memory["signals"],
            {
                "tag_counts": {"低温": 1, "转录组": 1},
                "key_term_counts": {"RNA-seq": 1},
                "roadmap_stage_counts": {"机制解析": 1, "组学筛选": 1},
                "strong_recommendation_count": 1,
                "unresolved_validation_count": 1,
            },
        )

    def test_learning_the_same_report_twice_is_idempotent(self) -> None:
        memory = memory_module.empty_memory()
        payload = report_payload("2026-09-01", [reviewed_article()], ["思路一"])
        memory, _ = memory_module.learn_report(payload, memory, delivery="local-report", profile=PROFILE)
        memory, stats = memory_module.learn_report(payload, memory, delivery="local-report", profile=PROFILE)
        self.assertFalse(stats["report_added"])
        self.assertEqual(stats["total_reports"], 1)
        self.assertEqual(stats["total_papers"], 1)
        self.assertEqual(len(memory["research_ideas"]), 1)

    def test_repeated_paper_is_merged_and_dates_are_tracked(self) -> None:
        memory = memory_module.empty_memory()
        memory, _ = memory_module.learn_report(
            report_payload("2026-09-01", [reviewed_article()]), memory, delivery="local-report", profile=PROFILE
        )
        memory, stats = memory_module.learn_report(
            report_payload("2026-09-02", [reviewed_article(recommendation_score=92)], ["思路二"]),
            memory,
            delivery="local-report",
            profile=PROFILE,
        )
        self.assertEqual(stats["new_papers"], 0)
        self.assertEqual(stats["total_papers"], 1)
        self.assertEqual(stats["total_reports"], 2)
        paper = memory["papers"][0]
        self.assertEqual(paper["first_report_date"], "2026-09-01")
        self.assertEqual(paper["last_report_date"], "2026-09-02")
        self.assertEqual(paper["recommendation_score"], 92)

    def test_identical_idea_on_same_date_is_not_duplicated(self) -> None:
        memory = memory_module.empty_memory()
        payload = report_payload("2026-09-01", [reviewed_article()], ["同一条思路", "同一条思路"])
        memory, _ = memory_module.learn_report(payload, memory, delivery="local-report", profile=PROFILE)
        self.assertEqual(len(memory["research_ideas"]), 1)

    def test_zero_recommendation_report_still_records_the_run(self) -> None:
        memory = memory_module.empty_memory()
        memory, stats = memory_module.learn_report(
            report_payload("2026-09-01", []), memory, delivery="local-report", profile=PROFILE
        )
        self.assertTrue(stats["report_added"])
        self.assertEqual(stats["total_papers"], 0)
        self.assertEqual(memory["learned_reports"][0]["article_count"], 0)

    def test_signals_are_recomputed_from_scratch_every_time(self) -> None:
        memory = memory_module.empty_memory()
        memory["signals"]["tag_counts"] = {"陈旧标签": 99}
        memory, _ = memory_module.learn_report(
            report_payload("2026-09-01", [reviewed_article()]), memory, delivery="local-report", profile=PROFILE
        )
        self.assertNotIn("陈旧标签", memory["signals"]["tag_counts"])

    def test_explicit_preferences_survive_learning(self) -> None:
        memory = memory_module.empty_memory()
        memory["explicit_preferences"]["liked_topics"] = ["低温驯化"]
        memory, _ = memory_module.learn_report(
            report_payload("2026-09-01", [reviewed_article()]), memory, delivery="local-report", profile=PROFILE
        )
        self.assertEqual(memory["explicit_preferences"]["liked_topics"], ["低温驯化"])

    def test_paper_without_doi_uses_title_identity(self) -> None:
        memory = memory_module.empty_memory()
        memory, _ = memory_module.learn_report(
            report_payload("2026-09-01", [reviewed_article(doi="")]), memory, delivery="local-report", profile=PROFILE
        )
        self.assertTrue(memory["papers"][0]["identity_key"].startswith("title:"))


class BuildContextTests(unittest.TestCase):
    def _memory_with_two_papers(self) -> dict:
        memory = memory_module.empty_memory()
        memory, _ = memory_module.learn_report(
            report_payload(
                "2026-09-01",
                [
                    reviewed_article(recommendation_score=70),
                    reviewed_article(
                        doi="10.1000/second",
                        title="Second paper",
                        chinese_title="第二篇",
                        recommendation_score=95,
                    ),
                ],
                ["思路一", "思路二", "思路三"],
            ),
            memory,
            delivery="local-report",
            profile=PROFILE,
        )
        return memory

    def test_recent_limit_is_respected(self) -> None:
        memory = self._memory_with_two_papers()
        context = memory_module.build_context(memory, recent=1)
        self.assertEqual(len(context["recent_high_value_papers"]), 1)
        self.assertEqual(len(context["recent_research_ideas"]), 1)

    def test_context_carries_counts_and_scope_note(self) -> None:
        context = memory_module.build_context(self._memory_with_two_papers())
        self.assertEqual(context["learned_paper_count"], 2)
        self.assertEqual(context["learned_report_count"], 1)
        self.assertEqual(context["strong_recommendation_count"], 2)
        self.assertEqual(context["scope_note"], memory_module.SCOPE_NOTE)
        self.assertIn("不得据此降低70分阈值", context["use_guidance"])

    def test_highest_scored_paper_comes_first(self) -> None:
        context = memory_module.build_context(self._memory_with_two_papers())
        self.assertEqual(context["recent_high_value_papers"][0]["score"], 95)

    def test_empty_memory_produces_empty_context(self) -> None:
        context = memory_module.build_context(memory_module.empty_memory())
        self.assertEqual(context["recent_high_value_papers"], [])
        self.assertEqual(context["top_tags"], [])

    def test_report_id_is_stable_and_date_sensitive(self) -> None:
        first = memory_module.report_id(report_payload("2026-09-01", [reviewed_article()]))
        again = memory_module.report_id(report_payload("2026-09-01", [reviewed_article()]))
        other = memory_module.report_id(report_payload("2026-09-02", [reviewed_article()]))
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)
        self.assertEqual(len(first), 20)


if __name__ == "__main__":
    unittest.main()
