from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from common import atomic_write_json, load_json
from rank_papers import rank_payload, select_review_candidates
from venue_ranking import load_ranking, classify, venue_priority, apply_venue_importance
from summarize_papers import render_report
from tests.test_report_validation import article, payload
import run_daily

RANKING = load_ranking()
A_CONFERENCE = "AAAI Conference on Artificial Intelligence"
A_JOURNAL = "Artificial Intelligence"
B_JOURNAL = "Computational Linguistics"


def reviewed(title, venue, score=80, day="2026-10-16", **extra):
    p = article(title=title, chinese_title=title, journal=venue, recommendation_score=score,
                doi="10.1000/"+title.replace(" ", "-"), publication_date=day, **extra)
    p["venue_ranking"] = classify(p, RANKING)
    if venue_priority(p) > 0:
        p["venue_evidence"] = {"url": "https://example.org/official-paper",
                                "publication_type": "full" if p["venue_ranking"]["kind"] == "conference" else "journal"}
    apply_venue_importance(p)
    return p


class VenuePriorityTests(unittest.TestCase):
    def test_verified_a_precedes_higher_scoring_b_and_new_preprint(self):
        a = reviewed("A conference work", A_CONFERENCE, 72, "2026-10-01", article_type="proceedings-article")
        b = reviewed("B journal work", B_JOURNAL, 99)
        preprint = reviewed("New preprint", "arXiv", 100, is_preprint=True, article_type="preprint")
        for p in (a,b,preprint):
            p.update(excluded=False, matched_topics=[p["title"]])
        chosen = select_review_candidates([b,preprint,a], 1, reading_order="venue_first")
        self.assertEqual(chosen[0]["title"], a["title"])
        text = render_report(payload([b,preprint,a], date="2026-10-16", term_explanation_mode="off", metadata={"reading_order":"venue_first"}))
        self.assertLess(text.index("### 1. A conference work"), text.index("### 2. B journal work"))
        self.assertIn("认可度/等级：CCF-A 会议", text)
        self.assertIn("🥇 **第一篇：A conference work**", text)

    def test_importance_uses_venue_majority_and_is_idempotent(self):
        p = reviewed("Journal work", A_JOURNAL, 80)
        self.assertEqual(p["assessment"]["importance"], round(95*.6+80*.4))
        snapshot = copy.deepcopy(p["assessment"])
        apply_venue_importance(p)
        self.assertEqual(p["assessment"], snapshot)

    def test_acceptance_statement_does_not_get_a_priority(self):
        p = reviewed("Unconfirmed", "arXiv", comment="Accepted at AAAI Conference on Artificial Intelligence",
                     is_preprint=True, article_type="preprint")
        v = p["venue_ranking"]
        self.assertFalse(v["verified_venue"])
        self.assertIsNone(v["ccf"])
        self.assertEqual(v["candidate_ccf"], "A")
        self.assertEqual(venue_priority(p), -1)
        self.assertIn("作者自述", render_report(payload([p], metadata={"reading_order":"venue_first"})))

    def test_abbreviations_and_companion_tracks_do_not_inherit_a(self):
        short = classify({"journal":"AAAI", "article_type":"proceedings-article"},RANKING)
        self.assertFalse(short["verified_venue"])
        self.assertIsNone(short["ccf"])
        for track in ("Findings", "Workshop", "Short paper", "Technical Brief", "Demo", "Summary"):
            p = reviewed("Track work", A_CONFERENCE, paper_track=track, article_type="proceedings-article")
            with self.subTest(track=track):
                self.assertLessEqual(venue_priority(p), 0)
                self.assertIsNone(p["venue_ranking"]["ccf"])

    def test_a_requires_official_paper_evidence(self):
        p = reviewed("A work", A_CONFERENCE, article_type="proceedings-article")
        del p["venue_evidence"]
        with self.assertRaisesRegex(ValueError,"venue_evidence"):
            render_report(payload([p],metadata={"reading_order":"venue_first"}))

    def test_ten_papers_can_be_delivered_in_one_report(self):
        items = [reviewed(f"Paper {i}", B_JOURNAL) for i in range(10)]
        text = render_report(payload(items,metadata={"reading_order":"venue_first","maximum":10}))
        self.assertIn("最终推荐：10篇",text)
        self.assertIn("### 10.",text)

    def test_off_topic_a_does_not_bypass_scope(self):
        profile = load_json(ROOT / "examples/research_profile.temporal-kg.json")
        result = rank_payload({"papers":[{"title":"Image classification with neural networks",
            "journal":A_CONFERENCE,"article_type":"proceedings-article","abstract":"Novel image classifier"}]},profile,ranking=RANKING)
        self.assertEqual(result["review_candidates"],[])


class VenueWorkflowTests(unittest.TestCase):
    def test_collect_retains_older_a_and_prepare_includes_all_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            profile=root/"profile.json"
            atomic_write_json(profile,{"configured":True,"profile_name":"Fixture","queries":["topic"],
                "topic_groups":[{"label":"Core","weight":100,"terms":["topic"]}],"sources":["arxiv"],"reading_order":"venue_first"})
            papers=[{"title":f"topic new preprint {i}","abstract":"topic reasoning","journal":"arXiv",
                "article_type":"preprint","is_preprint":True,"publication_date":"2026-10-16","url":f"https://example.org/{i}"} for i in range(10)]
            papers += [{"title":"topic older A work","abstract":"topic reasoning","journal":A_CONFERENCE,
                "article_type":"proceedings-article","is_preprint":False,"publication_date":"2026-10-01","url":"https://example.org/A"}]
            raw={"papers":papers,"date_from":"2026-09-17","date_to":"2026-10-16","queries":["topic"],
                "sources_requested":["arxiv"],"source_counts":{"arxiv":11},"retrieved_count":11,"unique_relevant_count":11,"status":"ok"}
            args=run_daily.parse_args(["collect","--profile",str(profile),"--output-dir",str(root/"run"),
                "--to-date","2026-10-16","--watch-limit","0","--observations",str(root/"obs.json"),
                "--history",str(root/"history.json"),"--memory",str(root/"memory.json")])
            with patch.object(run_daily,"collect",return_value=raw), contextlib.redirect_stdout(io.StringIO()):
                queue_path=run_daily.collect_stage(args)
                draft_path=root/"draft.json"
                run_daily.prepare_review(run_daily.parse_args(["prepare","--queue",str(queue_path),"--output",str(draft_path)]))
            q,draft=load_json(queue_path),load_json(draft_path)
            self.assertEqual(q["candidates"][0]["title"],"topic older A work")
            self.assertEqual(q["candidates"][0]["reading_lane"],"supplementary")
            self.assertEqual(len(draft["articles"]),11)
            self.assertEqual(draft["metadata"]["reading_order"],"venue_first")

    def test_delivery_reclassifies_forged_a_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            profile=root/"profile.json"
            atomic_write_json(profile,{"reading_order":"venue_first"})
            item=reviewed("Unranked work","Unknown Venue")
            item["venue_ranking"]={"status":"ccf_listed","ccf":"A","verified_venue":True,"label":"CCF-A"}
            data=root/"reviewed.json"
            atomic_write_json(data,payload([item]))
            with contextlib.redirect_stdout(io.StringIO()):
                run_daily.deliver_stage(run_daily.parse_args(["deliver","--profile",str(profile),
                    "--reviewed",str(data),"--report",str(root/"report.md"),"--skip-push"]))
            actual=load_json(root/"report.reviewed.json")["articles"][0]
            self.assertIsNone(actual["venue_ranking"]["ccf"])
            self.assertEqual(actual["assessment"]["venue_importance"],65)
