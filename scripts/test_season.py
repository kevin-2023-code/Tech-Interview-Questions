#!/usr/bin/env python3
"""Tests for the season index and the link checker. Standard library only; no network."""

from __future__ import annotations

import json
import posixpath
import re
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build import build  # noqa: E402
from catalog import Catalog, latest_sighting, load_catalog, question_from_payload, snapshot  # noqa: E402
from check_links import check, classify, site_links  # noqa: E402
from company import company_files  # noqa: E402
from render import SITE_LINK_QUERY, site_link  # noqa: E402
from season import (  # noqa: E402
    LATEST_PAGE_ROWS,
    README_AI_CODING_ROWS,
    README_NEW_ROWS,
    README_RECENT_ROWS,
    index_table,
    latest_files,
    new_block,
    new_this_week,
    recent_block,
    season_name,
    stats_line,
    title_line,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "scripts" / "fixtures" / "catalog-sample.json"
TODAY = date(2026, 9, 11)
TABLE = {"api_labels": {}, "homes": frozenset({"acme"}), "root": ""}


def q(slug="q-1", **over):
    payload = {
        "slug": slug,
        "title": over.pop("title", f"Question {slug}"),
        "url": f"https://trueinterview.io/questions/{slug}",
        "type": over.pop("type", "algorithm"),
        "typeLabel": "Algorithm",
        "difficulty": "medium",
        "companies": over.pop("companies", ["Acme"]),
        "topics": [],
        "tags": [],
        "rounds": over.pop("rounds", []),
        "reported": over.pop("reported", None),
        "hasSolution": True,
        "accessTier": "insider",
        "addedAt": over.pop("addedAt", "2026-01-01T00:00:00+00:00"),
    }
    payload.update(over)
    return question_from_payload(payload)


def render(questions=None):
    template = (ROOT / "README.md").read_text(encoding="utf-8")
    catalog = (
        Catalog(questions=questions, labels={})
        if questions is not None
        else load_catalog(json.loads(FIXTURE.read_text(encoding="utf-8")))
    )
    return catalog, build(catalog, template, TODAY)


def block(readme: str, marker: str) -> str:
    return readme.split(f"<!-- {marker}:start -->")[1].split(f"<!-- {marker}:end -->")[0]


class TestSeasonTitle(unittest.TestCase):
    def test_the_title_names_this_year_and_the_next(self):
        self.assertEqual(season_name(date(2026, 11, 19)), "2026 & 2027")
        self.assertEqual(title_line(date(2027, 1, 2)), "# 2027 & 2028 Tech Interview & OA Questions")

    def test_the_readme_opens_with_it(self):
        _catalog, result = render()
        self.assertIn("# 2026 & 2027 Tech Interview & OA Questions", result.files["README.md"].splitlines()[:3])


class TestLatestSighting(unittest.TestCase):
    def test_a_later_write_up_month_wins_and_is_printed_as_a_month(self):
        self.assertEqual(latest_sighting("2026-05-03", "2026-09"), ("2026-09", date(2026, 9, 1), True))

    def test_inside_the_same_month_the_day_is_kept(self):
        self.assertEqual(latest_sighting("2026-09-14", "2026-09"), ("2026-09-14", date(2026, 9, 14), False))

    def test_an_earlier_or_unreadable_month_changes_nothing(self):
        self.assertEqual(latest_sighting("2026-09-14", "2026-03")[0], "2026-09-14")
        self.assertEqual(latest_sighting("2026-09-14", "garbage")[0], "2026-09-14")
        self.assertEqual(latest_sighting(None, None), (None, None, False))

    def test_a_question_with_only_a_write_up_month_is_dated(self):
        question = q(reported=None, lastReported="2026-08")
        self.assertEqual((question.reported, question.reported_is_month_only), ("2026-08", True))


class TestStatsLine(unittest.TestCase):
    def test_every_number_is_counted_from_the_snapshot(self):
        rows = [
            q("new", addedAt=(TODAY - timedelta(days=3)).isoformat() + "T00:00:00+00:00", reported="2026-09-01"),
            q("old", addedAt="2026-01-01T00:00:00+00:00", reported="2025-01-01"),
            q("undated"),
        ]
        self.assertEqual(
            stats_line(rows, 1, TODAY),
            "**3 questions · 1 companies · +1 added in the last 30 days · "
            "1 reported in the last 90 days · data as of 2026-09-11**",
        )

    def test_a_row_added_after_the_snapshot_date_is_not_counted_as_new(self):
        rows = [q("future", addedAt="2026-12-01T00:00:00+00:00")]
        self.assertIn("+0 added", stats_line(rows, 1, TODAY))
        self.assertEqual(new_this_week(rows, TODAY), [])


class TestNewThisWeek(unittest.TestCase):
    def test_an_empty_week_says_so_and_promotes_nothing(self):
        text = new_block([q("old")], TODAY, show_reports=False, **TABLE)
        self.assertEqual(text, "No questions were added in the 7 days to 2026-09-11.")

    def test_it_leads_with_the_added_date_and_keeps_the_reported_date_apart(self):
        added = (TODAY - timedelta(days=2)).isoformat()
        text = new_block(
            [q("fresh", addedAt=added + "T08:00:00+00:00", reported="2026-03")], TODAY, show_reports=False, **TABLE
        )
        self.assertIn("| Added | Company | Question | Format | Round | Level | Last reported |", text)
        row = [line for line in text.splitlines() if line.startswith(f"| {added}")][0]
        self.assertTrue(row.endswith("| 2026-03 |"), row)

    def test_it_is_capped_and_says_how_many_there_were(self):
        added = (TODAY - timedelta(days=1)).isoformat() + "T00:00:00+00:00"
        rows = [q(f"n{i}", addedAt=added) for i in range(README_NEW_ROWS + 4)]
        text = new_block(rows, TODAY, show_reports=False, **TABLE)
        self.assertIn(f"{README_NEW_ROWS + 4} questions added", text)
        self.assertEqual(sum(1 for line in text.splitlines() if line.startswith("| 2026")), README_NEW_ROWS)


class TestIndexTable(unittest.TestCase):
    def test_a_row_is_metadata_and_a_site_link_only(self):
        row = q(
            "lru",
            title="LRU Cache",
            reported="2026-09-02",
            rounds=["oa", "onsite"],
            seniority=["senior", "intern", "wizard"],
            companies=["Acme", "Globex", "Initech"],
            reportCount=7,
        )
        text = index_table([row], show_reports=True, **TABLE)
        self.assertEqual(
            text.splitlines()[2],
            "| 2026-09-02 | [Acme](companies/acme/), Globex +1 | "
            f"[LRU Cache](https://trueinterview.io/questions/lru?{SITE_LINK_QUERY}) | Algorithm | OA, Onsite | "
            "Intern, Senior | 7 |",
        )

    def test_the_reports_column_is_absent_when_the_snapshot_has_no_counts(self):
        text = index_table([q("a", reported="2026-09-02")], show_reports=False, **TABLE)
        self.assertNotIn("Reports", text)

    def test_an_unknown_count_is_a_dash_and_a_real_zero_is_a_zero(self):
        rows = [q("a", reportCount=0), q("b"), q("c", reportCount=-1), q("d", reportCount=True)]
        text = index_table(rows, show_reports=True, **TABLE)
        last = [line.rstrip(" |").rsplit("| ", 1)[1] for line in text.splitlines()[2:]]
        self.assertEqual(last, ["0", "—", "—", "—"])

    def test_every_row_has_its_headers_cell_count(self):
        for lead in ("reported", "added"):
            for show in (True, False):
                text = index_table(
                    [q("a", title="Pipe | [x]", reported="2026-09-02")], show_reports=show, lead=lead, **TABLE
                )
                cells = [line.count("|") for line in text.splitlines()]
                self.assertEqual(len(set(cells)), 1, text)


class TestRecentAndLatest(unittest.TestCase):
    def test_undated_and_future_dated_rows_never_reach_the_index(self):
        rows = [
            q("ok", reported="2026-09-01"),
            q("undated"),
            q("future", reported="2027-01-01"),
            q("stale", reported="2024-01-01"),
        ]
        text = recent_block(rows, TODAY, show_reports=False, **TABLE)
        self.assertIn("questions/ok?", text)
        for slug in ("undated", "future", "stale"):
            self.assertNotIn(f"questions/{slug}?", text)

    def test_the_readme_table_is_capped_and_points_at_the_full_list(self):
        rows = [q(f"r{i:03d}", reported="2026-09-01") for i in range(README_RECENT_ROWS + 5)]
        text = recent_block(rows, TODAY, show_reports=False, **TABLE)
        self.assertEqual(sum(1 for line in text.splitlines() if line.startswith("| 2026")), README_RECENT_ROWS)
        self.assertIn("(latest/README.md)", text)

    def test_latest_is_paged_and_every_page_links_its_neighbours(self):
        rows = [q(f"r{i:04d}", reported="2026-09-01") for i in range(LATEST_PAGE_ROWS * 2 + 1)]
        files = latest_files(rows, TODAY, api_labels={}, homes=frozenset(), show_reports=False)
        self.assertEqual(sorted(files), ["latest/README.md", "latest/page-2.md", "latest/page-3.md"])
        self.assertIn("Page 1 of 3 · [Older →](page-2.md)", files["latest/README.md"])
        self.assertIn("[← Newer](README.md) · Page 2 of 3 · [Older →](page-3.md)", files["latest/page-2.md"])
        self.assertIn("[← Newer](page-2.md) · Page 3 of 3", files["latest/page-3.md"])
        listed = sum(content.count("| [Question r") for content in files.values())
        self.assertEqual(listed, len(rows))

    def test_an_empty_year_still_renders_one_page(self):
        files = latest_files([q("undated")], TODAY, api_labels={}, homes=frozenset(), show_reports=False)
        self.assertEqual(list(files), ["latest/README.md"])
        self.assertIn("Nothing in this window.", files["latest/README.md"])


def is_index(path: str) -> bool:
    return path == "README.md" or path.startswith(("latest/", "formats/"))


class TestWholeRender(unittest.TestCase):
    def setUp(self):
        self.catalog, self.result = render()
        self.files = self.result.files

    def test_every_site_link_in_an_index_is_a_catalog_questions_tagged_url(self):
        known = {site_link(question.url) for question in self.catalog.questions}
        seen = 0
        for path, content in self.files.items():
            if not is_index(path):
                continue
            for url in site_links(content):
                if url == "https://trueinterview.io":
                    continue
                self.assertIn(url, known, path)
                seen += 1
        self.assertGreater(seen, 0)

    def test_every_relative_link_in_an_index_points_at_a_rendered_file(self):
        rendered = set(self.files) | {"LICENSE", "LICENSE-CONTENT.md", "CONTRIBUTING.md"}
        folders = {posixpath.dirname(path) for path in self.files}
        for path, content in self.files.items():
            if not is_index(path):
                continue
            for target in re.findall(r"\]\((?!https?://|#)([^)#\s]+)(?:#[^)]*)?\)", content):
                if target.startswith("../../issues"):
                    continue
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
                self.assertTrue(
                    resolved in rendered or any(folder.startswith(resolved) for folder in folders),
                    f"{path}: {target}",
                )

    def test_every_anchor_linked_anywhere_is_a_readme_heading(self):
        readme = self.files["README.md"]
        headings = {
            re.sub(r"[^a-z0-9 -]", "", heading.lower()).replace(" ", "-")
            for heading in re.findall(r"^## (.+)$", readme, re.M)
        }
        anchors = set(re.findall(r"\]\(#([a-z0-9-]+)\)", readme))
        self.assertTrue(anchors)
        self.assertLessEqual(anchors, headings)
        for path, content in self.files.items():
            if path.startswith(("latest/", "formats/", "companies/")):
                for anchor in re.findall(r"README\.md#([a-z0-9-]+)\)", content):
                    self.assertIn(anchor, headings, path)

    def test_the_ai_coding_section_lists_only_ai_coding_questions(self):
        section = block(self.files["README.md"], "gen:ai-coding")
        rows = [line for line in section.splitlines() if line.startswith("| ") and "](https://" in line]
        self.assertLessEqual(len(rows), README_AI_CODING_ROWS)
        ai = {site_link(question.url) for question in self.catalog.questions if question.type == "ai-coding"}
        for row in rows:
            self.assertIn(site_links(row)[0], ai)
        if ai:
            self.assertIn("formats/ai-coding.md", self.files)

    def test_each_format_page_exists_and_the_readme_counts_it(self):
        formats = block(self.files["README.md"], "gen:formats")
        found = re.findall(r"\[[^\]]+ \(([\d,]+)\)\]\((formats/[a-z0-9-]+\.md)\)", formats)
        self.assertTrue(found)
        for count, path in found:
            self.assertIn(path, self.files)
            kind = path.split("/")[1][:-3]
            self.assertEqual(int(count.replace(",", "")), sum(1 for x in self.catalog.questions if x.type == kind))

    def test_a_snapshot_with_counts_prints_them_on_every_table(self):
        rows = [q("a", reported="2026-09-01", reportCount=3), q("b", reported="2026-09-02")]
        _catalog, result = render(rows)
        self.assertIn("| Reports |", block(result.files["README.md"], "gen:recent"))
        self.assertIn("| Reports |", result.files["latest/README.md"])
        self.assertIn("| Reports |", result.files["companies/acme/README.md"])
        self.assertIn(",3,2026-09-01,", result.files["companies/acme/all.csv"])

    def test_a_snapshot_without_counts_prints_no_reports_column_but_keeps_the_csv_column(self):
        self.assertNotIn("| Reports |", self.files["README.md"])
        company = next(p for p in self.files if p.startswith("companies/") and p.endswith("README.md"))
        self.assertNotIn("| Reports |", self.files[company])
        self.assertIn(",Reports,", self.files[company.replace("README.md", "all.csv")].splitlines()[0])

    def test_the_new_fields_survive_a_snapshot_round_trip(self):
        rows = [q("a", reported="2026-05-03", lastReported="2026-09", reportCount=3, seniority=["senior"])]
        again = load_catalog(snapshot(Catalog(questions=rows, labels={}), "x")).questions[0]
        self.assertEqual(again, rows[0])


class TestCompanyTableLinks(unittest.TestCase):
    def test_a_site_link_is_tagged_and_a_csv_link_is_not(self):
        files = company_files(
            key="acme", name="Acme", questions=[q("a")], pages={}, api_labels={}, today=TODAY, footer=""
        )
        self.assertIn(f"(https://trueinterview.io/questions/a?{SITE_LINK_QUERY})", files["companies/acme/README.md"])
        self.assertIn(",https://trueinterview.io/questions/a\n", files["companies/acme/all.csv"])

    def test_tagging_keeps_an_existing_query_and_a_fragment(self):
        self.assertEqual(site_link("https://x.io/a?b=1#c"), f"https://x.io/a?b=1&{SITE_LINK_QUERY}#c")


class TestLinkChecker(unittest.TestCase):
    def test_only_site_links_are_collected_once_each(self):
        text = (
            "[a](https://trueinterview.io/questions/a?x=1) [b](companies/a/) "
            "[a2](https://trueinterview.io/questions/a?x=1) [gh](https://github.com/x)"
        )
        self.assertEqual(site_links(text), ["https://trueinterview.io/questions/a?x=1"])

    def test_a_refusal_is_unverified_never_dead_and_never_ok(self):
        self.assertEqual(
            [classify(status) for status in (200, 404, 410, 500, None, 401, 403, 429)],
            ["ok", "dead", "dead", "dead", "dead", "unverified", "unverified", "unverified"],
        )

    def test_a_blip_is_retried_once_before_it_counts_as_dead(self):
        answers = {"u1": [None, 200], "u2": [503, 503], "u3": [404]}
        calls: list[str] = []

        def fetch(url):
            calls.append(url)
            return answers[url].pop(0)

        results = check(["u1", "u2", "u3"], fetch=fetch, pause=0)
        self.assertEqual([url for url, _status in results["ok"]], ["u1"])
        self.assertEqual(results["dead"], [("u2", 503), ("u3", 404)])
        self.assertEqual(calls, ["u1", "u1", "u2", "u2", "u3"])


if __name__ == "__main__":
    unittest.main()
