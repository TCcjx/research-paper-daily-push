from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from summarize_papers import render_report, stars, validate_article  # noqa: E402

INSPIRATION = {
    "experimental_design": ["设置时间序列"],
    "methods": ["RNA-seq"],
    "key_entities_mechanisms": ["CBF 通路"],
    "target_system": ["可迁移到水稻"],
    "future_direction": ["申请相关课题"],
    "paper_potential": ["具备高水平期刊潜力"],
    "limitations_validation": ["仅单一时间点"],
}


def article(**overrides) -> dict:
    """一份能通过严格校验的最小论文。"""
    base = {
        "title": "Cold stress transcriptome of Arabidopsis",
        "chinese_title": "拟南芥低温胁迫转录组",
        "journal": "Test Journal",
        "publication_date": "2026-09-01",
        "doi": "10.1000/test",
        "url": "https://doi.org/10.1000/test",
        "article_type": "研究论文",
        "recommendation_score": 86,
        "score_status": "evidence_reviewed",
        "component_scores": {
            "relevance": 90,
            "novelty": 80,
            "quality": 85,
            "methodology": 88,
            "inspiration": 90,
        },
        "why_worth_reading": "直接回答低温下的转录调控问题。",
        "core_findings": ["发现A", "发现B", "发现C"],
        "term_explanations": [
            {
                "term": "RNA-seq",
                "academic_explanation": "对样本中全部 RNA 进行高通量测序以定量基因表达。",
                "plain_explanation": "给细胞里正在工作的基因拍一张全景快照。",
            }
        ],
        "research_inspiration": dict(INSPIRATION),
        "tags": ["低温", "转录组"],
        "strong_recommendation": False,
        "strong_recommendation_reasons": [],
        "needs_verification": [],
    }
    base.update(overrides)
    return base


def errors(payload_article: dict, *, strict: bool = True, dual: bool = True) -> list[str]:
    return validate_article(payload_article, 0, strict, dual)


def payload(articles: list[dict], ideas: list[str] | None = None, **overrides) -> dict:
    base = {
        "date": "2026-09-01",
        "term_explanation_mode": "dual",
        "metadata": {"retrieved_count": 120, "screened_count": 30},
        "articles": articles,
        "research_ideas": ideas if ideas is not None else ["思路一", "思路二"],
    }
    base.update(overrides)
    return base


class ValidateArticleTests(unittest.TestCase):
    def test_valid_article_produces_no_errors(self) -> None:
        self.assertEqual(errors(article()), [])

    def test_required_fields_cannot_be_blank(self) -> None:
        found = errors(article(journal="   "))
        self.assertTrue(any("journal" in item for item in found))

    def test_recommendation_score_must_be_integer_within_range(self) -> None:
        self.assertTrue(errors(article(recommendation_score=101)))
        self.assertTrue(errors(article(recommendation_score="86")))
        self.assertEqual(errors(article(recommendation_score=0)), [])

    def test_every_component_score_is_validated(self) -> None:
        components = dict(article()["component_scores"])
        components["novelty"] = 120
        found = errors(article(component_scores=components))
        self.assertTrue(any("novelty" in item for item in found))

    def test_why_worth_reading_is_required_and_capped(self) -> None:
        self.assertTrue(errors(article(why_worth_reading="")))
        self.assertTrue(any("100个字符" in item for item in errors(article(why_worth_reading="很" * 101))))
        self.assertEqual(errors(article(why_worth_reading="很" * 100)), [])

    def test_core_findings_must_have_three_to_five_items(self) -> None:
        self.assertTrue(errors(article(core_findings=["只有一条"])))
        self.assertTrue(errors(article(core_findings=["1", "2", "3", "4", "5", "6"])))
        self.assertEqual(errors(article(core_findings=["1", "2", "3", "4", "5"])), [])

    def test_term_explanations_required_only_in_dual_mode(self) -> None:
        self.assertTrue(errors(article(term_explanations=[]), dual=True))
        self.assertEqual(errors(article(term_explanations=[]), dual=False), [])

    def test_duplicate_terms_within_one_article_are_rejected(self) -> None:
        term = {
            "term": "RNA-seq",
            "academic_explanation": "学术解释",
            "plain_explanation": "大白话解释",
        }
        found = errors(article(term_explanations=[term, dict(term)]))
        self.assertTrue(any("重复" in item for item in found))

    def test_academic_and_plain_explanations_must_differ(self) -> None:
        found = errors(
            article(
                term_explanations=[
                    {"term": "RNA-seq", "academic_explanation": "相同的解释", "plain_explanation": "相同的解释"}
                ]
            )
        )
        self.assertTrue(any("不能完全相同" in item for item in found))

    def test_explanations_are_capped_at_180_characters(self) -> None:
        found = errors(
            article(
                term_explanations=[
                    {
                        "term": "RNA-seq",
                        "academic_explanation": "长" * 181,
                        "plain_explanation": "大白话",
                    }
                ]
            )
        )
        self.assertTrue(any("180个字符" in item for item in found))

    def test_all_seven_inspiration_fields_are_required(self) -> None:
        for field in INSPIRATION:
            inspiration = dict(INSPIRATION)
            inspiration[field] = []
            found = errors(article(research_inspiration=inspiration))
            self.assertTrue(any(field in item for item in found), f"{field} 缺失时应当报错")

    def test_tags_must_have_one_to_eight_items(self) -> None:
        self.assertTrue(errors(article(tags=[])))
        self.assertTrue(errors(article(tags=[f"标签{i}" for i in range(9)])))
        self.assertEqual(errors(article(tags=["只有一个"])), [])

    def test_strict_mode_requires_evidence_reviewed_status(self) -> None:
        self.assertTrue(errors(article(score_status="preliminary_metadata_score"), strict=True))
        self.assertEqual(errors(article(score_status="preliminary_metadata_score"), strict=False), [])

    def test_strong_recommendation_requires_at_least_one_reason(self) -> None:
        found = errors(article(strong_recommendation=True, strong_recommendation_reasons=[]))
        self.assertTrue(any("strong_recommendation_reasons" in item for item in found))
        self.assertEqual(
            errors(article(strong_recommendation=True, strong_recommendation_reasons=["证据充分"])), []
        )


