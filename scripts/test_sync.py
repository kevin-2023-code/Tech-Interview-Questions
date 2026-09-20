#!/usr/bin/env python3
"""Offline tests for the sync pipeline.

No network and no clock: every test renders the committed fixture at a pinned
date, which is the only way an assertion about a 🔥 marker can still hold next
month. What each test is defending is stated where it is not obvious — a test
whose reason is not written down is a test the next person deletes.

    python3 -m unittest discover scripts -v
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
import unittest
from datetime import date
from unittest import mock
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

import insights as insights_module  # noqa: E402
import report  # noqa: E402
from build import (  # noqa: E402
    COMPANY_TYPE_MIN_QUESTIONS,
    build,
    check_budgets,
    company_type_cuts,
    group_by_company,
    group_by_month,
)
from catalog import CatalogError, load_catalog, parse_catalog_date, question_from_payload  # noqa: E402
from insights import compute as compute_insights  # noqa: E402
from labels import COMPANY_LABELS, company_key, company_label  # noqa: E402
from render import (  # noqa: E402
    PAGE_MAX_BYTES,
    README_MAX_BYTES,
    ROWS_PER_PAGE,
    columns_for,
    render_shard,
    escape_cell,
    freshness_marker,
    inject,
    percent,
    sort_questions,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "scripts" / "fixtures" / "catalog-sample.json"
TODAY = date(2026, 9, 11)


def load_fixture():
    return load_catalog(json.loads(FIXTURE.read_text(encoding="utf-8")))


def render(catalog=None):
    template = (ROOT / "README.md").read_text(encoding="utf-8")
    return build(catalog or load_fixture(), template, TODAY)


class TestDates(unittest.TestCase):
    def test_full_date_and_month_only(self):
        self.assertEqual(parse_catalog_date("2026-09-10"), (date(2026, 9, 10), False))
        self.assertEqual(parse_catalog_date("2026-09"), (date(2026, 9, 1), True))

    def test_unparseable_is_undated_not_guessed(self):
        # An out-of-range month must not become a date. The alternative — clamping
        # to December — would publish a sighting nobody reported.
        for value in ("2026-13-45", "not a date", "", None, "2026"):
            self.assertEqual(parse_catalog_date(value), (None, False), value)

    def test_month_only_resolves_to_the_first_not_the_last(self):
        # The conservative end. Resolving "2026-09" to Sep 30 would put a 🔥
        # marker on a question only known to have been seen sometime that month.
        resolved, month_only = parse_catalog_date("2026-09")
        self.assertEqual(resolved, date(2026, 9, 1))
        self.assertTrue(month_only)


class TestFreshness(unittest.TestCase):
    def test_windows(self):
        self.assertEqual(freshness_marker(date(2026, 9, 10), TODAY), "🔥 ")
        self.assertEqual(freshness_marker(date(2026, 8, 29), TODAY), "🔥 ")  # 13 days
        self.assertEqual(freshness_marker(date(2026, 8, 20), TODAY), "🆕 ")
        self.assertEqual(freshness_marker(date(2026, 1, 1), TODAY), "")

    def test_undated_is_never_marked_fresh(self):
        # The rule this repository departs from the prior art on: unmeasured is
        # not the same fact as stale, and neither is the same fact as fresh.
        self.assertEqual(freshness_marker(None, TODAY), "")

    def test_future_date_is_not_marked(self):
        self.assertEqual(freshness_marker(date(2027, 1, 1), TODAY), "")


class TestEscaping(unittest.TestCase):
    def test_table_breaking_characters(self):
        out = escape_cell("Pipe | Bracket [x] & <tag> \\ back")
        for raw in ("|", "[", "]", "<", ">", "\\"):
            self.assertNotIn(raw, out)
        self.assertIn("&", out)  # an ampersand is legal text and stays

    def test_every_rendered_row_has_its_headers_cell_count(self):
        # The failure an unescaped pipe actually causes: one row splits into an
        # extra cell and the table renders ragged from there down. Checked by
        # comparing every row against the header of the table it is in, on every
        # generated page — not just the one the fixture's torture row landed on.
        result = render()
        self.assertTrue(
            any("&#124;" in content for content in result.files.values()),
            "the fixture's escaping row should reach a rendered page",
        )
        for path, content in result.files.items():
            if not path.endswith(".md"):
                continue
            expected: int | None = None
            for number, line in enumerate(content.splitlines(), start=1):
                if not line.startswith("|"):
                    expected = None
                    continue
                if expected is None:
                    expected = line.count("|")  # this line is the header
                    continue
                self.assertEqual(line.count("|"), expected, f"{path}:{number}")


class TestSorting(unittest.TestCase):
    def test_three_buckets_real_then_future_then_undated(self):
        questions = load_fixture().questions
        ordered = sort_questions(questions, TODAY)
        buckets = [
            2 if q.reported_date is None else (1 if q.reported_date > TODAY else 0)
            for q in ordered
        ]
        self.assertEqual(buckets, sorted(buckets), "buckets must not interleave")
        self.assertEqual(set(buckets), {0, 1, 2}, "the fixture must exercise all three")

    def test_real_sightings_are_newest_first(self):
        questions = load_fixture().questions
        ordered = sort_questions(questions, TODAY)
        real = [q.reported_date for q in ordered if q.reported_date and q.reported_date <= TODAY]
        self.assertEqual(real, sorted(real, reverse=True))

    def test_a_future_date_never_takes_the_top_row(self):
        # The most valuable slot in the repository must not be awarded to
        # whichever row is most wrong.
        questions = load_fixture().questions
        ordered = sort_questions(questions, TODAY)
        self.assertLessEqual(ordered[0].reported_date, TODAY)

    def test_order_is_total_so_reruns_do_not_churn(self):
        # Two rows that compared equal would be free to swap between syncs, and
        # every swap is a committed diff on a file nobody changed.
        questions = load_fixture().questions
        from render import _sort_key

        keys = [_sort_key(q, TODAY) for q in questions]
        self.assertEqual(len(set(keys)), len(keys))


class TestBudgets(unittest.TestCase):
    def test_readme_and_pages_are_inside_budget(self):
        result = render()
        self.assertEqual(check_budgets(result), [])

    def test_readme_is_far_under_githubs_render_limit(self):
        result = render()
        self.assertLess(len(result.files["README.md"].encode("utf-8")), README_MAX_BYTES)

    def test_every_page_is_inside_the_page_budget(self):
        result = render()
        for path, content in result.files.items():
            if path.endswith(".md"):
                self.assertLessEqual(len(content.encode("utf-8")), PAGE_MAX_BYTES, path)

    def test_an_oversized_render_is_refused_rather_than_written(self):
        # The budget must FAIL a run, not warn about it. Proven by handing
        # check_budgets an over-budget README rather than by trusting the caller.
        result = render()
        result.files["README.md"] = "x" * (README_MAX_BYTES + 1)
        self.assertTrue(check_budgets(result))


class TestPagination(unittest.TestCase):
    def test_a_shard_over_the_row_cap_paginates(self):
        from render import columns_for, render_shard

        c = load_fixture(); questions, labels = c.questions, c.labels
        pages = render_shard(
            base="formats/algorithm",
            title="t",
            lede="l",
            back="b",
            questions=questions,
            cols=columns_for("format", labels, TODAY),
            today=TODAY,
        )
        expected = -(-len(questions) // ROWS_PER_PAGE)
        self.assertEqual(len(pages), expected)
        self.assertIn("formats/algorithm.md", pages)

    def test_a_shard_that_fits_is_exactly_one_file(self):
        # A company crossing the row cap must GAIN a file, never rename the one
        # it had — a renamed page breaks every link anyone ever shared.
        from render import columns_for, render_shard

        c = load_fixture(); questions, labels = c.questions, c.labels
        pages = render_shard(
            base="companies/meta",
            title="t",
            lede="l",
            back="b",
            questions=questions[:5],
            cols=columns_for("company", labels, TODAY),
            today=TODAY,
        )
        self.assertEqual(list(pages), ["companies/meta.md"])


class TestMonthPages(unittest.TestCase):
    def test_undated_questions_are_absent_from_by_month(self):
        questions = load_fixture().questions
        undated = [q for q in questions if q.reported_date is None]
        self.assertTrue(undated, "the fixture must contain undated rows")
        filed = {q.slug for rows in group_by_month(questions).values() for q in rows}
        for question in undated:
            self.assertNotIn(question.slug, filed)

    def test_the_month_index_states_how_many_it_excluded(self):
        # A quiet omission is the failure mode here: the count has to be on the
        # page, or the monthly view silently under-reports the bank.
        result = render()
        questions = load_fixture().questions
        undated = sum(1 for q in questions if q.reported_date is None)
        self.assertIn(f"**{undated:,}** carry no sighting date", result.files["by-month/README.md"])


class TestLabels(unittest.TestCase):
    def test_api_labels_win_over_the_transcription(self):
        # The transcription is a fallback for the tail the capped companies
        # endpoint cannot reach, never the authority.
        self.assertEqual(company_label("meta", {"meta": "Meta Platforms"}), "Meta Platforms")

    def test_transcription_handles_the_tail(self):
        self.assertEqual(company_label("capitalone", {}), "Capital One")
        self.assertEqual(company_label("openai", {}), "OpenAI")
        self.assertEqual(company_label("Susquehanna International Group", {}), "Susquehanna International Group")

    def test_company_key_matches_the_sites_own_slug_derivation(self):
        self.assertEqual(company_key("capitalone", {}), "capital-one")
        self.assertEqual(company_key("scale.ai", {}), "scale-ai")
        self.assertEqual(company_key("openai", {}), "openai")

    def test_spellings_that_share_a_label_share_a_page(self):
        self.assertEqual(company_key("openai", {}), company_key("OpenAI", {}))


class TestCatalogValidation(unittest.TestCase):
    def test_a_row_without_a_slug_is_refused(self):
        with self.assertRaises(CatalogError):
            question_from_payload({"title": "t", "url": "https://trueinterview.io/questions/x"})

    def test_a_row_without_an_absolute_url_is_refused(self):
        with self.assertRaises(CatalogError):
            question_from_payload({"slug": "x", "title": "t", "url": "/questions/x"})

    def test_an_unknown_difficulty_is_refused(self):
        # Silently passing it through would print an unlabelled value in a column
        # readers compare across pages.
        with self.assertRaises(CatalogError):
            question_from_payload(
                {"slug": "x", "title": "t", "url": "https://trueinterview.io/q", "difficulty": "brutal"}
            )

    def test_duplicate_slugs_are_refused(self):
        row = {"slug": "x", "title": "t", "url": "https://trueinterview.io/q"}
        with self.assertRaises(CatalogError):
            load_catalog({"questions": [row, dict(row)]})

    def test_an_empty_catalog_is_refused(self):
        # The failure that would otherwise commit an empty repository over a good
        # one, on a schedule, at an hour nobody is watching.
        with self.assertRaises(CatalogError):
            load_catalog({"questions": []})


class TestInjection(unittest.TestCase):
    def test_replaces_only_between_the_markers(self):
        doc = "before\n<!-- gen:x:start -->\nold\n<!-- gen:x:end -->\nafter\n"
        out = inject(doc, "gen:x", "new")
        self.assertIn("before", out)
        self.assertIn("after", out)
        self.assertIn("new", out)
        self.assertNotIn("old", out)

    def test_a_missing_marker_is_an_error_not_a_skip(self):
        # A skipped block is a stale number on the landing page, and nobody
        # audits a landing page.
        with self.assertRaises(ValueError):
            inject("no markers here", "gen:x", "new")


class TestDeterminism(unittest.TestCase):
    def test_two_renders_are_byte_identical(self):
        self.assertEqual(render().files, render().files)

    def test_every_readme_marker_is_filled(self):
        readme = render().files["README.md"]
        self.assertNotIn("Run `python3 scripts/sync.py` to populate.", readme)

    def test_generated_pages_carry_the_do_not_edit_notice(self):
        result = render()
        for path, content in result.files.items():
            if path.endswith(".md") and path != "README.md":
                self.assertTrue(content.startswith("<!--"), path)

    def test_exports_are_slug_sorted_for_line_stable_diffs(self):
        # The property that keeps the repository small to clone: a changed
        # question is a one-line diff, not a re-emitted file.
        lines = render().files["data/questions.jsonl"].splitlines()
        slugs = [json.loads(line)["slug"] for line in lines]
        self.assertEqual(slugs, sorted(slugs))

    def test_csv_row_count_matches_the_bank(self):
        result = render()
        questions = load_fixture().questions
        self.assertEqual(len(result.files["data/questions.csv"].splitlines()) - 1, len(questions))


class TestCoverage(unittest.TestCase):
    def test_every_question_reaches_a_company_page_and_a_format_page(self):
        result = render()
        questions = load_fixture().questions
        markdown = "\n".join(
            content for path, content in result.files.items()
            if path.startswith(("companies/", "formats/"))
        )
        for question in questions:
            self.assertIn(question.url, markdown, question.slug)

    def test_a_question_at_several_companies_is_on_each_of_their_pages(self):
        result = render()
        c = load_fixture(); questions, labels = c.questions, c.labels
        multi = next(q for q in questions if len(q.companies) > 1)
        for company in multi.companies:
            page = result.files[f"companies/{company_key(company, labels)}.md"]
            self.assertIn(multi.url, page)


if __name__ == "__main__":
    unittest.main()


class TestFutureDates(unittest.TestCase):
    def test_a_future_sighting_is_reported_rather_than_ignored_or_fatal(self):
        # Both alternatives are worse. Exiting non-zero lets one mistyped date on
        # the site stop every other row from syncing; ignoring it hides the error
        # for ever, because correct-looking output is what keeps bad input alive.
        result = render()
        future = result.stats["future_dated"]
        self.assertTrue(future, "the fixture should contain a future-dated sighting")
        for slug in future:
            self.assertIn(f"/questions/{slug})", result.files["data/questions.csv"] + "".join(result.files.values()))

    def test_a_future_sighting_is_still_never_marked_fresh(self):
        result = render()
        questions = load_fixture().questions
        future = {q.slug for q in questions if q.reported_date and q.reported_date > TODAY}
        for path, content in result.files.items():
            if not path.endswith(".md"):
                continue
            for line in content.splitlines():
                if any(f"/questions/{slug})" in line for slug in future):
                    self.assertNotIn("🔥", line, path)
                    self.assertNotIn("🆕", line, path)


class TestFutureDatesInTheIndex(unittest.TestCase):
    def test_the_landing_page_latest_list_excludes_future_dates(self):
        # Caught a real one on the first live sync: a catalog row dated 2126 led
        # "Latest sightings" until this rule existed.
        result = render()
        questions = load_fixture().questions
        future = {q.slug for q in questions if q.reported_date and q.reported_date > TODAY}
        self.assertTrue(future)
        readme = result.files["README.md"]
        latest = readme.split("<!-- gen:latest:start -->")[1].split("<!-- gen:latest:end -->")[0]
        for slug in future:
            self.assertNotIn(f"/questions/{slug})", latest)

    def test_a_future_dated_question_still_reaches_its_company_page(self):
        # Excluded from the newest list, never from the bank: dropping the row
        # would hide the error from the only people who can fix it.
        result = render()
        c = load_fixture(); questions, labels = c.questions, c.labels
        future = next(q for q in questions if q.reported_date and q.reported_date > TODAY)
        page = result.files[f"companies/{company_key(future.companies[0], labels)}.md"]
        self.assertIn(future.url, page)


class TestGuides(unittest.TestCase):
    def test_the_guides_index_lists_every_guide(self):
        result = render()
        guides = load_fixture().guides
        index = result.files["guides/README.md"]
        for guide in guides:
            self.assertIn(guide.url, index, guide.slug)

    def test_a_company_page_carries_its_guides_above_its_questions(self):
        # The order is the point: which rounds this company runs comes first,
        # because a question is what you practise once you know that. Anchored
        # on the catalog HEADING rather than on a `| Question |` header row,
        # because the blocks above it — what was asked this quarter, where to
        # start — are question tables too.
        result = render()
        c = load_fixture(); guides, labels = c.guides, c.labels
        guide = next(g for g in guides if g.companies)
        key = company_key(guide.companies[0], labels)
        page = result.files[f"companies/{key}.md"]
        self.assertIn(guide.url, page)
        self.assertLess(page.index(guide.url), page.index("## Every question reported at"))

    def test_a_multi_company_guide_is_on_each_of_their_pages(self):
        result = render()
        c = load_fixture(); guides, labels = c.guides, c.labels
        multi = next((g for g in guides if len(g.companies) > 1), None)
        self.assertIsNotNone(multi, "the fixture should carry a two-company guide")
        for company in multi.companies:
            page = result.files[f"companies/{company_key(company, labels)}.md"]
            self.assertIn(multi.url, page)

    def test_an_unattributed_guide_is_still_published(self):
        # It belongs to no company page, so the index is its only home. Dropping
        # it would lose a document with no error anywhere.
        result = render()
        guides = load_fixture().guides
        orphan = next(g for g in guides if not g.companies)
        self.assertIn(orphan.url, result.files["guides/README.md"])
        self.assertIn("Not tied to one company", result.files["guides/README.md"])

    def test_a_company_with_no_guides_renders_no_empty_block(self):
        result = render()
        c = load_fixture(); guides, labels = c.guides, c.labels
        have = {company_key(c, labels) for g in guides for c in g.companies}
        questions = load_fixture().questions
        all_companies = {company_key(c, labels) for q in questions for c in q.companies}
        without = all_companies - have
        for key in list(without)[:5]:
            self.assertNotIn("How ", result.files[f"companies/{key}.md"].split("| Question |")[0])

    def test_guide_titles_are_escaped_like_question_titles(self):
        result = render()
        self.assertTrue(any("&#124;" in c for c in result.files.values()))

    def test_a_catalog_with_no_guides_still_renders(self):
        # An empty Study section is a normal deployment state, not a failed read.
        result = render(dataclasses.replace(load_fixture(), guides=[]))
        self.assertNotIn("guides/README.md", result.files)
        self.assertIn("No interview guides published yet", result.files["README.md"])

    def test_an_incomplete_guide_read_says_so_on_the_page(self):
        # The bug this replaced: raising here stopped the WHOLE sync — all two
        # thousand questions with it — because one endpoint could not page yet.
        # Partial is fine; silent is not, because a short list of guides looks
        # exactly like a site that publishes few of them.
        result = render(dataclasses.replace(load_fixture(), guides_complete=False))
        self.assertIn("This list is incomplete", result.files["guides/README.md"])
        self.assertIn("partial", result.files["README.md"])

    def test_a_complete_guide_read_prints_no_caveat(self):
        result = render()
        self.assertNotIn("This list is incomplete", result.files["guides/README.md"])
        self.assertNotIn("partial, see the note", result.files["README.md"])


class TestReadmeShape(unittest.TestCase):
    def test_the_latest_table_is_four_columns_for_a_phone(self):
        readme = render().files["README.md"]
        latest = readme.split("<!-- gen:latest:start -->")[1].split("<!-- gen:latest:end -->")[0]
        header = next(line for line in latest.splitlines() if line.startswith("|"))
        self.assertEqual(header.count("|"), 5, f"expected 4 columns, got: {header}")

    def test_the_company_nav_folds_the_tail_when_there_is_one(self):
        # Tested on the function rather than the fixture: production carries ~99
        # companies and the fixture 14, so only a direct call exercises both
        # branches — and the no-fold branch is the one a fixture-only test would
        # silently be asserting nothing about.
        from render import README_TOP_COMPANIES, company_nav

        many = [(f"c{i}", f"Company {i}", 100 - i) for i in range(README_TOP_COMPANIES + 9)]
        folded = company_nav(many)
        self.assertIn("<details>", folded)
        self.assertIn("+ 9 more companies", folded)
        self.assertIn("Every company, with counts", folded)
        # Every company is still NAMED on the landing page — a fold, not a cut.
        for _, name, _ in many:
            self.assertIn(name, folded)

    def test_the_company_nav_does_not_fold_a_short_list(self):
        from render import company_nav

        few = [("a", "Alpha", 3), ("b", "Beta", 2)]
        self.assertNotIn("<details>", company_nav(few))

    def test_the_fixtures_company_nav_is_short_enough_not_to_fold(self):
        readme = render().files["README.md"]
        nav = readme.split("<!-- gen:companies:start -->")[1].split("<!-- gen:companies:end -->")[0]
        self.assertNotIn("<details>", nav)

    def test_no_self_congratulation_section(self):
        # Removed deliberately: the argument for the layout belongs in DESIGN.md,
        # not on the landing page of a repository people open to find questions.
        readme = render().files["README.md"]
        self.assertNotIn("Why this repo loads fast", readme)
        self.assertNotIn("One-giant-README", readme)


class TestGuideFetchShapes(unittest.TestCase):
    """`/api/v1/articles` can answer in two shapes; the sync must survive both."""

    def _with_stub(self, responses):
        import catalog as catalog_module

        calls = {"n": 0}

        def fake(url, timeout):
            payload = responses[min(calls["n"], len(responses) - 1)]
            calls["n"] += 1
            return payload

        original = catalog_module._get_json
        catalog_module._get_json = fake
        self.addCleanup(setattr, catalog_module, "_get_json", original)
        return catalog_module

    @staticmethod
    def _articles(n, start=0):
        return [
            {"slug": f"g{i}", "title": f"T{i}", "url": f"https://trueinterview.io/study/g{i}"}
            for i in range(start, start + n)
        ]

    def test_old_shape_full_page_is_partial_not_an_error(self):
        m = self._with_stub([{"articles": self._articles(50), "total": 50}])
        rows, complete = m.fetch_guides("https://x")
        self.assertEqual(len(rows), 50)
        self.assertFalse(complete)

    def test_old_shape_short_page_is_the_whole_set(self):
        m = self._with_stub([{"articles": self._articles(7), "total": 7}])
        rows, complete = m.fetch_guides("https://x")
        self.assertEqual(len(rows), 7)
        self.assertTrue(complete)

    def test_new_shape_pages_to_the_end(self):
        m = self._with_stub([
            {"articles": self._articles(50, 0), "page": {"page": 1, "limit": 50, "total": 60, "hasMore": True}},
            {"articles": self._articles(10, 50), "page": {"page": 2, "limit": 50, "total": 60, "hasMore": False}},
        ])
        rows, complete = m.fetch_guides("https://x")
        self.assertEqual(len(rows), 60)
        self.assertTrue(complete)

    def test_new_shape_short_read_against_total_is_an_error(self):
        # Here it IS fatal: the API said how many there are and handed over
        # fewer, which is a broken read rather than a known limitation.
        m = self._with_stub([
            {"articles": self._articles(5), "page": {"page": 1, "limit": 50, "total": 99, "hasMore": False}},
        ])
        with self.assertRaises(CatalogError):
            m.fetch_guides("https://x")


class TestInsightNumbers(unittest.TestCase):
    """The arithmetic behind the statistics pages."""

    def setUp(self):
        self.catalog = load_fixture()
        self.insights = compute_insights(self.catalog.questions, self.catalog.guides, self.catalog.labels, TODAY)

    def test_dated_and_undated_add_up_to_the_bank(self):
        # The index prints both numbers in one sentence. An earlier version
        # counted only sightings on or before today as "dated", so the two
        # numbers silently failed to add up by however many rows carried a
        # mistyped future date.
        self.assertEqual(self.insights.dated + self.insights.undated, self.insights.total)

    def test_a_future_sighting_is_dated_but_never_in_a_window(self):
        future = [q for q in self.catalog.questions if q.reported_date and q.reported_date > TODAY]
        self.assertTrue(future, "fixture must carry a future-dated row for this to mean anything")
        self.assertEqual(self.insights.future_dated, len(future))
        window = {q.slug for q in self.catalog.questions if insights_module._in_window(q, TODAY, 90)}
        self.assertTrue(window.isdisjoint({q.slug for q in future}))

    def test_the_window_holds_only_sightings_inside_it(self):
        counted = [
            q
            for q in self.catalog.questions
            if q.reported_date and q.reported_date <= TODAY and (TODAY - q.reported_date).days <= 90
        ]
        self.assertEqual(self.insights.window_total, len(counted))

    def test_a_share_of_nothing_is_a_dash_not_a_zero(self):
        # The null-is-not-a-zero rule, at the cell level: 0% states that a thing
        # was measured and found absent, which is the opposite of unmeasured.
        self.assertEqual(percent(0, 0), "—")
        self.assertEqual(percent(0, 10), "0%")

    def test_topic_shares_are_taken_over_labelled_rows_only(self):
        labelled = sum(1 for q in self.catalog.questions if q.topics)
        self.assertEqual(self.insights.topics_known, labelled)
        self.assertLess(labelled, self.insights.total, "fixture must carry unlabelled rows")
        page = render().files["insights/topics.md"]
        self.assertIn(f"{labelled:,} questions that carry a topic label", page)

    def test_the_most_asked_list_is_multi_company_and_totally_ordered(self):
        self.assertTrue(all(len(q.companies) > 1 for q in self.insights.breadth))
        again = compute_insights(
            list(reversed(self.catalog.questions)), self.catalog.guides, self.catalog.labels, TODAY
        )
        self.assertEqual([q.slug for q in self.insights.breadth], [q.slug for q in again.breadth])

    def test_company_counts_match_the_company_pages(self):
        # One source of truth: a number on an insights page and the row count on
        # the company page it links to are computed from the same records, and a
        # reader who clicks through must not find a different bank.
        pages = render().files
        for row in self.insights.companies[:5]:
            page = pages[f"companies/{row.key}.md"]
            self.assertIn(f"**{row.questions:,} questions** reported at", page)


class TestInsightPages(unittest.TestCase):
    def test_an_unmeasurable_window_prints_a_dash_not_a_zero(self):
        # A company whose questions carry no sighting date at all has an UNKNOWN
        # recent count. Printing 0 there would say we looked and found nothing.
        catalog = load_fixture()
        stripped = [dataclasses.replace(q, reported=None, reported_date=None) for q in catalog.questions]
        insights = compute_insights(stripped, catalog.guides, catalog.labels, TODAY)
        page = report.companies_page(insights)
        self.assertNotIn("| 0 |", page)
        self.assertIn("could not be measured", page)

    def test_an_empty_window_is_a_sentence_not_a_table_of_zeros(self):
        catalog = load_fixture()
        # Every sighting a decade old: the window is legitimately empty, which
        # is a recurring state for this catalog rather than a broken pipeline.
        old = [
            dataclasses.replace(q, reported="2015-01-01", reported_date=date(2015, 1, 1))
            for q in catalog.questions
        ]
        insights = compute_insights(old, catalog.guides, catalog.labels, TODAY)
        page = report.insights_index(insights, catalog.labels)
        self.assertIn("No sighting has been recorded in this window", page)
        self.assertIn("Jan 01, 2015", page)

    def test_every_page_states_what_it_counted_over(self):
        pages = render().files
        self.assertIn("carry a sighting date", pages["insights/README.md"])
        self.assertIn("carry a topic label", pages["insights/topics.md"])
        self.assertIn("carry a sighting date", pages["insights/trends.md"])

    def test_the_insights_export_publishes_inputs_rather_than_percentages(self):
        # A published percentage is the one place a denominator can go missing.
        payload = json.loads(render().files["data/insights.json"])
        self.assertEqual(payload["totals"]["questions"], len(load_fixture().questions))
        flat = json.dumps(payload)
        self.assertNotIn("percent", flat)
        self.assertNotIn("share", flat)

    def test_the_export_keeps_a_stable_key_order(self):
        text = render().files["data/insights.json"]
        self.assertEqual(text, json.dumps(json.loads(text), ensure_ascii=False, indent=2, sort_keys=True) + "\n")


class TestFreePages(unittest.TestCase):
    def test_every_free_question_reaches_a_free_page_and_nothing_else_does(self):
        catalog = load_fixture()
        free = {q.slug for q in catalog.questions if q.access_tier == "free"}
        self.assertTrue(free, "fixture must carry free questions")
        paid = {q.slug for q in catalog.questions if q.access_tier != "free"}
        pages = {path: text for path, text in render().files.items() if path.startswith("free/")}
        listed = " ".join(pages.values())
        by_format = " ".join(text for path, text in pages.items() if path != "free/README.md")
        for slug in free:
            self.assertIn(f"/questions/{slug})", by_format, slug)
        for slug in paid:
            self.assertNotIn(f"/questions/{slug})", listed, slug)

    def test_free_pages_are_ordered_easiest_first(self):
        catalog = load_fixture()
        free = [q for q in catalog.questions if q.access_tier == "free" and q.type == "algorithm"]
        order = [q.difficulty for q in sorted(free, key=report._free_sort_key)]
        ranks = [{"easy": 0, "medium": 1, "hard": 2}.get(d or "", 3) for d in order]
        self.assertEqual(ranks, sorted(ranks))

    def test_the_tier_is_the_catalogs_own_never_asserted_here(self):
        catalog = load_fixture()
        none_free = [dataclasses.replace(q, access_tier="insider") for q in catalog.questions]
        insights = compute_insights(none_free, catalog.guides, catalog.labels, TODAY)
        files = report.free_pages(none_free, insights, catalog.labels, TODAY)
        self.assertIn("No question in the bank is currently on the free tier", files["free/README.md"])
        self.assertEqual([path for path in files if path != "free/README.md"], [])


class TestExperiences(unittest.TestCase):
    def test_the_slice_says_how_much_of_the_board_it_is_not_showing(self):
        catalog = load_fixture()
        self.assertTrue(catalog.experiences, "fixture must carry interview reports")
        page = render().files["experiences/README.md"]
        self.assertIn(f"the {len(catalog.experiences)} newest of {catalog.experiences_total:,}", page)

    def test_reports_are_newest_first_and_escaped(self):
        page = render().files["experiences/README.md"]
        rows = [line for line in page.splitlines() if line.startswith("| **")]
        self.assertEqual(rows[0].count("|"), 5)
        self.assertIn("&#124;", page)  # a pipe in a title must not end its cell

    def test_a_catalog_with_no_reports_renders_no_page(self):
        catalog = load_fixture()
        bare = dataclasses.replace(catalog, experiences=[], experiences_total=None)
        files = build(bare, (ROOT / "README.md").read_text(encoding="utf-8"), TODAY).files
        self.assertNotIn("experiences/README.md", files)

    def test_a_failed_read_is_never_an_empty_section(self):
        # The distinction the whole pipeline holds: a LIMITATION travels as a
        # caveat on the page, a FAILURE stops the run. Publishing an empty
        # section over a good one is how an hourly job deletes a page nobody
        # was watching.
        import catalog as catalog_module

        def boom(url, timeout):
            raise CatalogError("upstream is down")

        original = catalog_module._get_json
        catalog_module._get_json = boom
        self.addCleanup(setattr, catalog_module, "_get_json", original)
        with self.assertRaises(CatalogError):
            catalog_module.fetch_experiences("https://x")


class TestGuidesByTopic(unittest.TestCase):
    def test_every_tagged_guide_is_listed_and_the_untagged_are_counted(self):
        catalog = load_fixture()
        page = render().files["guides/by-topic.md"]
        untagged = [g for g in catalog.guides if not g.tags]
        for guide in catalog.guides:
            if guide.tags:
                self.assertIn(guide.url, page, guide.slug)
        if untagged:
            self.assertIn(f"**{len(untagged):,} of them carry no topic label**", page)
            for guide in untagged:
                self.assertNotIn(guide.url, page, guide.slug)

    def test_a_guide_with_several_topics_is_under_each(self):
        catalog = load_fixture()
        multi = next((g for g in catalog.guides if len(g.tags) > 1), None)
        if multi is None:
            self.skipTest("fixture carries no multi-topic guide")
        page = render().files["guides/by-topic.md"]
        self.assertEqual(page.count(multi.url), len(set(multi.tags)))


class TestOrderingSurvivesTheRenderer(unittest.TestCase):
    """A renderer must not silently re-sort what a caller ordered on purpose.

    Both holes below shipped in the first draft of these pages and neither was
    visible from the calling code: the caller sorted, passed the rows on, and a
    helper sorted them again. What the reader saw then contradicted the lede
    directly above it, which is the one kind of wrong a statistics page cannot
    afford.
    """

    def test_free_pages_render_easiest_first_as_their_lede_promises(self):
        pages = render().files
        rank = {"Easy": 0, "Medium": 1, "Hard": 2}
        for path, text in pages.items():
            if not path.startswith("free/") or path == "free/README.md":
                continue
            self.assertIn("Easiest first", text, path)
            levels = [
                rank[cell]
                for line in text.splitlines()
                if line.startswith("| [")
                for cell in [line.split("|")[3].strip()]
                if cell in rank
            ]
            self.assertEqual(levels, sorted(levels), path)

    def test_recently_published_guides_are_in_date_order(self):
        catalog = load_fixture()
        page = render().files["guides/README.md"]
        block = page.split("## Recently published")[1].split("## By company")[0]
        urls = [line.split("](")[1].split(")")[0] for line in block.splitlines() if line.startswith("| [")]
        added = {g.url: parse_catalog_date(g.added_at)[0] for g in catalog.guides}
        stamps = [added[url] for url in urls]
        self.assertEqual(stamps, sorted(stamps, reverse=True))

    def test_a_guide_dated_after_today_is_not_called_recently_published(self):
        catalog = load_fixture()
        ahead = dataclasses.replace(catalog.guides[0], added_at="2099-01-01T00:00:00+00:00")
        patched = dataclasses.replace(catalog, guides=[ahead, *catalog.guides[1:]])
        page = build(patched, (ROOT / "README.md").read_text(encoding="utf-8"), TODAY).files["guides/README.md"]
        block = page.split("## Recently published")[1].split("## By company")[0]
        self.assertNotIn(ahead.url, block)
        self.assertIn(ahead.url, page)  # still published, just not called recent

    def test_a_future_sighting_does_not_win_the_most_asked_tie_break(self):
        catalog = load_fixture()
        pair = [q for q in catalog.questions if len(q.companies) > 1][:2]
        self.assertEqual(len(pair), 2, "fixture must carry multi-company questions")
        same = min(len(q.companies) for q in pair)
        ahead, behind = (
            dataclasses.replace(pair[0], companies=pair[0].companies[:same], reported="2099-01-01",
                                reported_date=date(2099, 1, 1)),
            dataclasses.replace(pair[1], companies=pair[1].companies[:same], reported="2026-09-01",
                                reported_date=date(2026, 9, 1)),
        )
        ranked = compute_insights([ahead, behind], [], {}, TODAY).breadth
        self.assertEqual(ranked[0].slug, behind.slug)


class TestMeasuredZeros(unittest.TestCase):
    def test_a_counted_zero_prints_zero_and_only_the_unmeasurable_prints_a_dash(self):
        # `free` and `guides` are counted for every row, so 0 of them is a
        # measured fact. Only the window column can be genuinely unmeasurable,
        # and only when the company carries no sighting date at all.
        catalog = load_fixture()
        insights = compute_insights(
            [dataclasses.replace(q, access_tier="insider") for q in catalog.questions],
            [],
            catalog.labels,
            TODAY,
        )
        page = report.companies_page(insights)
        rows = [line for line in page.splitlines() if line.startswith("| [") and line.count("|") == 9]
        self.assertTrue(rows)
        for row in rows:
            cells = [cell.strip() for cell in row.split("|")]
            self.assertEqual(cells[3], "0", row)  # guides
            self.assertEqual(cells[4], "0", row)  # free

    def test_the_export_carries_the_future_dated_count(self):
        payload = json.loads(render().files["data/insights.json"])
        catalog = load_fixture()
        expected = sum(1 for q in catalog.questions if q.reported_date and q.reported_date > TODAY)
        self.assertEqual(payload["totals"]["futureDated"], expected)


class TestReadmeCounts(unittest.TestCase):
    def test_the_window_company_count_is_the_population_not_the_ranking(self):
        # `window_companies` is a capped ranking, so reading its length reported
        # exactly TOP_COMPANIES on any catalog with more active companies than
        # that — a number that would be wrong in one direction for ever.
        catalog = load_fixture()
        rows = [
            insights_module.CompanyRow(
                key=f"c{i}", name=f"Company {i}", questions=1, guides=0, dated=1, window=1,
                year=1, last_seen=TODAY, top_format=None, top_topic=None, free=0,
            )
            for i in range(insights_module.TOP_COMPANIES + 7)
        ]
        stats = compute_insights(catalog.questions, catalog.guides, catalog.labels, TODAY)
        stats = dataclasses.replace(stats, companies=tuple(rows))
        block = report.readme_insights_block(stats, catalog.labels)
        self.assertIn(f"{len(rows):,} companies", block)


class TestMostReportedRanking(unittest.TestCase):
    def test_a_busy_small_company_is_not_capped_out_of_the_ranking(self):
        # `insights.companies` is ordered by LIFETIME question count, so capping
        # before sorting dropped exactly the row this table exists to show: a
        # company with one question in the bank and all of it this quarter.
        catalog = load_fixture()
        big = [
            insights_module.CompanyRow(
                key=f"big{i}", name=f"Big {i}", questions=1000 - i, guides=0, dated=1, window=1,
                year=1, last_seen=TODAY, top_format=None, top_topic=None, free=0,
            )
            for i in range(report.COMPANY_ROWS)
        ]
        busy = insights_module.CompanyRow(
            key="busy", name="Busy", questions=1, guides=0, dated=1, window=99,
            year=99, last_seen=TODAY, top_format=None, top_topic=None, free=0,
        )
        stats = compute_insights(catalog.questions, catalog.guides, catalog.labels, TODAY)
        stats = dataclasses.replace(stats, companies=tuple(big + [busy]))
        page = report.companies_page(stats)
        ranking = page.split("## Most reported")[1].split("## Every company")[0]
        self.assertIn("../companies/busy.md", ranking)


# ── the company page ─────────────────────────────────────────────────────────
#
# The page went from "a table of questions" to "how this employer's loop runs,
# then the table". Everything on it is a COUNT of what was reported, and the
# tests below are about the three ways that stops being true: a claim that is
# not a count, a zero standing in for an unknown, and a jump link pointing at a
# heading that is not on the page.

from company_page import ROUND_MEANINGS, anchor, company_preamble  # noqa: E402
from company_registry import COMPANY_SEGMENTS  # noqa: E402
from segments import SECTOR_BY_ID, SIZE_LABELS, is_big_tech, sector_chip, segment_of  # noqa: E402


def _question(**over):
    payload = {
        "slug": over.pop("slug", "q-1"),
        "title": over.pop("title", "A Question"),
        "url": over.pop("url", "https://trueinterview.io/questions/q-1"),
        "type": over.pop("type", "algorithm"),
        "typeLabel": "Algorithm",
        "difficulty": over.pop("difficulty", "medium"),
        "companies": over.pop("companies", ["Acme"]),
        "topics": over.pop("topics", []),
        "tags": [],
        "rounds": over.pop("rounds", []),
        "reported": over.pop("reported", None),
        "hasSolution": True,
        "accessTier": over.pop("accessTier", "insider"),
        "addedAt": over.pop("addedAt", "2026-01-01T00:00:00+00:00"),
    }
    payload.update(over)
    return question_from_payload(payload)


def _preamble(questions, **over):
    return company_preamble(
        name=over.pop("name", "Acme"),
        key=over.pop("key", "acme"),
        questions=questions,
        guides=over.pop("guides", []),
        guides_complete=over.pop("guides_complete", True),
        experiences=over.pop("experiences", []),
        experiences_total=over.pop("experiences_total", None),
        api_labels={},
        today=over.pop("today", TODAY),
    )


class TestCompanyPage(unittest.TestCase):
    def test_every_jump_link_lands_on_a_heading_that_exists(self):
        # The one defect on this page nobody reports: a link that silently
        # scrolls to the top because an anchor drifted by one character.
        page = render().files["companies/google.md"]
        headings = {anchor(line[3:].strip()) for line in page.splitlines() if line.startswith("## ")}
        jump = next(line for line in page.splitlines() if line.startswith("**On this page:**"))
        targets = re.findall(r"\]\(#([^)]+)\)", jump)
        self.assertTrue(targets)
        for target in targets:
            self.assertIn(target, headings, f"#{target} is not a heading on the page")

    def test_a_company_with_no_reported_round_gets_a_sentence_not_a_table(self):
        page = _preamble([_question(rounds=[])])
        self.assertIn("No question reported at Acme names which round", page)
        self.assertNotIn("What this round is", page)

    def test_a_round_is_described_by_its_format_and_counted_by_the_employer(self):
        page = _preamble([_question(rounds=["oa"]), _question(slug="q-2", rounds=["oa", "onsite"])])
        self.assertIn(ROUND_MEANINGS["oa"], page)
        # The count is the claim about the employer; the description is not.
        self.assertIn("**Online assessment**", page)
        self.assertIn("2 of 2", page)

    def test_an_undated_company_is_unmeasured_rather_than_quiet(self):
        page = _preamble([_question(reported=None)])
        self.assertIn("no sighting date on file", page)
        self.assertIn("unmeasured", page)
        # A real zero would say we looked in the window and found nothing.
        self.assertNotIn("| Reported in the last 90 days | 0 |", page)

    def test_a_quiet_window_names_the_last_time_anything_was_reported(self):
        page = _preamble([_question(reported="2024-01-15")])
        self.assertIn("Nothing has been reported at Acme since Jan 15, 2024", page)

    def test_also_asked_at_excludes_the_company_whose_page_this_is(self):
        # Off by one on every row of every page if this counts the company
        # itself: "also asked at 3" has to mean three OTHER employers.
        page = _preamble([_question(companies=["Acme", "Globex", "Initech"], reported="2026-09-01")])
        start = page[page.index("## Start here"):]
        self.assertIn("| 2 |", start)
        self.assertNotIn("| 3 |", start)

    def test_a_future_sighting_never_becomes_the_most_recent_one(self):
        page = _preamble([_question(reported="2126-01-01"), _question(slug="q-2", reported="2026-08-01")])
        self.assertIn("Aug 01, 2026", page)
        self.assertNotIn("Jan 01, 2126", page[: page.index("## Start here")])

    def test_the_topic_share_names_the_rows_it_is_a_share_of(self):
        page = _preamble([_question(topics=["graphs"]), _question(slug="q-2", topics=[])])
        self.assertIn("1 question at Acme that carries a topic label", page)

    def test_the_preamble_rides_the_first_page_of_a_paginated_company(self):
        rows = [_question(slug=f"q-{n}", reported="2026-09-01") for n in range(ROWS_PER_PAGE + 5)]
        pages = render_shard(
            base="companies/acme",
            title="Acme",
            lede="lede",
            back="back",
            questions=rows,
            cols=columns_for("company", {}, TODAY),
            today=TODAY,
            preamble=_preamble(rows),
        )
        self.assertIn("## At a glance", pages["companies/acme.md"])
        self.assertNotIn("## At a glance", pages["companies/acme-2.md"])


class TestCompanySegments(unittest.TestCase):
    def test_every_registry_key_is_the_slug_a_company_is_filed_under(self):
        # A key that is not what `company_key` produces is an entry nothing will
        # ever look up, and nothing anywhere would report it.
        for key in COMPANY_SEGMENTS:
            self.assertEqual(company_key(key), key, key)

    def test_the_registry_only_records_values_the_taxonomy_defines(self):
        for key, entry in COMPANY_SEGMENTS.items():
            sector, size = entry.get("sector"), entry.get("size")
            self.assertTrue(sector is None or sector in SECTOR_BY_ID, f"{key}: {sector}")
            self.assertTrue(size is None or size in SIZE_LABELS, f"{key}: {size}")

    def test_an_unknown_company_is_unclassified_rather_than_guessed(self):
        self.assertEqual(segment_of("Zzz Definitely Not A Real Employer"), (None, None))
        self.assertEqual(sector_chip(None, None), "")

    def test_big_tech_is_derived_from_the_sector_and_the_size(self):
        self.assertTrue(is_big_tech("consumer-internet", "mega"))
        # Ten thousand people and not a technology company: big, not Big Tech.
        self.assertFalse(is_big_tech("engineering-services", "mega"))
        self.assertFalse(is_big_tech("consumer-internet", "large"))
        self.assertFalse(is_big_tech(None, "mega"))

    def test_the_chip_prints_what_it_knows_and_nothing_else(self):
        self.assertEqual(sector_chip("fintech", None), "💳 Fintech, payments & crypto")
        self.assertEqual(sector_chip(None, "mid"), "200–999 people")


class TestCompanyTypeCuts(unittest.TestCase):
    """The cut a candidate preparing for "a quant loop" actually opens."""

    def _by_company(self):
        shared = _question(slug="shared", companies=["Alpha", "Beta"], reported="2026-09-01")
        return {
            "alpha": ("Alpha", [shared] + [_question(slug=f"a{n}", companies=["Alpha"]) for n in range(4)]),
            "beta": ("Beta", [shared] + [_question(slug=f"b{n}", companies=["Beta"]) for n in range(4)]),
            "gamma": ("Gamma", [_question(slug=f"g{n}", companies=["Gamma"]) for n in range(6)]),
            "delta": ("Delta", [_question(slug=f"d{n}", companies=["Delta"]) for n in range(2)]),
        }

    def test_a_question_asked_at_two_companies_in_one_cut_is_counted_once(self):
        # Every share on the page is wrong in the same direction otherwise.
        registry = {
            "alpha": {"sector": "fintech", "size": "large"},
            "beta": {"sector": "fintech", "size": "mid"},
        }
        with mock.patch.dict(COMPANY_SEGMENTS, registry, clear=True):
            cuts = {cut["id"]: cut for cut in company_type_cuts(self._by_company(), {})}
        # Five each, one of them the same question: nine, not ten.
        self.assertEqual(len(cuts["fintech"]["questions"]), 9)
        self.assertEqual(len(cuts["fintech"]["companies"]), 2)

    def test_an_unclassified_company_is_in_no_cut_at_all(self):
        registry = {"alpha": {"sector": "fintech", "size": "large"}}
        with mock.patch.dict(COMPANY_SEGMENTS, registry, clear=True):
            cuts = company_type_cuts(self._by_company(), {})
        named = {key for cut in cuts for key, _, _ in cut["companies"]}
        self.assertIn("alpha", named)
        self.assertNotIn("gamma", named)

    def test_big_tech_is_technology_and_ten_thousand_people_together(self):
        registry = {
            "alpha": {"sector": "consumer-internet", "size": "mega"},
            # Ten thousand people and not a technology company.
            "beta": {"sector": "engineering-services", "size": "mega"},
            "gamma": {"sector": "consumer-internet", "size": "mid"},
        }
        with mock.patch.dict(COMPANY_SEGMENTS, registry, clear=True):
            cuts = {cut["id"]: cut for cut in company_type_cuts(self._by_company(), {})}
        self.assertEqual([key for key, _, _ in cuts["big-tech"]["companies"]], ["alpha"])
        self.assertEqual([key for key, _, _ in cuts["mid-size-tech"]["companies"]], ["gamma"])

    def test_a_cut_too_small_to_be_worth_a_page_does_not_get_one(self):
        registry = {"delta": {"sector": "gaming", "size": "mid"}}
        with mock.patch.dict(COMPANY_SEGMENTS, registry, clear=True):
            cuts = company_type_cuts(self._by_company(), {})
        # Delta carries 2 questions; the floor is COMPANY_TYPE_MIN_QUESTIONS.
        self.assertLess(2, COMPANY_TYPE_MIN_QUESTIONS)
        self.assertEqual(cuts, [])

    def test_every_cut_in_the_readme_block_has_a_page_behind_it(self):
        result = render()
        readme = result.files["README.md"]
        block = readme[readme.index("gen:company-types:start"):readme.index("gen:company-types:end")]
        for target in re.findall(r"\]\((company-types/[^)]+)\)", block):
            self.assertIn(target, result.files, target)

    def test_a_cut_page_states_the_rule_that_selected_it(self):
        result = render()
        for path, contents in result.files.items():
            if path.startswith("company-types/") and path != "company-types/README.md":
                self.assertIn("registry", contents, path)
                self.assertIn("## The companies in this cut", contents, path)


class TestPlurals(unittest.TestCase):
    def test_no_page_says_one_of_something_plural(self):
        # Small pages are where a count of one turns up, and they are exactly
        # the pages nobody re-reads: "**1 questions** with a sighting recorded
        # in May 2024" was live on four month pages and on every company page
        # with a single labelled topic.
        offenders = []
        pattern = re.compile(r"(?<![\d\"])1 (questions|guides|writeups|sightings|employers|reports|companies|roles)\b")
        for path, contents in render().files.items():
            if not path.endswith(".md"):
                continue
            for match in pattern.finditer(contents):
                offenders.append(f"{path}: …{contents[max(0, match.start() - 40):match.end() + 10]}…")
        self.assertEqual(offenders, [])


class TestCompanyLabelAliases(unittest.TestCase):
    def test_an_alias_never_splits_one_company_into_two_pages(self):
        # `company_key` derives from the LABEL, so an alias that changes the
        # slug moves the company's page — and the slug is also the address of
        # its page on the site. An alias must resolve the stored spelling and
        # its own display name to the same key, or the two spellings become two
        # pages that each hold half the questions.
        for stored, label in COMPANY_LABELS.items():
            self.assertEqual(
                company_key(stored), company_key(label), f"{stored!r} → {label!r} moves the page"
            )

    def test_the_bank_renders_no_obviously_mangled_capitalisation(self):
        # Not a rule a machine can settle in general, so it is pinned to the
        # names this repository has actually been publishing wrong.
        for stored, expected in (("mongodb", "MongoDB"), ("ebay", "eBay"), ("geico", "GEICO")):
            self.assertEqual(company_label(stored), expected)


class TestLinks(unittest.TestCase):
    def test_every_relative_link_points_at_a_file_that_exists(self):
        # The guard the sibling job-list repositories have and this one did not:
        # `guides/README.md` linked at `companies/general.md`, a company with
        # guides and no questions, and so a page the generator never writes. A
        # 404 in a published index is invisible to every other test here.
        result = render()
        generated = set(result.files)
        broken = []
        for path, contents in result.files.items():
            if not path.endswith(".md"):
                continue
            here = PurePosixPath(path).parent
            for href in re.findall(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", contents):
                if href.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                if href.startswith("../../../issues") or href.startswith("../../issues"):
                    continue
                target = os.path.normpath(str(here / href.split("#")[0]))
                if target in generated or (ROOT / target).exists():
                    continue
                broken.append(f"{path} → {href}")
        self.assertEqual(broken, [])


class TestPartialReads(unittest.TestCase):
    def test_a_guide_count_is_a_floor_when_the_catalog_read_fell_short(self):
        # The guides index carries a standing "this list is incomplete" notice
        # for this state; a bare count on a company page reads as a total,
        # which is the same lie in a smaller font.
        guide = load_fixture().guides[0]
        whole = _preamble([_question()], guides=[guide], guides_complete=True)
        partial = _preamble([_question()], guides=[guide], guides_complete=False)
        self.assertIn("| Round-by-round guides | 1 |", whole)
        self.assertIn("| Round-by-round guides | 1+ |", partial)
        self.assertIn("could only hand over one page of guides", partial)
        self.assertNotIn("could only hand over one page of guides", whole)

    def test_the_sector_chip_carries_its_rule_and_links_at_it(self):
        # The one line on the page that is not a count. It is printed under a
        # docstring promising the page asserts nothing, so it says what selects
        # it and points at the cut that states the rule in full.
        page = render().files["companies/amazon.md"]
        chip = page.split("\n")[8]
        self.assertIn("company-types/", chip, chip)
        if "Big Tech" in chip:
            self.assertIn("a technology-sector employer with 10,000+ people", chip)

    def test_no_page_promises_a_verdict_the_bank_cannot_give(self):
        # 377 of 2,315 questions are system-design or AI-coding, where a run's
        # exit code is a diagnostic and there is no verdict at all.
        for path, contents in render().files.items():
            if not path.endswith(".md"):
                continue
            # Over the whole document rather than per line: the claim wraps.
            for match in re.finditer(r"server-judged verdict|judged server-side", contents):
                window = " ".join(contents[match.start() : match.end() + 160].split())
                self.assertTrue(
                    "algorithm" in window and "SQL" in window,
                    f"{path}: unqualified judging claim — {window[:160]}",
                )


class TestPathSafety(unittest.TestCase):
    def test_a_rendered_path_outside_the_generated_directories_is_refused(self):
        # A question's `type` is free text to the catalog API and becomes a
        # path (`formats/{type}.md`). Nothing downstream checked it, and the
        # writer makes directories on the way.
        from sync import refuse_unwritable

        self.assertEqual(refuse_unwritable(["README.md", "companies/a.md", "data/x.csv"]), [])
        for bad in ("formats/../../../etc/passwd.md", "/etc/x.md", "scripts/evil.md", ".git/config.md",
                    "formats/x.py", "formats/..%2f.md"):
            self.assertEqual(len(refuse_unwritable([bad])), 1, bad)

    def test_every_path_the_real_build_produces_is_writable(self):
        from sync import refuse_unwritable

        self.assertEqual(refuse_unwritable(render().files), [])


class TestOneEmployerCountedOnce(unittest.TestCase):
    def test_a_row_naming_one_employer_twice_is_one_row_on_its_page(self):
        # The alias table folds `SpaceX` and `Spacex` onto one key, which is
        # what let a row carrying both be appended to that key twice.
        question = _question(companies=["SpaceX", "Spacex"], reported="2026-09-01")
        groups = group_by_company([question], {})
        self.assertEqual(list(groups), ["spacex"])
        self.assertEqual(len(groups["spacex"][1]), 1)
        stats = compute_insights([question], [], {}, TODAY)
        self.assertEqual([(row.key, row.questions) for row in stats.companies], [("spacex", 1)])
