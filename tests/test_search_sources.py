from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import search_papers  # noqa: E402


class SearchSourceTests(unittest.TestCase):
    def test_crossref_registration_is_not_used_as_publication_date(self):
        self.assertEqual(search_papers._crossref_date({"created": {"date-time": "2026-10-17T00:00:00Z"}}), "")
        self.assertEqual(search_papers._crossref_date({"published-online": {"date-parts": [[2026, 9, 21]]},
                                                      "published-print": {"date-parts": [[2026, 11, 1]]}}), "2026-09-21")

    def test_semantic_pagination_cycle_is_reported(self):
        page = {"data": [{"title": "TKG reasoning", "publicationDate": "2026-10-17"}], "token": "repeated"}
        with patch.object(search_papers, "json_request", return_value=page) as request, \
             patch.object(search_papers.time, "sleep"), self.assertWarns(UserWarning):
            search_papers.search_semantic_scholar(["TKG"], date(2026, 10, 17), date(2026, 10, 17))
        self.assertEqual(request.call_count, 2)

    def test_semantic_bulk_pages_latest_and_preserves_boolean_query(self):
        paper = {"paperId": "new", "title": "Temporal knowledge graph reasoning", "publicationDate": "2026-10-17"}
        pages = [{"data": [paper], "token": "page2"},
                 {"data": [dict(paper, paperId="old", publicationDate="2026-10-16"),
                           dict(paper, publicationDate=None), dict(paper, publicationDate="2026-10-18")]}]
        with patch.object(search_papers, "json_request", side_effect=pages) as request, patch.object(search_papers.time, "sleep"):
            result = search_papers.search_semantic_scholar(
                ['("temporal knowledge graph" OR TKGR)'], date(2026, 10, 16), date(2026, 10, 17))
        self.assertEqual([p["publication_date"] for p in result], ["2026-10-17", "2026-10-16"])
        query = parse_qs(urlparse(request.call_args_list[0].args[0]).query)
        self.assertEqual(query["sort"], ["publicationDate:desc"])
        self.assertEqual(query["publicationDateOrYear"], ["2026-10-16:2026-10-17"])
        self.assertIn('"temporal knowledge graph" | TKGR', query["query"][0])
        self.assertIn("token=page2", request.call_args_list[1].args[0])

    def test_single_failed_source_preserves_healthy_result(self):
        paper = {"title": "Temporal knowledge graph reasoning", "publication_date": "2026-10-17"}
        def failed(*args):
            raise TimeoutError("offline")
        with patch.dict(search_papers.SOURCE_FUNCTIONS, {"arxiv": failed, "crossref": lambda *args: [paper]}):
            result = search_papers.collect(queries=["TKG"], start=date(2026, 10, 17), end=date(2026, 10, 17),
                                           sources=["arxiv", "crossref"], relevance_terms=["temporal knowledge graph"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["papers"]), 1)
        self.assertEqual(result["errors"][0]["source"], "arxiv")

    def test_crossref_searches_online_and_general_publication_dates(self) -> None:
        empty_payload = {"message": {"items": []}}
        with (
            patch.dict(
                os.environ,
                {"CROSSREF_MAILTO": "researcher@example.org"},
                clear=True,
            ),
            patch.object(
                search_papers, "json_request", return_value=empty_payload
            ) as request,
            patch.object(search_papers.time, "sleep"),
        ):
            search_papers.search_crossref(
                ["plant cold stress"], date(2026, 8, 31), date(2026, 9, 1)
            )

        self.assertEqual(request.call_count, 4)
        queries = [
            parse_qs(urlparse(call.args[0]).query)
            for call in request.call_args_list
        ]
        self.assertTrue(all(query["sort"] == ["score"] and "cursor" in query for query in queries))
        filters = {query["filter"][0] for query in queries}
        self.assertEqual(
            filters,
            {
                "from-online-pub-date:2026-08-31,until-online-pub-date:2026-09-01,type:journal-article",
                "from-pub-date:2026-08-31,until-pub-date:2026-09-01,type:journal-article",
                "from-online-pub-date:2026-08-31,until-online-pub-date:2026-09-01,type:proceedings-article",
                "from-pub-date:2026-08-31,until-pub-date:2026-09-01,type:proceedings-article",
            },
        )
        self.assertTrue(
            all(query["mailto"] == ["researcher@example.org"] for query in queries)
        )

    def test_semantic_scholar_spaces_multiple_queries_at_one_request_per_second(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(
                search_papers, "json_request", return_value={"data": []}
            ),
            patch.object(search_papers.time, "sleep") as sleep,
        ):
            search_papers.search_semantic_scholar(
                ["cold stress", "polyploid"],
                date(2026, 8, 31),
                date(2026, 9, 1),
            )

        sleep.assert_called_once_with(1.05)


if __name__ == "__main__":
    unittest.main()
