from __future__ import annotations

import copy
import sys
import tempfile
import unittest
import warnings
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from research_radar import analyze
from profile_config import profile_sources, ProfileError
from rank_papers import rank_payload, select_review_candidates
from search_papers import collect, search_crossref, search_europepmc
from summarize_papers import render_report
from deduplicate import commit_history, filter_history
from tests.test_report_validation import article, payload

PROFILE = {"profile_name": "test", "topic_groups": [{"label": "T", "weight": 100, "terms": ["topic"]}]}


def paper(index, day="2026-10-16", **extra):
    return {"title": f"topic {index}", "doi": f"10.1000/{index}", "url": f"https://example.org/{index}",
            "publication_date": day, "citation_counts": {"crossref": 10}, **extra}


def raw(papers, **extra):
    return {"date_from": "2026-09-17", "date_to": "2026-10-16", "queries": ["topic"],
            "sources_requested": ["crossref"], "errors": [], "coverage_warnings": [], "papers": papers, **extra}


class RadarTests(unittest.TestCase):
    def test_rate_normalizes_unequal_windows(self):
        data = raw([paper(i) for i in range(7)] + [paper(i+10, "2026-09-20") for i in range(23)])
        radar, _ = analyze(data, PROFILE, {}, today="2026-10-16")
        topic = radar["topics"][0]
        self.assertEqual(topic["daily_rate_ratio"], 1)
        self.assertEqual(topic["status"], "no_clear_rise")

    def test_rising_requires_support_and_healthy_sample(self):
        data = raw([paper(i) for i in range(5)] + [paper(8, "2026-09-20")])
        radar, _ = analyze(data, PROFILE, {}, today="2026-10-16")
        self.assertEqual(radar["topics"][0]["status"], "rising")
        data["coverage_warnings"] = ["truncated"]
        radar, _ = analyze(data, PROFILE, {}, today="2026-10-16")
        self.assertEqual(radar["topics"][0]["status"], "insufficient_data")

    def test_new_topic_without_baseline_is_not_called_rising(self):
        radar, _ = analyze(raw([paper(i) for i in range(4)]), PROFILE, {}, today="2026-10-16")
        self.assertEqual(radar["topics"][0]["status"], "emerging_sample")
        self.assertIsNone(radar["topics"][0]["daily_rate_ratio"])

    def test_watch_only_records_do_not_inflate_topic_activity(self):
        data = raw([paper(i, _watch_only=True) for i in range(10)] + [paper(11, "2026-09-20")])
        radar, _ = analyze(data, PROFILE, {}, today="2026-10-16")
        self.assertEqual(radar["topics"][0]["recent_count"], 0)

    def test_same_day_rerun_replaces_snapshot(self):
        _, state = analyze(raw([paper(1)]), PROFILE, {}, today="2026-10-16")
        _, state = analyze(raw([paper(1), paper(2)]), PROFILE, state, today="2026-10-16")
        self.assertEqual(len(state["snapshots"]), 1)
        self.assertEqual(len(state["snapshots"][0]["records"]), 2)

    def test_citation_growth_only_compares_same_source(self):
        _, state = analyze(raw([paper(1)]), PROFILE, {}, today="2026-10-15")
        radar, _ = analyze(raw([paper(1, citation_counts={"crossref": 13, "openalex": 200})]), PROFILE, state, today="2026-10-16")
        signal = next(iter(radar["paper_signals"].values()))
        self.assertEqual(signal["citation_deltas"], {"crossref": 3})

    def test_changed_queries_do_not_compare_citations(self):
        _, state = analyze(raw([paper(1)]), PROFILE, {}, today="2026-10-15")
        radar, _ = analyze(raw([paper(1)], queries=["different"]), PROFILE, state, today="2026-10-16")
        self.assertEqual(next(iter(radar["paper_signals"].values()))["status"], "unknown")

    def test_metadata_version_update(self):
        _, state = analyze(raw([paper(1, source_ids={"arxiv_version": "1"})]), PROFILE, {}, today="2026-10-15")
        radar, _ = analyze(raw([paper(1, source_ids={"arxiv_version": "2"})]), PROFILE, state, today="2026-10-16")
        self.assertIn("arXiv 版本更新", radar["observed_updates"][0]["changes"])


class RecallTests(unittest.TestCase):
    def test_low_total_score_is_not_a_review_gate(self):
        candidate = paper(1, journal="arXiv", is_preprint=True, abstract="topic")
        result = rank_payload({"papers": [candidate]}, PROFILE)
        self.assertLess(result["review_candidates"][0]["recommendation_score"], 70)

    def test_topic_round_robin_preserves_small_topic(self):
        ranked = [{"title": str(i), "excluded": False, "publication_date": "2026-10-16",
                   "recommendation_score": 90-i, "matched_topics": ["large" if i < 10 else "small"]} for i in range(11)]
        selected = select_review_candidates(ranked, 3)
        self.assertTrue(any(p["matched_topics"] == ["small"] for p in selected))
        self.assertEqual(len(selected), 3)

    def test_source_presets_cover_relevant_domains(self):
        self.assertIn("openalex", profile_sources({"source_preset": "computer_science"}))
        self.assertIn("europepmc", profile_sources({"source_preset": "biomedical"}))
        self.assertEqual(profile_sources({}, "arxiv,crossref"), ["arxiv", "crossref"])
        with self.assertRaises(ProfileError):
            profile_sources({}, "invalid")


