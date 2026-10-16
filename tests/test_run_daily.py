from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_daily  # noqa: E402

REVIEWED = {
    "date": "2026-09-01",
    "term_explanation_mode": "dual",
    "metadata": {"retrieved_count": 120, "screened_count": 30},
    "articles": [],
    "research_ideas": [],
}


class ParseArgsTests(unittest.TestCase):
    def test_collect_defaults_match_documented_behaviour(self) -> None:
        args = run_daily.parse_args(["collect"])
        self.assertEqual(args.command, "collect")
        self.assertEqual(args.mode, "all")
        self.assertEqual(args.threshold, 70)
        self.assertEqual(args.min_results, 5)
        self.assertEqual(args.max_results, 5)
        self.assertEqual(args.sources, "auto")

    def test_opt_in_sources_are_off_by_default_but_still_acceptable(self) -> None:
        default_sources = run_daily.parse_args(["collect"]).sources.split(",")
        for source in ("openalex", "semanticscholar"):
            self.assertNotIn(source, default_sources)
        opted_in = run_daily.parse_args(
            ["collect", "--sources", "openalex,semanticscholar,europepmc"]
        )
        self.assertEqual(
            opted_in.sources, "openalex,semanticscholar,europepmc"
        )

    def test_deliver_requires_reviewed_and_report(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run_daily.parse_args(["deliver"])

    def test_deliver_rejects_unknown_provider(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run_daily.parse_args(
                ["deliver", "--reviewed", "a.json", "--report", "b.md", "--provider", "sms"]
            )


class MainValidationTests(unittest.TestCase):
    def test_min_results_cannot_exceed_candidate_limit(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            run_daily.main(["collect", "--min-results", "20", "--candidate-limit", "5"])
        self.assertIn("--min-results <= --candidate-limit", str(raised.exception))

    def test_argument_errors_are_reported_before_profile_errors(self) -> None:
        """配置缺失不应该掩盖明显的用法错误。"""
        with self.assertRaises(SystemExit) as raised:
            run_daily.main(["collect", "--min-results", "20", "--candidate-limit", "5"])
        self.assertNotIn("尚未配置研究主题", str(raised.exception))

    def test_unconfigured_profile_stops_the_run(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            run_daily.main(["collect"])
        self.assertIn("尚未配置研究主题", str(raised.exception))


class CollectStageTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)
        self.profile = self.tmp / "profile.json"
        self.profile.write_text(json.dumps({"configured": True, "profile_name": "fixture",
            "queries": ["topic"], "topic_groups": [{"label": "T", "weight": 100, "terms": ["topic"]}]}), encoding="utf-8")

    def collect(self, papers, **raw_overrides):
        raw = {"date_from": "2026-09-17", "date_to": "2026-10-16", "queries": ["topic"],
               "sources_requested": ["crossref"], "source_counts": {"crossref": len(papers)},
               "retrieved_count": len(papers), "unique_relevant_count": len(papers),
               "status": "ok", "errors": [], "coverage_warnings": [], "papers": papers}
        raw.update(raw_overrides)
        args = run_daily.parse_args(["collect", "--profile", str(self.profile), "--to-date", "2026-10-16",
            "--output-dir", str(self.tmp / "run"), "--history", str(self.tmp / "history.json"),
            "--memory", str(self.tmp / "memory.json"), "--observations", str(self.tmp / "observations.json"),
            "--watch-limit", "0", "--sources", "crossref"])
        with patch.object(run_daily, "collect", return_value=raw) as search, contextlib.redirect_stdout(io.StringIO()):
            path = run_daily.collect_stage(args)
        self.assertEqual(search.call_count, 1)
        return json.loads(path.read_text(encoding="utf-8"))

    def paper(self, index, published):
        return {"title": f"topic {index}", "doi": f"10.1000/{index}", "publication_date": published,
                "url": f"https://example.org/{index}", "journal": "arXiv", "is_preprint": True,
                "abstract": "topic study", "article_type": "preprint"}

    def test_highly_relevant_low_proxy_score_still_enters_review(self):
        queue = self.collect([self.paper(1, "2026-10-16")])
        self.assertEqual(len(queue["candidates"]), 1)
        self.assertLess(queue["candidates"][0]["recommendation_score"], 70)
        self.assertEqual(queue["candidates"][0]["reading_lane"], "daily")

    def test_recent_and_supplementary_candidates_are_separated(self):
        queue = self.collect([self.paper(1, "2026-10-16"), self.paper(2, "2026-10-10")])
        self.assertEqual([p["reading_lane"] for p in queue["candidates"]], ["daily", "supplementary"])
        self.assertTrue((self.tmp / "run" / "discovery.md").exists())
        self.assertTrue((self.tmp / "observations.json").exists())

    def test_five_relevant_papers_do_not_expand_despite_low_proxy_scores(self):
        queue = self.collect([self.paper(i, "2026-10-16") for i in range(5)])
        self.assertIn("近24小时", queue["metadata"]["window"])
        self.assertEqual(len(queue["candidates"]), 5)

    def test_partial_sample_is_explicit_and_not_called_rising(self):
        queue = self.collect([self.paper(i, "2026-10-16") for i in range(6)],
                             errors=[{"source": "arxiv", "error": "offline"}], status="partial")
        self.assertEqual(queue["status"], "partial")
        self.assertTrue(queue["metadata"]["warnings"])
        self.assertEqual(queue["radar"]["topics"][0]["status"], "insufficient_data")

    def test_all_failed_invalidates_old_queue_and_does_not_write_observations(self):
        args = run_daily.parse_args(["collect", "--profile", str(self.profile), "--output-dir", str(self.tmp),
                                    "--observations", str(self.tmp / "observations.json")])
        with patch.object(run_daily, "collect", side_effect=RuntimeError("all failed")):
            with self.assertRaises(RuntimeError):
                run_daily.collect_stage(args)
        self.assertEqual(json.loads((self.tmp / "review_queue.json").read_text())["status"], "failed")
        self.assertFalse((self.tmp / "observations.json").exists())


class DeliverStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.reviewed = self.tmp / "reviewed_articles.json"
        self.reviewed.write_text(json.dumps(REVIEWED, ensure_ascii=False), encoding="utf-8")
        self.report = self.tmp / "daily-report.md"
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.push_status: dict = {"configured": True, "success": True, "provider": "wxpusher"}

    def fake_run_script(self, name: str, *arguments: str) -> None:
        self.calls.append((name, arguments))

    def fake_load_json(self, path, default=None):
        if Path(path).name.endswith(".delivery.json"):
            return self.push_status
        return default if default is not None else {}

    def deliver(self, extra: list[str]) -> None:
        args = run_daily.parse_args(
            ["deliver", "--reviewed", str(self.reviewed), "--report", str(self.report), *extra]
        )
        with contextlib.redirect_stdout(io.StringIO()), patch.object(
            run_daily, "run_script", self.fake_run_script
        ), patch.object(run_daily, "load_json", self.fake_load_json):
            run_daily.deliver_stage(args)

    def test_local_report_is_recorded_when_explicitly_confirmed(self) -> None:
        self.deliver(["--skip-push", "--record-local-report"])
        executed = [name for name, _ in self.calls]
        self.assertEqual(
            executed, ["summarize_papers.py", "deduplicate.py", "update_research_memory.py"]
        )
        self.assertNotIn("push_wechat.py", executed)
        commit_args = next(args for name, args in self.calls if name == "deduplicate.py")
        self.assertIn("local-report", commit_args)

    def test_nothing_is_committed_without_confirmation(self) -> None:
        self.deliver(["--skip-push"])
        executed = [name for name, _ in self.calls]
        self.assertEqual(executed, ["summarize_papers.py"])

    def test_successful_push_triggers_history_and_memory_updates(self) -> None:
        self.deliver(["--provider", "wxpusher"])
        executed = [name for name, _ in self.calls]
        self.assertIn("push_wechat.py", executed)
        self.assertIn("deduplicate.py", executed)
        self.assertIn("update_research_memory.py", executed)
        commit_args = next(args for name, args in self.calls if name == "deduplicate.py")
        self.assertIn("wxpusher", commit_args)

    def test_unconfigured_push_does_not_pollute_history(self) -> None:
        self.push_status = {"configured": False, "success": False, "provider": None}
        self.deliver([])
        executed = [name for name, _ in self.calls]
        self.assertNotIn("deduplicate.py", executed)
        self.assertNotIn("update_research_memory.py", executed)

    def test_failed_push_does_not_pollute_history(self) -> None:
        self.push_status = {"configured": True, "success": False, "provider": "wxpusher"}
        self.deliver([])
        executed = [name for name, _ in self.calls]
        self.assertNotIn("deduplicate.py", executed)


if __name__ == "__main__":
    unittest.main()
