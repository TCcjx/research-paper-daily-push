"""用偏题但高关键词得分的样本验证主题和暂停来源约束。"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from common import atomic_write_json, load_json
from profile_config import load_profile, profile_sources, ProfileError
from topic_scope import assess_scope, term_matches
from rank_papers import rank_payload
from search_papers import collect
from research_radar import analyze
from watch_papers import check_watchlist
from summarize_papers import render_report
import run_daily
from tests.test_report_validation import article, payload

PROFILE = load_profile(ROOT / "examples/research_profile.temporal-kg.json")
SCOPE = PROFILE["topic_scope"]


def paper(title, day="2026-10-16", abstract=""):
    return {"title": title, "abstract": abstract, "publication_date": day,
            "url": "https://example.org/paper", "article_type": "preprint",
            "journal": "arXiv", "is_preprint": True}


class ScopeTests(unittest.TestCase):
    def test_core_tasks_and_abbreviations_pass(self):
        for title in ("Agent planning for temporal knowledge graph reasoning",
                      "Temporal knowledge graphs for link prediction", "TKGR with agents",
                      "A benchmark for TKGQA", "时态知识图谱问答"):
            with self.subTest(title=title):
                self.assertTrue(assess_scope(paper(title), SCOPE)["in_scope"])

    def test_agent_memory_static_graph_and_general_temporal_work_fail(self):
        for title in ("Agent memory and graph retrieval for reasoning",
                      "Agents for static knowledge graph reasoning",
                      "Temporal graph neural networks for traffic forecasting",
                      "Temporal knowledge graph storage and indexing",
                      "Chemical agents for soil conditioning"):
            with self.subTest(title=title):
                self.assertFalse(assess_scope(paper(title), SCOPE)["in_scope"])

    def test_abstract_can_establish_scope_but_review_claims_cannot(self):
        p = paper("A new agent system", abstract="Temporal knowledge graph reasoning with tools.")
        self.assertTrue(assess_scope(p, SCOPE)["in_scope"])
        p["abstract"] = ""
        p["why_worth_reading"] = "Can transfer to temporal knowledge graph reasoning"
        self.assertFalse(assess_scope(p, SCOPE)["in_scope"])

    def test_word_matching_avoids_reagent_and_supports_variants(self):
        self.assertFalse(term_matches("reagent and agency", "agent"))
        self.assertTrue(term_matches("Agents use time aware knowledge graphs", "agent"))
        self.assertTrue(term_matches("time aware", "time-aware"))

    def test_high_proxy_score_cannot_override_scope(self):
        p = paper("Agent planning and knowledge graph reasoning without time modeling",
                  abstract="Large language model agent memory with tool use and code is available.")
        ranked = rank_payload({"papers": [p]}, PROFILE)
        self.assertTrue(ranked["ranked_papers"][0]["excluded"])
        self.assertEqual(ranked["review_candidates"], [])

    def test_collection_and_radar_exclude_off_topic_before_counting(self):
        core = paper("Agent temporal knowledge graph reasoning")
        noise = [paper(f"Agent memory reasoning study {i}") for i in range(14)]
        with patch("search_papers.SOURCE_FUNCTIONS", {"arxiv": lambda *args: [core] + noise}):
            raw = collect(queries=["topic"], start=date(2026,9,17), end=date(2026,10,16),
                          sources=["arxiv"], relevance_terms=["agent"], topic_scope=SCOPE)
        self.assertEqual(raw["unique_relevant_count"], 1)
        self.assertEqual(raw["off_topic_count"], 14)
        self.assertEqual(len(raw["off_topic_papers"]), 14)
        # 直接分析未筛原始数据也不能让相邻论文污染热点。
        radar, state = analyze({**raw, "papers": [core] + noise}, PROFILE, {}, today="2026-10-16")
        self.assertEqual(len(state["snapshots"][0]["records"]), 1)
        self.assertTrue(all(t["recent_count"] <= 1 for t in radar["topics"]))

    def test_default_sources_and_disabled_overrides(self):
        for preset in ("general", "computer_science", "biomedical"):
            self.assertNotIn("openalex", profile_sources({"source_preset": preset}))
        self.assertEqual(profile_sources(PROFILE), ["arxiv", "semanticscholar", "crossref"])
        for source in ("openalex",):
            with self.assertRaises(ProfileError):
                profile_sources(PROFILE, source)

    def test_paused_source_not_called_by_watchlist(self):
        with patch("watch_papers.json_request") as request, patch("watch_papers.text_request") as arxiv:
            papers, warnings = check_watchlist([{"doi": "10.1000/only-doi"}], sources=["arxiv"])
        request.assert_not_called()
        arxiv.assert_not_called()
        self.assertEqual(papers, [])
        self.assertTrue(warnings)

    def test_report_rejects_off_topic_even_with_valid_evidence_fields(self):
        with self.assertRaises(ValueError):
            render_report(payload([article(title="Generic agent memory reasoning")], topic_scope=SCOPE))
        text = render_report(payload([article(title="Agent temporal knowledge graph reasoning")], topic_scope=SCOPE))
        self.assertIn(SCOPE["name"], text)


class ScopedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / "profile.json"
        atomic_write_json(self.profile, PROFILE)

    def test_today_noise_does_not_prevent_same_topic_window_expansion(self):
        items = [paper(f"Agent memory reasoning study {i}") for i in range(14)]
        items += [paper("Agent temporal knowledge graph reasoning", "2026-10-05")]
        args = run_daily.parse_args(["collect", "--sources", "arxiv", "--profile", str(self.profile), "--mode", "agent_tkg",
            "--to-date", "2026-10-16", "--output-dir", str(self.root / "run"),
            "--history", str(self.root / "history.json"), "--memory", str(self.root / "memory.json"),
            "--observations", str(self.root / "observations.json"), "--watch-limit", "0"])
        with patch("search_papers.SOURCE_FUNCTIONS", {"arxiv": lambda *args: items}), contextlib.redirect_stdout(io.StringIO()):
            queue = load_json(run_daily.collect_stage(args))
        self.assertEqual(len(queue["candidates"]), 1)
        self.assertEqual(queue["candidates"][0]["reading_lane"], "supplementary")
        self.assertIn("最近30天", queue["metadata"]["window"])
        self.assertEqual(queue["metadata"]["sources_requested"], ["arxiv"])
        self.assertEqual(queue["metadata"]["off_topic_count"], 14)
        self.assertEqual(queue["topic_scope"], SCOPE)
        # 同一范围经 prepare、真实渲染子进程一直传到本地报告。
        reviewed = self.root / "reviewed.json"
        run_daily.prepare_review(run_daily.parse_args(["prepare", "--queue", str(self.root / "run/review_queue.json"),
                                                       "--output", str(reviewed)]))
        draft = load_json(reviewed)
        self.assertEqual(draft["topic_scope"], SCOPE)
        draft["articles"] = [article(title=items[-1]["title"], publication_date="2026-10-05")]
        atomic_write_json(reviewed, draft)
        report = self.root / "report.md"
        with contextlib.redirect_stdout(io.StringIO()):
            run_daily.deliver_stage(run_daily.parse_args(["deliver", "--profile", str(self.profile),
                "--reviewed", str(reviewed), "--queue", str(self.root / "run/review_queue.json"),
                "--report", str(report), "--skip-push"]))
        self.assertIn(SCOPE["name"], report.read_text(encoding="utf-8"))
        self.assertFalse((self.root / "history.json").exists())

    def test_invalid_scope_stops_before_any_network_request(self):
        request = patch("search_papers.SOURCE_FUNCTIONS", {"arxiv": lambda *args: self.fail("不应联网")})
        with request, self.assertRaises(ValueError):
            collect(queries=["topic"], start=date(2026,9,17), end=date(2026,10,16),
                    sources=["arxiv"], relevance_terms=["topic"],
                    topic_scope={"name": "TKG", "required_groups": []})

    def test_deliver_checks_current_profile_without_queue(self):
        reviewed = self.root / "reviewed.json"
        atomic_write_json(reviewed, payload([article(title="Generic agent reasoning")]))
        args = run_daily.parse_args(["deliver", "--profile", str(self.profile), "--reviewed", str(reviewed),
            "--report", str(self.root / "report.md"), "--skip-push"])
        with patch.object(run_daily, "run_script") as renderer, self.assertRaises(ValueError):
            run_daily.deliver_stage(args)
        renderer.assert_not_called()

    def test_reworded_review_cannot_override_off_topic_original_queue(self):
        reviewed, queue = self.root / "reviewed.json", self.root / "queue.json"
        original = paper("Static knowledge graph reasoning")
        original["doi"] = "10.1000/test"
        atomic_write_json(queue, {"status": "ok", "date": "2026-09-01", "candidates": [original],
                                  "topic_scope": SCOPE})
        atomic_write_json(reviewed, payload([article(title="Temporal knowledge graph reasoning")]))
        args = run_daily.parse_args(["deliver", "--profile", str(self.profile), "--reviewed", str(reviewed),
            "--queue", str(queue), "--report", str(self.root / "report.md"), "--skip-push"])
        with patch.object(run_daily, "run_script") as renderer, self.assertRaises(ValueError):
            run_daily.deliver_stage(args)
        renderer.assert_not_called()