class SourceReliabilityTests(unittest.TestCase):
    def test_all_sources_failed_raises(self):
        with patch("search_papers.SOURCE_FUNCTIONS", {"bad": lambda *a: (_ for _ in ()).throw(RuntimeError("offline"))}):
            with self.assertRaises(RuntimeError):
                collect(queries=["topic"], start=date(2026,10,15), end=date(2026,10,16), sources=["bad"], relevance_terms=["topic"])

    def test_partial_sources_preserve_successes_and_limits(self):
        def good(*args):
            warnings.warn("cap reached")
            return [paper(1)]
        with patch("search_papers.SOURCE_FUNCTIONS", {"good": good, "bad": lambda *a: 1/0}):
            result = collect(queries=["topic"], start=date(2026,10,15), end=date(2026,10,16), sources=["good", "bad"], relevance_terms=["topic"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["papers"]), 1)
        self.assertTrue(result["coverage_warnings"])

    def test_crossref_follows_cursor_and_includes_conferences(self):
        batch = [{"DOI": f"10.1000/{i}", "title": [str(i)]} for i in range(100)]
        pages = [{"message": {"items": batch, "next-cursor": "page2"}}, {"message": {"items": []}}]
        with patch("search_papers.json_request", side_effect=pages + [{"message": {"items": []}}]*3) as request, patch("search_papers.time.sleep"):
            result = search_crossref(["topic"], date(2026,10,15), date(2026,10,16))
        self.assertEqual(len(result), 100)
        self.assertIn("cursor=page2", request.call_args_list[1].args[0])
        self.assertTrue(any("proceedings-article" in call.args[0] for call in request.call_args_list))

    def test_europepmc_follows_cursor(self):
        pages = [{"hitCount": 2, "nextCursorMark": "page2", "resultList": {"result": [{"title": "A"}]}},
                 {"hitCount": 2, "nextCursorMark": "end", "resultList": {"result": [{"title": "B"}]}}]
        with patch("search_papers.json_request", side_effect=pages) as request, patch("search_papers.time.sleep"):
            result = search_europepmc(["topic"], date(2026,10,15), date(2026,10,16))
        self.assertEqual(len(result), 2)
        self.assertIn("cursorMark=page2", request.call_args_list[1].args[0])


class ReportSafetyTests(unittest.TestCase):
    def test_strict_report_rejects_low_score(self):
        with self.assertRaises(ValueError):
            render_report(payload([article(recommendation_score=69)]))

    def test_duplicate_doi_and_title_are_rejected(self):
        for second in (article(title="Different"), article(doi="10.1000/other")):
            with self.assertRaises(ValueError):
                render_report(payload([article(), second]))

    def test_evidence_and_comparison_are_required(self):
        for overrides in ({"evidence": []}, {"assessment": {}}, {"publication_date": "invalid"}, {"url": "javascript:alert(1)"}):
            with self.assertRaises(ValueError):
                render_report(payload([article(**overrides)]))

    def test_score_must_match_components(self):
        item = article()
        item["recommendation_score"] = 100
        with self.assertRaises(ValueError):
            render_report(payload([item]))

    def test_brief_report_shows_window_contribution_and_evidence(self):
        report = render_report(payload([article()], term_explanation_mode="off", metadata={"window": "最近7天"}))
        for term in ("最近7天", "主要贡献", "相较已有工作", "证据（abstract）", "研究热点线索", "持续跟踪"):
            self.assertIn(term, report)
        self.assertNotIn("学术名词双重解释", report)

    def test_delivered_update_advances_history(self):
        old = paper(1, journal="arXiv", is_preprint=True)
        new = paper(1, journal="Journal", is_preprint=False, article_type="article")
        history, _ = commit_history([old], [], delivery="local-report", pushed_date="2026-10-15")
        self.assertEqual(filter_history([new], history)["update_count"], 1)
        history, count = commit_history([new], history, delivery="local-report", pushed_date="2026-10-16")
        self.assertEqual(count, 0)
        self.assertEqual(filter_history([new], history)["update_count"], 0)

    def test_formal_publication_does_not_regress_to_preprint(self):
        formal = paper(1, journal="Journal", is_preprint=False, article_type="article")
        preprint = paper(1, journal="arXiv", is_preprint=True, article_type="preprint")
        self.assertEqual(filter_history([preprint], [formal])["update_count"], 0)
