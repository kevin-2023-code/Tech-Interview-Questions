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
        # because a question is what you practise once you know that.
        result = render()
        c = load_fixture(); guides, labels = c.guides, c.labels
        guide = next(g for g in guides if g.companies)
        key = company_key(guide.companies[0], labels)
        page = result.files[f"companies/{key}.md"]
        self.assertIn(guide.url, page)
        self.assertLess(page.index(guide.url), page.index("| Question |"))

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
