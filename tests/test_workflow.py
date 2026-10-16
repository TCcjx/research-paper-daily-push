from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_daily
from common import load_json, atomic_write_json, workflow_lock
from watch_papers import check_watchlist
from tests.test_report_validation import article


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / "profile.json"
        self.observations = self.root / "observations.json"
        self.history = self.root / "history.json"
        self.memory = self.root / "memory.json"

    def invoke(self, args):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_daily.main(args)

    def test_configuration_discovery_review_delivery_and_weekly(self):
        self.invoke(["init-profile", "--name", "Fixture", "--query", "topic", "--topic", "主题=topic",
                     "--output", str(self.profile)])
        with self.assertRaises(ValueError):
            self.invoke(["init-profile", "--name", "Overwrite", "--query", "topic", "--topic", "T=topic",
                         "--output", str(self.profile)])
        raw = {"date_from": "2026-09-17", "date_to": "2026-10-16", "queries": ["topic"],
               "sources_requested": ["arxiv"], "source_counts": {"arxiv": 1},
               "retrieved_count": 1, "unique_relevant_count": 1, "status": "ok", "errors": [],
               "coverage_warnings": [], "papers": [{"title": "topic fixture", "doi": "10.1000/fixture",
                "abstract": "topic", "publication_date": "2026-10-16", "url": "https://example.org/paper",
                "journal": "arXiv", "article_type": "preprint", "is_preprint": True}]}
        with patch.object(run_daily, "collect", return_value=raw):
            self.invoke(["collect", "--profile", str(self.profile), "--to-date", "2026-10-16",
                         "--sources", "arxiv", "--output-dir", str(self.root / "run"), "--watch-limit", "0",
                         "--observations", str(self.observations), "--history", str(self.history), "--memory", str(self.memory)])
        queue = self.root / "run" / "review_queue.json"
        reviewed = self.root / "reviewed.json"
        self.invoke(["prepare", "--queue", str(queue), "--output", str(reviewed)])
        draft = load_json(reviewed)
        self.assertEqual(draft["articles"][0]["score_status"], "pending_evidence_review")
        draft["articles"] = [article(title="topic fixture", doi="10.1000/fixture", publication_date="2026-10-16",
                                     is_preprint=True, article_type="preprint")]
        # 此处模拟研究人员完成阅读，测试只验证完整数据流，不声称这是一篇真实论文。
        atomic_write_json(reviewed, draft)
        report = self.root / "daily.md"
        command = ["deliver", "--profile", str(self.profile), "--reviewed", str(reviewed), "--queue", str(queue),
                   "--report", str(report), "--history", str(self.history), "--memory", str(self.memory),
                   "--skip-push", "--record-local-report"]
        self.invoke(command)
        text = report.read_text(encoding="utf-8")
        for term in ("研究热点线索", "持续跟踪", "主要贡献", "预印本"):
            self.assertIn(term, text)
        self.assertEqual(len(load_json(self.history)), 1)
        self.assertEqual(len(load_json(self.memory)["learned_reports"]), 1)
        with self.assertRaises(ValueError):
            self.invoke(command)
        weekly = self.root / "weekly.md"
        self.invoke(["weekly", "--profile", str(self.profile), "--sources", "arxiv",
                     "--observations", str(self.observations), "--to-date", "2026-10-16", "--output", str(weekly)])
        self.assertIn("实际观测 1 天", weekly.read_text(encoding="utf-8"))
        self.assertFalse(self.history.with_suffix(".lock").exists())

    def test_lock_blocks_concurrent_data_mutations_and_releases(self):
        path = self.root / "run.lock"
        with workflow_lock(path):
            with self.assertRaises(RuntimeError):
                with workflow_lock(path):
                    self.fail("second lock should not be acquired")
        self.assertFalse(path.exists())

    def test_watchlist_is_bounded_and_failure_is_nonfatal(self):
        items = [{"doi": f"10.1000/{i}"} for i in range(10)]
        with patch("watch_papers.json_request", side_effect=RuntimeError("offline")) as request, patch("watch_papers.time.sleep"):
            papers, errors = check_watchlist(items, limit=2, sources=["crossref"])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(papers, [])
        self.assertEqual(len(errors), 2)

    def test_watchlist_disabled_does_not_query(self):
        with patch("watch_papers.json_request") as request:
            check_watchlist([{"doi": "10.1000/test"}], limit=0)
        request.assert_not_called()
