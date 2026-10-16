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
        self.assertEqual(args.max_results, 10)
        self.assertIn("openalex", args.sources)

    def test_deliver_requires_reviewed_and_report(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run_daily.parse_args(["deliver"])

    def test_deliver_rejects_unknown_provider(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run_daily.parse_args(
                ["deliver", "--reviewed", "a.json", "--report", "b.md", "--provider", "sms"]
            )


class MainValidationTests(unittest.TestCase):
    def test_min_results_cannot_exceed_max_results(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            run_daily.main(["collect", "--min-results", "20", "--max-results", "5"])
        self.assertIn("--min-results <= --max-results", str(raised.exception))

    def test_argument_errors_are_reported_before_profile_errors(self) -> None:
        """配置缺失不应该掩盖明显的用法错误。"""
        with self.assertRaises(SystemExit) as raised:
            run_daily.main(["collect", "--min-results", "20", "--max-results", "5"])
        self.assertNotIn("尚未配置研究主题", str(raised.exception))

    def test_unconfigured_profile_stops_the_run(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            run_daily.main(["collect"])
        self.assertIn("尚未配置研究主题", str(raised.exception))


class CollectStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.recommended_24h = 6
        self.recommended_7d = 6

    def fake_run_script(self, name: str, *arguments: str) -> None:
        self.calls.append((name, arguments))

    def fake_load_json(self, path, default=None):
        name = Path(path).name
        if name == "ranked_24h.json":
            return {
                "recommended_count": self.recommended_24h,
                "screened_count": 30,
                "recommendations": [{"title": f"候选{i}"} for i in range(self.recommended_24h)],
                "formula": "relevance*0.40 + ...",
            }
        if name == "ranked_7d.json":
            return {
                "recommended_count": self.recommended_7d,
                "screened_count": 90,
                "recommendations": [{"title": "扩窗候选"}],
                "formula": "relevance*0.40 + ...",
            }
        if name == "search_24h.json":
            return {"retrieved_count": 120, "unique_relevant_count": 40, "source_counts": {}}
        if name == "deduplicated_24h.json":
            return {"new_count": 12, "update_count": 1, "updates": [{"title": "预印本转正"}]}
        return default if default is not None else {}

    def collect(self) -> dict:
        args = run_daily.parse_args(
            ["collect", "--output-dir", str(self.tmp), "--min-results", "5", "--max-results", "10"]
        )
        with contextlib.redirect_stdout(io.StringIO()), patch.object(
            run_daily, "run_script", self.fake_run_script
        ), patch.object(run_daily, "load_json", self.fake_load_json):
            queue_path = run_daily.collect_stage(args)
        return json.loads(Path(queue_path).read_text(encoding="utf-8"))

    def test_sufficient_candidates_stay_within_the_24_hour_window(self) -> None:
        queue = self.collect()
        self.assertIn("近24小时优先", queue["metadata"]["window"])
        self.assertNotIn("最近7天", queue["metadata"]["window"])
        self.assertEqual(queue["candidates"][0]["title"], "候选0")
        executed = [name for name, _ in self.calls]
        self.assertEqual(executed, ["search_papers.py", "deduplicate.py", "rank_papers.py"])

    def test_window_expands_when_high_scoring_candidates_are_short(self) -> None:
        self.recommended_24h = 1
        queue = self.collect()
        self.assertIn("最近7天", queue["metadata"]["window"])
        self.assertIn("5篇", queue["metadata"]["window"])
        executed = [name for name, _ in self.calls]
        self.assertEqual(
            executed, ["search_papers.py", "deduplicate.py", "rank_papers.py"] * 2
        )

    def test_window_expansion_ladder_runs_until_min_results_or_30_days(self) -> None:
        """7 天池仍不足时逐级扩到 14 天、30 天，到顶后如实停止。"""
        self.recommended_24h = 1
        self.recommended_7d = 2
        queue = self.collect()
        self.assertIn("最近30天", queue["metadata"]["window"])
        self.assertIn("不足5篇", queue["metadata"]["window"])
        executed = [name for name, _ in self.calls]
        self.assertEqual(
            executed, ["search_papers.py", "deduplicate.py", "rank_papers.py"] * 4
        )

    def test_expansion_message_follows_min_results(self) -> None:
        self.recommended_24h = 1
        args = run_daily.parse_args(
            ["collect", "--output-dir", str(self.tmp), "--min-results", "3", "--max-results", "10"]
        )
        with contextlib.redirect_stdout(io.StringIO()), patch.object(
            run_daily, "run_script", self.fake_run_script
        ), patch.object(run_daily, "load_json", self.fake_load_json):
            queue_path = run_daily.collect_stage(args)
        queue = json.loads(Path(queue_path).read_text(encoding="utf-8"))
        self.assertIn("不足3篇", queue["metadata"]["window"])

    def test_queue_records_counts_warnings_and_memory_context(self) -> None:
        queue = self.collect()
        self.assertEqual(queue["metadata"]["retrieved_count"], 120)
        self.assertEqual(queue["metadata"]["new_after_history_count"], 12)
        self.assertEqual(queue["metadata"]["preliminary_recommended_count"], 6)
        self.assertIn("预印本转正", json.dumps(queue["updates_not_for_new_push"], ensure_ascii=False))
        self.assertTrue(
            any("出版信息更新" in item for item in queue["metadata"]["warnings"])
        )
        self.assertIn("scope_note", queue["research_memory_context"])

    def test_queue_carries_the_review_requirement(self) -> None:
        queue = self.collect()
        self.assertIn("不是最终推送结果", queue["review_requirement"])


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
