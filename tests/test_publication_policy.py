"""以虚构记录验证筛选政策；不声称样例URL已核实真实论文。"""
from __future__ import annotations

import contextlib
import copy
import io
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from common import atomic_write_json, load_json
from publication_policy import evaluate_publication, validate_agent_assessment
from profile_config import load_profile, profile_queries
from topic_scope import assess_scope
from venue_ranking import load_ranking
from rank_papers import rank_payload
from search_papers import collect
from deduplicate import filter_history, history_reference, commit_history
from summarize_papers import render_report
from tests.test_report_validation import article, payload
import run_daily

RANKING = load_ranking()
PROFILE = load_profile(ROOT / "examples/research_profile.temporal-kg.json")
POLICY = PROFILE["publication_filter"]
A = "AAAI Conference on Artificial Intelligence"
B = "ACM International Conference on Information and Knowledge Management"
C = "International Conference on Artificial Neural Networks"


def record(venue=A, publication_type="full"):
    return {"venue": venue, "url": "https://example.org/paper", "publication_type": publication_type,
            "status": "accepted", "verified": True}


def candidate(index=0, venue=A, **changes):
    p = article(title=f"Temporal knowledge graph reasoning {index}", chinese_title=f"时态推理论文{index}",
                publication_date="2026-10-16", journal=venue, article_type="proceedings-article",
                doi=f"10.1000/policy-{index}", venue_evidence=record(venue),
                recommendation_score=65, agent_assessment={"integrated": False})
    p.update(changes)
    return p


def quartile(venue, system="jcr", q=2, **changes):
    result = {"system": system, "quartile": q, "year": 2025, "category": "Artificial Intelligence",
              "venue": venue, "source_url": "https://example.org/quartile", "verified": True}
    result.update(changes)
    return result


def decision(p):
    return evaluate_publication(p, POLICY, RANKING, year=2026)["status"]


class PublicationTests(unittest.TestCase):
    def test_latest_first_keeps_newer_non_agent_ahead_of_older_agent(self):
        newer = candidate(1, publication_date="2026-10-17")
        older_agent = candidate(2, publication_date="2026-10-16",
                                agent_assessment={"integrated": True, "role": "plans graph traversal",
                                                  "evidence_url": "https://example.org/paper"})
        newer_agent = candidate(3, publication_date="2026-10-17",
                                agent_assessment=older_agent["agent_assessment"])
        report = payload([older_agent, newer, newer_agent], publication_filter=POLICY,
                         metadata={"reading_order": "latest_first"})
        rendered = render_report(report)
        self.assertLess(rendered.index(newer_agent["chinese_title"]), rendered.index(newer["chinese_title"]))
        self.assertLess(rendered.index(newer["chinese_title"]), rendered.index(older_agent["chinese_title"]))

    def test_conferences_a_b_accepted_c_excluded(self):
        for venue, expected in ((A, "eligible"), (B, "eligible"), (C, "excluded")):
            with self.subTest(venue=venue):
                self.assertEqual(decision(candidate(venue=venue)), expected)

    def test_claim_and_abbreviation_do_not_establish_official_acceptance(self):
        p = candidate(venue="arXiv", article_type="preprint", is_preprint=True,
                      journal_reference="Accepted at AAAI", venue_evidence={})
        self.assertEqual(decision(p), "pending")
        self.assertEqual(decision(candidate(venue="AAAI")), "pending")

    def test_official_acceptance_of_preprint_is_eligible_and_keeps_carrier(self):
        p = candidate(journal="arXiv", article_type="preprint", is_preprint=True)
        self.assertEqual(decision(p), "eligible")
        self.assertTrue(p["is_preprint"])
        report = render_report(payload([p], publication_filter=POLICY, metadata={"reading_order":"agent_first"}))
        self.assertIn("官方录用已核实", report)
        self.assertNotIn("未经正式同行评议", report)

    def test_workshop_and_short_do_not_inherit_main_conference(self):
        for changes in ({"paper_track":"workshop"}, {"venue_evidence":record(A,"short")}):
            self.assertNotEqual(decision(candidate(**changes)), "eligible")

    def test_journal_either_quartile_system_is_sufficient_regardless_of_ccf(self):
        venue = "Applied Intelligence"  # 内置CCF C期刊，不等于CAS/JCR低分区。
        for records in ([quartile(venue)], [quartile(venue,"cas",1)],
                        [quartile(venue,"cas",3),quartile(venue,"jcr",2)]):
            p = candidate(venue=venue, article_type="journal-article", venue_evidence=record(venue,"journal"), journal_quartiles=records)
            self.assertEqual(decision(p), "eligible")
        p["journal_quartiles"] = [quartile(venue,"cas",3),quartile(venue,"jcr",4)]
        self.assertEqual(decision(p), "excluded")
        p["journal_quartiles"] = [quartile(venue,"cas",3)]
        self.assertEqual(decision(p), "pending")

    def test_invalid_or_unknown_quartile_evidence_stays_pending(self):
        venue = "Applied Intelligence"
        for invalid in ({"year":2027}, {"category":""}, {"venue":"Other Journal"},
                        {"verified":False}, {"source_url":"not a URL"}, {"quartile":True}):
            p = candidate(venue=venue, article_type="journal-article", venue_evidence=record(venue,"journal"),
                          journal_quartiles=[quartile(venue,**invalid)]) if "venue" not in invalid else candidate(
                          venue=venue, article_type="journal-article", venue_evidence=record(venue,"journal"),
                          journal_quartiles=[quartile("Other Journal")])
            self.assertEqual(decision(p), "pending", invalid)

    def test_wrong_official_venue_cannot_promote_formal_paper(self):
        self.assertEqual(decision(candidate(venue=C,venue_evidence=record(A))), "pending")

    def test_final_renderer_rechecks_gate_and_requires_actually_read_record(self):
        p = candidate(venue=C)
        p["publication_decision"] = {"status":"eligible"}
        with self.assertRaisesRegex(ValueError,"最终场所门槛"):
            render_report(payload([p], publication_filter=POLICY))
        p = candidate(venue_evidence={**record(),"url":"https://example.org/not-read"})
        with self.assertRaisesRegex(ValueError,"实际阅读"):
            render_report(payload([p], publication_filter=POLICY))

    def test_agent_claim_requires_role_and_read_evidence(self):
        for assessment in ({"integrated":None}, {"integrated":True,"role":"planning","evidence_url":"https://example.org/unread"}):
            with self.assertRaises(ValueError):
                validate_agent_assessment(candidate(agent_assessment=assessment))

    def test_unlimited_final_low_scores_and_agent_priority(self):
        papers = [candidate(i) for i in range(125)]
        papers[-1]["agent_assessment"] = {"integrated":True,"role":"时间约束工具规划","evidence_url":"https://example.org/paper"}
        report = render_report(payload(papers, publication_filter=POLICY, metadata={"maximum":10,"reading_order":"agent_first"}))
        self.assertIn("最终推荐：125篇", report)
        self.assertLess(report.index("### 1. 时态推理论文124"), report.index("### 2. "))
        ranked = rank_payload({"papers":papers}, PROFILE, ranking=RANKING, threshold=99, maximum=10)
        self.assertEqual(len(ranked["recommendations"]),125)
        self.assertEqual(len(ranked["review_candidates"]),125)