class RenderReportTests(unittest.TestCase):
    def test_articles_are_sorted_by_score_descending(self) -> None:
        report = render_report(
            payload(
                [
                    article(chinese_title="低分论文", recommendation_score=72),
                    article(chinese_title="高分论文", doi="10.1000/second", recommendation_score=95),
                ]
            )
        )
        self.assertLess(report.index("高分论文"), report.index("低分论文"))

    def test_top_three_section_uses_medals(self) -> None:
        report = render_report(
            payload([article(chinese_title=f"论文{i}", doi=f"10.1000/{i}") for i in range(4)])
        )
        self.assertIn("今日最值得阅读前三名", report)
        self.assertIn("🥇", report)
        self.assertIn("🥉", report)

    def test_research_ideas_are_limited_to_one_to_three(self) -> None:
        with self.assertRaises(ValueError):
            render_report(payload([article()], ideas=[]))
        with self.assertRaises(ValueError):
            render_report(payload([article()], ideas=[f"思路{i}" for i in range(4)]))

    def test_empty_report_is_allowed_and_mentions_no_recommendation(self) -> None:
        report = render_report(payload([], ideas=[]))
        self.assertIn("今天没有达到推荐阈值且证据充分的论文", report)
        self.assertIn("今日没有符合条件的前三名论文", report)
        self.assertIn("今日暂无足够文献证据形成可靠研究灵感", report)

    def test_strict_mode_caps_articles_at_ten(self) -> None:
        articles = [article(doi=f"10.1000/{i}") for i in range(11)]
        with self.assertRaises(ValueError) as raised:
            render_report(payload(articles))
        self.assertIn("最多只能包含10篇论文", str(raised.exception))

    def test_non_list_articles_raise_type_error(self) -> None:
        with self.assertRaises(TypeError):
            render_report(payload("not a list"))

    def test_warnings_appear_in_report_header(self) -> None:
        report = render_report(payload([article()], metadata={"warnings": ["Crossref 检索失败"]}))
        self.assertIn("检索提示", report)
        self.assertIn("Crossref 检索失败", report)

    def test_dual_mode_is_off_when_not_declared(self) -> None:
        report = render_report(payload([article(term_explanations=[])], term_explanation_mode=""))
        self.assertNotIn("学术名词双重解释", report)

    def test_strong_recommendation_is_highlighted(self) -> None:
        report = render_report(
            payload([article(strong_recommendation=True, strong_recommendation_reasons=["证据充分"])])
        )
        self.assertIn("强烈推荐", report)
        self.assertIn("证据充分", report)

    def test_missing_doi_renders_verification_placeholder(self) -> None:
        report = render_report(payload([article(doi="")]))
        self.assertIn("暂无/需核实", report)

    def test_doi_is_normalized_to_lowercase(self) -> None:
        report = render_report(payload([article(doi="https://doi.org/10.1000/ABC")]))
        self.assertIn("10.1000/abc", report)
        self.assertNotIn("10.1000/ABC", report)


class StarsTests(unittest.TestCase):
    def test_star_count_maps_relevance_to_one_to_five(self) -> None:
        self.assertEqual(stars(0), "★☆☆☆☆")
        self.assertEqual(stars(50), "★★★☆☆")
        self.assertEqual(stars(100), "★★★★★")
        self.assertEqual(stars(999), "★★★★★")

    def test_star_count_never_decreases_as_relevance_grows(self) -> None:
        """曾经因为 round() 的银行家舍入，70/80/90 分都显示4颗星。"""
        counts = [stars(score).count("★") for score in range(0, 101)]
        self.assertEqual(counts, sorted(counts))
        self.assertEqual(counts[0], 1)
        self.assertEqual(counts[100], 5)
        self.assertEqual(counts[70], 4)
        self.assertEqual(counts[90], 5)


if __name__ == "__main__":
    unittest.main()
