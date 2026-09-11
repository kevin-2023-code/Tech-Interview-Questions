#!/usr/bin/env python3
"""Offline tests for the sync pipeline.

No network and no clock: every test renders the committed fixture at a pinned
date, which is the only way an assertion about a 🔥 marker can still hold next
month. What each test is defending is stated where it is not obvious — a test
whose reason is not written down is a test the next person deletes.

    python3 -m unittest discover scripts -v
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build import build, check_budgets, group_by_month  # noqa: E402
from catalog import CatalogError, load_catalog, parse_catalog_date, question_from_payload  # noqa: E402
from labels import company_key, company_label  # noqa: E402
from render import (  # noqa: E402
    PAGE_MAX_BYTES,
    README_MAX_BYTES,
    ROWS_PER_PAGE,
    escape_cell,
    freshness_marker,
    inject,
    sort_questions,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "scripts" / "fixtures" / "catalog-sample.json"
TODAY = date(2026, 9, 11)


def load_fixture():
    return load_catalog(json.loads(FIXTURE.read_text(encoding="utf-8")))


def render():
    questions, labels = load_fixture()
    template = (ROOT / "README.md").read_text(encoding="utf-8")
    return build(questions, labels, template, TODAY)


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
    def test_newest_first_and_undated_last(self):
        questions, _ = load_fixture()
        ordered = sort_questions(questions)
        dated = [q.reported_date for q in ordered if q.reported_date]
        self.assertEqual(dated, sorted(dated, reverse=True))
        first_undated = next(i for i, q in enumerate(ordered) if q.reported_date is None)
        self.assertTrue(all(q.reported_date is None for q in ordered[first_undated:]))

    def test_order_is_total_so_reruns_do_not_churn(self):
        # Two rows that compared equal would be free to swap between syncs, and
        # every swap is a committed diff on a file nobody changed.
        questions, _ = load_fixture()
        from render import _sort_key

        keys = [_sort_key(q) for q in questions]
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

        questions, labels = load_fixture()
        pages = render_shard(
            base="formats/algorithm",
            title="t",
            lede="l",
            back="b",
            questions=questions,
            cols=columns_for("format", labels, TODAY),
        )
        expected = -(-len(questions) // ROWS_PER_PAGE)
        self.assertEqual(len(pages), expected)
        self.assertIn("formats/algorithm.md", pages)

    def test_a_shard_that_fits_is_exactly_one_file(self):
        # A company crossing the row cap must GAIN a file, never rename the one
        # it had — a renamed page breaks every link anyone ever shared.
        from render import columns_for, render_shard

        questions, labels = load_fixture()
        pages = render_shard(
            base="companies/meta",
            title="t",
            lede="l",
            back="b",
            questions=questions[:5],
            cols=columns_for("company", labels, TODAY),
        )
        self.assertEqual(list(pages), ["companies/meta.md"])


class TestMonthPages(unittest.TestCase):
    def test_undated_questions_are_absent_from_by_month(self):
        questions, _ = load_fixture()
        undated = [q for q in questions if q.reported_date is None]
        self.assertTrue(undated, "the fixture must contain undated rows")
        filed = {q.slug for rows in group_by_month(questions).values() for q in rows}
        for question in undated:
            self.assertNotIn(question.slug, filed)

    def test_the_month_index_states_how_many_it_excluded(self):
        # A quiet omission is the failure mode here: the count has to be on the
        # page, or the monthly view silently under-reports the bank.
        result = render()
        questions, _ = load_fixture()
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
        questions, _ = load_fixture()
        self.assertEqual(len(result.files["data/questions.csv"].splitlines()) - 1, len(questions))


class TestCoverage(unittest.TestCase):
    def test_every_question_reaches_a_company_page_and_a_format_page(self):
        result = render()
        questions, _ = load_fixture()
        markdown = "\n".join(
            content for path, content in result.files.items()
            if path.startswith(("companies/", "formats/"))
        )
        for question in questions:
            self.assertIn(question.url, markdown, question.slug)

    def test_a_question_at_several_companies_is_on_each_of_their_pages(self):
        result = render()
        questions, labels = load_fixture()
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
        questions, _ = load_fixture()
        future = {q.slug for q in questions if q.reported_date and q.reported_date > TODAY}
        for path, content in result.files.items():
            if not path.endswith(".md"):
                continue
            for line in content.splitlines():
                if any(f"/questions/{slug})" in line for slug in future):
                    self.assertNotIn("🔥", line, path)
                    self.assertNotIn("🆕", line, path)