class StagesAndHistoryTests(unittest.TestCase):
    def test_tkg_before_reasoning_agent_never_bypasses_scope(self):
        storage = assess_scope({"title":"Temporal knowledge graph storage"},PROFILE["topic_scope"])
        self.assertEqual([s["passed"] for s in storage["stages"]],[True,False])
        static = assess_scope({"title":"Agent static knowledge graph reasoning"},PROFILE["topic_scope"])
        self.assertEqual(static["first_failed_stage"],"时态知识图谱")

    def test_proof_carrying_agent_decisions_are_in_scope(self):
        p={"title":"Proof-Carrying Selective Recommendation", "abstract":"A language agent links requirements to evidence in a temporal knowledge graph, while a deterministic checker establishes support."}
        self.assertTrue(assess_scope(p, PROFILE["topic_scope"])["in_scope"])

    def test_retrieval_mode_is_broad_then_locally_staged(self):
        self.assertEqual(profile_queries(PROFILE,"agent_tkg"),profile_queries(PROFILE,"all"))
        self.assertNotIn("AND",profile_queries(PROFILE,"agent_tkg")[0])
        samples=[candidate(),candidate(1,title="Temporal knowledge graph storage"),candidate(2,title="Agent static knowledge graph reasoning")]
        with patch("search_papers.SOURCE_FUNCTIONS",{"arxiv":lambda *a:samples}):
            raw=collect(queries=profile_queries(PROFILE,"agent_tkg"),start=date(2026,9,17),end=date(2026,10,16),sources=["arxiv"],relevance_terms=["agent"],topic_scope=PROFILE["topic_scope"])
        self.assertEqual(len(raw["papers"]),1)
        self.assertEqual([g["count"] for g in raw["scope_stage_counts"]],[2,1])

    def test_only_last_delivered_report_is_dedup_reference(self):
        old,last=candidate(0),candidate(1)
        memory={"learned_reports":[{"report_id":"a","learned_at":"2026-10-16T01:00:00Z"},{"report_id":"b","learned_at":"2026-10-16T02:00:00Z"}],
                "papers":[{**old,"report_ids":["a"]},{**last,"report_ids":["b"]}]}
        reference=history_reference([old,last],memory,"last_report")
        self.assertEqual([p["doi"] for p in reference],[last["doi"]])
        self.assertEqual(filter_history([old,last],reference)["new_count"],1)
        history=[{**old,"pushed_date":"2026-10-15"},{**last,"pushed_date":"2026-10-16"}]
        self.assertEqual(history_reference(history,{},"last_report"),[history[-1]])

    def test_arxiv_rename_version_and_new_doi_are_updates_not_new(self):
        old=candidate(doi="",source_ids={"arxiv":"2609.12345v1","arxiv_version":"v1"})
        new=candidate(title="Renamed temporal graph paper",doi="10.1000/new",source_ids={"arxiv":"2609.12345v2","arxiv_version":"v2"})
        result=filter_history([new],[old])
        self.assertEqual(result["new_count"],0)
        self.assertEqual(result["update_count"],1)
        history,count=commit_history([new],[old],delivery="local-report",pushed_date="2026-10-16")
        self.assertEqual(count,0)
        self.assertEqual(len(history),1)

    def test_empty_followup_does_not_erase_last_paper_batch(self):
        p=candidate()
        memory={"learned_reports":[{"report_id":"papers","article_count":1,"learned_at":"2026-10-16T01:00:00Z"},
                {"report_id":"empty","article_count":0,"learned_at":"2026-10-16T02:00:00Z"}],
                "papers":[{**p,"report_ids":["papers"]}]}
        self.assertEqual(history_reference([p],memory,"last_report"),[p])
        # 同秒交付通过明确批次指针确定先后，不依赖报告哈希的排序。
        memory["learned_reports"].append({"report_id":"other","article_count":1,"learned_at":"2026-10-16T01:00:00Z"})
        memory["last_paper_report_id"]="papers"
        self.assertEqual(history_reference([p],memory,"last_report"),[p])


class CompletePolicyWorkflowTests(unittest.TestCase):
    def test_already_removed_duplicates_survive_queue_prepare_and_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            p=candidate()
            atomic_write_json(root/"profile.json",PROFILE)
            atomic_write_json(root/"history.json",[p])
            queue={"status":"ok","date":"2026-10-16","metadata":{"maximum":None},"topic_scope":PROFILE["topic_scope"],
                   "publication_filter":POLICY,"candidates":[],"duplicates_not_recommended":[p]}
            atomic_write_json(root/"queue.json",queue)
            with contextlib.redirect_stdout(io.StringIO()):
                run_daily.prepare_review(run_daily.parse_args(["prepare","--queue",str(root/"queue.json"),"--output",str(root/"draft.json")]))
                run_daily.deliver_stage(run_daily.parse_args(["deliver","--profile",str(root/"profile.json"),"--reviewed",str(root/"draft.json"),
                    "--queue",str(root/"queue.json"),"--report",str(root/"report.md"),"--history",str(root/"history.json"),"--memory",str(root/"memory.json"),"--skip-push"]))
            self.assertEqual(load_json(root/"report.reviewed.json")["metadata"]["duplicate_count"],1)
            self.assertIn("与上次交付重复",(root/"report.md").read_text(encoding="utf-8"))

    def test_collect_prepare_deliver_unlimited_and_filter_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            atomic_write_json(root/"profile.json",PROFILE)
            papers=[candidate(i) for i in range(125)]
            papers += [candidate(125,venue=C),candidate(126,journal="arXiv",is_preprint=True,article_type="preprint",venue_evidence={})]
            raw={"papers":papers,"date_from":"2026-09-17","date_to":"2026-10-16","queries":PROFILE["queries"],
                 "sources_requested":["arxiv"],"source_counts":{"arxiv":len(papers)},"retrieved_count":len(papers),"unique_relevant_count":len(papers),"status":"ok"}
            args=run_daily.parse_args(["collect","--profile",str(root/"profile.json"),"--to-date","2026-10-16","--output-dir",str(root/"run"),
                "--history",str(root/"history.json"),"--memory",str(root/"memory.json"),"--observations",str(root/"obs.json"),"--watch-limit","0"])
            with patch.object(run_daily,"collect",return_value=raw),contextlib.redirect_stdout(io.StringIO()):
                queue=run_daily.collect_stage(args)
                run_daily.prepare_review(run_daily.parse_args(["prepare","--queue",str(queue),"--output",str(root/"draft.json")]))
            self.assertEqual(len(load_json(root/"draft.json")["articles"]),126)
            self.assertEqual(len(load_json(queue)["publication_exclusions"]),1)
            # 模拟研究人员复核；最终门槛仍重算，不能信任初筛标签。
            atomic_write_json(root/"reviewed.json",payload([p for p in papers if p["journal"] != C], date="2026-10-16",term_explanation_mode="off"))
            atomic_write_json(root/"history.json",[papers[0]])
            with contextlib.redirect_stdout(io.StringIO()):
                run_daily.deliver_stage(run_daily.parse_args(["deliver","--profile",str(root/"profile.json"),"--reviewed",str(root/"reviewed.json"),
                    "--queue",str(queue),
                    "--report",str(root/"report.md"),"--history",str(root/"history.json"),"--memory",str(root/"memory.json"),"--skip-push"]))
            reviewed=load_json(root/"report.reviewed.json")
            self.assertEqual(len(reviewed["articles"]),124)
            for field in ("pending_verification","publication_exclusions","duplicates_not_recommended"):
                self.assertEqual(len(reviewed[field]),1)
            self.assertIn("最终推荐：124篇",(root/"report.md").read_text(encoding="utf-8"))
            self.assertEqual(len(load_json(root/"history.json")),1)


if __name__ == "__main__":
    unittest.main()
