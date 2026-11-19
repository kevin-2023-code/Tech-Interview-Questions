#!/usr/bin/env python3
"""Tests for the sync engine. Standard library only; no network.

    python3 -m unittest discover scripts -v

Everything here renders the fixture in ``scripts/fixtures/catalog-sample.json``
with a pinned date, so a failure is a change in the renderers and never a
change on the site.
"""

from __future__ import annotations

import csv
import io
import json
import posixpath
import re
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build import build, check_budgets, group_by_company  # noqa: E402
from catalog import CatalogError, load_catalog, parse_catalog_date, question_from_payload  # noqa: E402
from company import (  # noqa: E402
    CSV_HEADER,
    TABLE_MAX_ROWS,
    WINDOWS,
    companies_reporting,
    company_files,
    in_window,
    render_company_csv,
)
from labels import ACRONYMS, COMPANY_LABELS, company_key, company_label  # noqa: E402
from render import PAGE_MAX_BYTES, README_MAX_BYTES, escape_cell, inject, site_link, sort_questions  # noqa: E402
from season import stats_line  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "scripts" / "fixtures" / "catalog-sample.json"
TODAY = date(2026, 9, 11)

COMPANY_FILES = ("README.md", "all.csv", "six-months.csv", "thirty-days.csv", "three-months.csv")


def load_fixture():
    return load_catalog(json.loads(FIXTURE.read_text(encoding="utf-8")))


def render(catalog=None):
    template = (ROOT / "README.md").read_text(encoding="utf-8")
    return build(catalog or load_fixture(), template, TODAY)


def read_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


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


class TestDates(unittest.TestCase):
    def test_full_date_and_month_only(self):
        self.assertEqual(parse_catalog_date("2026-09-10"), (date(2026, 9, 10), False))
        self.assertEqual(parse_catalog_date("2026-09"), (date(2026, 9, 1), True))

    def test_unparseable_is_undated_not_guessed(self):
        # An out-of-range month must not become a date. The alternative, clamping
        # to December, would publish a sighting nobody reported.
        for value in ("2026-13-45", "not a date", "", None, "2026"):
            self.assertEqual(parse_catalog_date(value), (None, False), value)

    def test_month_only_resolves_to_the_first_not_the_last(self):
        # The conservative end. Resolving "2026-09" to Sep 30 would put a question
        # in the thirty-day file that is only known to have been seen that month.
        resolved, month_only = parse_catalog_date("2026-09")
        self.assertEqual(resolved, date(2026, 9, 1))
        self.assertTrue(month_only)


class TestEscaping(unittest.TestCase):
    def test_table_breaking_characters(self):
        out = escape_cell("Pipe | Bracket [x] & <tag> \\ back")
        for raw in ("|", "[", "]", "<", ">", "\\"):
            self.assertNotIn(raw, out)
        self.assertIn("&", out)  # an ampersand is legal text and stays

    def test_every_rendered_row_has_its_headers_cell_count(self):
        # The failure an unescaped pipe actually causes: one row splits into an
        # extra cell and the table renders ragged from there down.
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

    def test_a_csv_survives_a_title_full_of_delimiters(self):
        question = _question(title='Comma, "quote" | pipe', reported="2026-09-01")
        rows = read_csv(render_company_csv([question], {}))
        self.assertEqual(rows[0]["Title"], 'Comma, "quote" | pipe')
        self.assertEqual(rows[0]["Link"], question.url)


class TestSorting(unittest.TestCase):
    def test_three_buckets_real_then_future_then_undated(self):
        ordered = sort_questions(load_fixture().questions, TODAY)
        buckets = [2 if q.reported_date is None else (1 if q.reported_date > TODAY else 0) for q in ordered]
        self.assertEqual(buckets, sorted(buckets), "buckets must not interleave")
        self.assertEqual(set(buckets), {0, 1, 2}, "the fixture must exercise all three")

    def test_real_sightings_are_newest_first(self):
        ordered = sort_questions(load_fixture().questions, TODAY)
        real = [q.reported_date for q in ordered if q.reported_date and q.reported_date <= TODAY]
        self.assertEqual(real, sorted(real, reverse=True))

    def test_order_is_total_so_reruns_do_not_churn(self):
        # Two rows that compared equal would be free to swap between syncs, and
        # every swap is a committed diff on a file nobody changed.
        from render import _sort_key

        keys = [_sort_key(q, TODAY) for q in load_fixture().questions]
        self.assertEqual(len(set(keys)), len(keys))


class TestLayout(unittest.TestCase):
    """One axis, the company; one predictable set of files per folder."""

    def test_only_the_generated_directories_and_the_readme_are_written(self):
        tops = {path.split("/")[0] for path in render().files}
        self.assertEqual(tops, {"README.md", "companies", "data", "formats", "latest", "questions"})

    def test_every_company_folder_has_exactly_the_same_five_files(self):
        result = render()
        folders: dict[str, set[str]] = {}
        for path in result.files:
            if path.startswith("companies/"):
                _, key, name = path.split("/")
                folders.setdefault(key, set()).add(name)
        self.assertEqual(set(folders), set(group_by_company(load_fixture().questions, load_fixture().labels)))
        for key, names in folders.items():
            self.assertEqual(sorted(names), sorted(COMPANY_FILES), key)

    def test_data_holds_the_whole_bank_and_the_path_registry(self):
        data = sorted(p for p in render().files if p.startswith("data/"))
        self.assertEqual(data, ["data/paths.json", "data/questions.csv", "data/questions.jsonl"])

    def test_without_an_export_questions_is_only_its_index(self):
        self.assertEqual([p for p in render().files if p.startswith("questions/")], ["questions/README.md"])


class TestCompanyCsv(unittest.TestCase):
    def test_the_header_is_the_documented_one_on_every_file(self):
        header = ",".join(CSV_HEADER)
        self.assertEqual(
            header, "Title,Format,Difficulty,Round,Level,Companies Reporting,Reports,Last Reported,Link"
        )
        for path, content in render().files.items():
            if path.startswith("companies/") and path.endswith(".csv"):
                self.assertEqual(content.splitlines()[0], header, path)

    def test_an_empty_window_is_a_header_only_file(self):
        files = company_files(
            key="acme", name="Acme", questions=[_question()], pages={}, api_labels={}, today=TODAY, footer=""
        )
        for stem, _days in WINDOWS:
            self.assertEqual(files[f"companies/acme/{stem}.csv"], ",".join(CSV_HEADER) + "\n")
        self.assertEqual(len(read_csv(files["companies/acme/all.csv"])), 1)

    def test_a_question_lands_in_exactly_the_windows_its_date_falls_in(self):
        def aged(slug, days):
            return _question(slug=slug, url=f"https://trueinterview.io/questions/{slug}",
                             reported=(TODAY - timedelta(days=days)).isoformat())

        questions = [aged("d0", 0), aged("d30", 30), aged("d31", 31), aged("d90", 90), aged("d91", 91),
                     aged("d180", 180), aged("d181", 181), _question(slug="undated", url="https://trueinterview.io/questions/undated")]
        files = company_files(
            key="acme", name="Acme", questions=questions, pages={}, api_labels={}, today=TODAY, footer=""
        )

        def slugs(stem):
            return {row["Link"].rsplit("/", 1)[-1] for row in read_csv(files[f"companies/acme/{stem}.csv"])}

        self.assertEqual(slugs("thirty-days"), {"d0", "d30"})
        self.assertEqual(slugs("three-months"), {"d0", "d30", "d31", "d90"})
        self.assertEqual(slugs("six-months"), {"d0", "d30", "d31", "d90", "d91", "d180"})
        self.assertEqual(len(slugs("all")), len(questions))

    def test_an_undated_or_future_dated_question_is_in_all_csv_only(self):
        self.assertFalse(in_window(_question(), TODAY, 180))
        self.assertFalse(in_window(_question(reported="2027-01-01"), TODAY, 180))
        result = render()
        questions = load_fixture().questions
        outside = {q.url for q in questions if q.reported_date is None or q.reported_date > TODAY}
        self.assertTrue(outside)
        for path, content in result.files.items():
            if path.startswith("companies/") and path.endswith(".csv") and not path.endswith("/all.csv"):
                for row in read_csv(content):
                    self.assertNotIn(row["Link"], outside, path)

    def test_the_windows_are_nested(self):
        result = render()
        for key in group_by_company(load_fixture().questions, load_fixture().labels):
            links = [
                {row["Link"] for row in read_csv(result.files[f"companies/{key}/{stem}.csv"])}
                for stem in ("thirty-days", "three-months", "six-months", "all")
            ]
            for smaller, larger in zip(links, links[1:]):
                self.assertLessEqual(smaller, larger, key)

    def test_all_csv_has_one_row_per_question_reported_there(self):
        result = render()
        catalog = load_fixture()
        for key, (_name, rows) in group_by_company(catalog.questions, catalog.labels).items():
            self.assertEqual(len(read_csv(result.files[f"companies/{key}/all.csv"])), len(rows), key)

    def test_rows_are_most_recently_reported_first(self):
        for path, content in render().files.items():
            if path.endswith("/six-months.csv"):
                dates = [row["Last Reported"].ljust(10, "-") for row in read_csv(content)]
                # A month-only sighting counts as the first of its month.
                dates = [d.replace("---", "-01") for d in dates]
                self.assertEqual(dates, sorted(dates, reverse=True), path)

    def test_the_count_column_is_the_number_of_distinct_companies_and_nothing_else(self):
        # The catalog publishes no per-question report count, so the only
        # frequency signal this repository may print is one it can count.
        self.assertEqual(companies_reporting(_question(companies=["SpaceX", "Spacex", "Meta"]), {}), 2)
        result = render()
        catalog = load_fixture()
        by_url = {q.url: companies_reporting(q, catalog.labels) for q in catalog.questions}
        for path, content in result.files.items():
            if path.endswith("/all.csv"):
                for row in read_csv(content):
                    self.assertEqual(int(row["Companies Reporting"]), by_url[row["Link"]], path)

    def test_a_month_only_sighting_is_printed_as_a_month(self):
        row = read_csv(render_company_csv([_question(reported="2026-08")], {}))[0]
        self.assertEqual(row["Last Reported"], "2026-08")
        row = read_csv(render_company_csv([_question()], {}))[0]
        self.assertEqual(row["Last Reported"], "")

    def test_every_link_is_the_questions_site_url(self):
        urls = {q.url for q in load_fixture().questions}
        for path, content in render().files.items():
            if path.startswith("companies/") and path.endswith(".csv"):
                for row in read_csv(content):
                    self.assertIn(row["Link"], urls, path)


class TestCompanyReadme(unittest.TestCase):
    def test_it_is_one_table_with_a_row_per_question(self):
        result = render()
        catalog = load_fixture()
        for key, (name, rows) in group_by_company(catalog.questions, catalog.labels).items():
            page = result.files[f"companies/{key}/README.md"]
            self.assertIn(f"# {name} interview questions", page)
            table_rows = [line for line in page.splitlines() if line.startswith("| [")]
            self.assertEqual(len(table_rows), len(rows), key)
            for stem, _days in WINDOWS:
                self.assertIn(f"]({stem}.csv)", page)
            self.assertIn("](all.csv)", page)

    def test_the_table_is_capped_and_says_so(self):
        questions = [
            _question(slug=f"q-{n}", url=f"https://trueinterview.io/questions/q-{n}")
            for n in range(TABLE_MAX_ROWS + 3)
        ]
        files = company_files(
            key="acme", name="Acme", questions=questions, pages={}, api_labels={}, today=TODAY, footer=""
        )
        page = files["companies/acme/README.md"]
        self.assertEqual(sum(1 for line in page.splitlines() if line.startswith("| [")), TABLE_MAX_ROWS)
        self.assertIn(f"of {TABLE_MAX_ROWS + 3:,}", page)
        self.assertEqual(len(read_csv(files["companies/acme/all.csv"])), TABLE_MAX_ROWS + 3)

    def test_a_row_links_at_the_page_here_when_one_exists_and_the_site_otherwise(self):
        here = _question(slug="two-sum", url="https://trueinterview.io/questions/two-sum")
        there = _question(slug="other", url="https://trueinterview.io/questions/other")
        files = company_files(
            key="acme",
            name="Acme",
            questions=[here, there],
            pages={"two-sum": "questions/algorithm/two-sum/README.md"},
            api_labels={},
            today=TODAY,
            footer="",
        )
        page = files["companies/acme/README.md"]
        self.assertIn("](../../questions/algorithm/two-sum/README.md)", page)
        self.assertIn(f"]({site_link('https://trueinterview.io/questions/other')})", page)

    def test_no_freshness_marker_or_schedule_claim_is_printed_anywhere(self):
        for path, content in render().files.items():
            if not path.endswith(".md"):
                continue
            for banned in ("🔥", "🆕", "hourly", "every hour"):
                self.assertNotIn(banned, content, path)


class TestReadme(unittest.TestCase):
    def setUp(self):
        self.readme = render().files["README.md"]

    def test_the_stats_line_is_dated_and_counted(self):
        catalog = load_fixture()
        companies = len(group_by_company(catalog.questions, catalog.labels))
        line = stats_line(catalog.questions, companies, TODAY)
        self.assertTrue(line.startswith(f"**{len(catalog.questions):,} questions · {companies} companies · +"), line)
        self.assertTrue(line.endswith("data as of 2026-09-11**"), line)
        self.assertIn(line, self.readme)

    def test_the_company_table_links_every_folder_with_its_count(self):
        catalog = load_fixture()
        groups = group_by_company(catalog.questions, catalog.labels)
        block = self.readme.split("<!-- gen:companies:start -->")[1].split("<!-- gen:companies:end -->")[0]
        rows = re.findall(r"^\| \[[^\]]+\]\(companies/([a-z0-9-]+)/\) \| ([\d,]+) \|", block, re.M)
        self.assertEqual({key for key, _count in rows}, set(groups))
        for key, count in rows:
            self.assertEqual(int(count.replace(",", "")), len(groups[key][1]), key)
        counts = [int(count.replace(",", "")) for _key, count in rows]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_it_has_no_badge_or_banner(self):
        for line in self.readme.splitlines():
            self.assertFalse(line.startswith(("> ", "<div", "![")), line)
        self.assertNotIn("shields.io", self.readme)

    def test_every_documented_column_is_a_real_csv_column(self):
        for column in CSV_HEADER:
            self.assertIn(f"**{column}**", self.readme)


class TestBudgets(unittest.TestCase):
    def test_readme_and_pages_are_inside_budget(self):
        self.assertEqual(check_budgets(render()), [])

    def test_every_page_is_inside_the_page_budget(self):
        for path, content in render().files.items():
            if path.endswith(".md"):
                self.assertLessEqual(len(content.encode("utf-8")), PAGE_MAX_BYTES, path)

    def test_an_oversized_render_is_refused_rather_than_written(self):
        # The budget must FAIL a run, not warn about it.
        result = render()
        result.files["README.md"] = "x" * (README_MAX_BYTES + 1)
        self.assertTrue(check_budgets(result))
        result = render()
        result.files["companies/meta/README.md"] = "x" * (PAGE_MAX_BYTES + 1)
        self.assertTrue(check_budgets(result))


class TestLabels(unittest.TestCase):
    def test_api_labels_win_over_the_transcription(self):
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


class TestCompanyLabelAliases(unittest.TestCase):
    def test_an_alias_never_splits_one_company_into_two_pages(self):
        # `company_key` derives from the LABEL, so an alias that changes the slug
        # moves the company's folder. An alias must resolve the stored spelling
        # and its own display name to the same key.
        for stored, label in COMPANY_LABELS.items():
            self.assertEqual(company_key(stored), company_key(label), f"{stored!r} → {label!r} moves the page")

    def test_an_acronym_never_moves_a_slug(self):
        for token in ACRONYMS:
            for stored in (token, f"{token}-research", f"acme-{token}"):
                self.assertEqual(company_key(stored), stored, f"{stored!r} moved when {token!r} was uppercased")

    def test_the_bank_renders_no_obviously_mangled_capitalisation(self):
        for stored, expected in (("mongodb", "MongoDB"), ("ebay", "eBay"), ("geico", "GEICO")):
            self.assertEqual(company_label(stored), expected)


class TestCatalogValidation(unittest.TestCase):
    def test_a_row_without_a_slug_is_refused(self):
        with self.assertRaises(CatalogError):
            question_from_payload({"title": "t", "url": "https://trueinterview.io/questions/x"})

    def test_a_row_without_an_absolute_url_is_refused(self):
        with self.assertRaises(CatalogError):
            question_from_payload({"slug": "x", "title": "t", "url": "/questions/x"})

    def test_an_unknown_difficulty_is_refused(self):
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

    def test_a_snapshot_round_trips(self):
        from catalog import snapshot

        catalog = load_fixture()
        again = load_catalog(snapshot(catalog, "https://trueinterview.io"))
        self.assertEqual(again.questions, catalog.questions)
        self.assertEqual(again.labels, catalog.labels)
        self.assertIsNone(again.content)


class TestInjection(unittest.TestCase):
    def test_replaces_only_between_the_markers(self):
        doc = "before\n<!-- gen:x:start -->\nold\n<!-- gen:x:end -->\nafter\n"
        out = inject(doc, "gen:x", "new")
        self.assertIn("before", out)
        self.assertIn("after", out)
        self.assertIn("new", out)
        self.assertNotIn("old", out)

    def test_a_missing_marker_is_an_error_not_a_skip(self):
        with self.assertRaises(ValueError):
            inject("no markers here", "gen:x", "new")


class TestDeterminism(unittest.TestCase):
    def test_two_renders_are_byte_identical(self):
        self.assertEqual(render().files, render().files)

    def test_rendering_the_rendered_readme_changes_nothing(self):
        # What the scheduled --check pass relies on: the README is its own template.
        first = render()
        second = build(load_fixture(), first.files["README.md"], TODAY, json.loads(first.files["data/paths.json"]))
        self.assertEqual(first.files, second.files)

    def test_generated_pages_carry_the_do_not_edit_notice(self):
        for path, content in render().files.items():
            if path.endswith(".md") and path != "README.md":
                self.assertTrue(content.startswith("<!--"), path)

    def test_exports_are_slug_sorted_for_line_stable_diffs(self):
        lines = render().files["data/questions.jsonl"].splitlines()
        slugs = [json.loads(line)["slug"] for line in lines]
        self.assertEqual(slugs, sorted(slugs))

    def test_csv_row_count_matches_the_bank(self):
        result = render()
        self.assertEqual(len(read_csv(result.files["data/questions.csv"])), len(load_fixture().questions))

    def test_no_generated_file_has_a_carriage_return(self):
        for path, content in render().files.items():
            self.assertNotIn("\r", content, path)


class TestCoverage(unittest.TestCase):
    def test_every_question_with_a_company_reaches_a_company_folder(self):
        result = render()
        tables = "\n".join(c for p, c in result.files.items() if p.startswith("companies/") and p.endswith(".md"))
        sheets = "\n".join(c for p, c in result.files.items() if p.endswith("/all.csv"))
        for question in load_fixture().questions:
            if question.companies:
                self.assertIn(question.url, tables, question.slug)
                self.assertIn(question.url, sheets, question.slug)

    def test_a_question_at_several_companies_is_in_each_of_their_folders(self):
        result = render()
        catalog = load_fixture()
        multi = next(q for q in catalog.questions if len(q.companies) > 1)
        for company in multi.companies:
            key = company_key(company, catalog.labels)
            self.assertIn(multi.url, result.files[f"companies/{key}/README.md"])
            self.assertIn(multi.url, result.files[f"companies/{key}/all.csv"])

    def test_a_row_naming_one_employer_twice_is_one_row_in_its_folder(self):
        # The alias table folds `SpaceX` and `Spacex` onto one key, which is
        # what would let a row carrying both be appended to that key twice.
        question = _question(companies=["SpaceX", "Spacex"], reported="2026-09-01")
        groups = group_by_company([question], {})
        self.assertEqual(list(groups), ["spacex"])
        self.assertEqual(len(groups["spacex"][1]), 1)


class TestFutureDates(unittest.TestCase):
    def test_a_future_sighting_is_reported_rather_than_ignored_or_fatal(self):
        # Exiting non-zero lets one mistyped date stop every other row from
        # syncing; ignoring it hides the error for ever.
        result = render()
        future = result.stats["future_dated"]
        self.assertTrue(future, "the fixture should contain a future-dated sighting")
        everything = "".join(result.files.values())
        for slug in future:
            self.assertIn(f"/questions/{slug}", everything)

    def test_a_future_sighting_never_leads_a_company_table(self):
        result = render()
        catalog = load_fixture()
        future = {q.url for q in catalog.questions if q.reported_date and q.reported_date > TODAY}
        for key, (_name, rows) in group_by_company(catalog.questions, catalog.labels).items():
            if not any(q.reported_date and q.reported_date <= TODAY for q in rows):
                continue
            first = read_csv(result.files[f"companies/{key}/all.csv"])[0]
            self.assertNotIn(first["Link"], future, key)


class TestNoBodies(unittest.TestCase):
    def test_a_company_folder_carries_titles_metadata_and_links_only(self):
        # The fixture plus an export: the statement must reach the question's
        # own page and nowhere under companies/ or data/.
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        free = next(q for q in data["questions"] if q["accessTier"] == "free" and q["companies"])
        data["content"] = {
            "questions": [{"slug": free["slug"], "type": free["type"], "contentMd": "THE STATEMENT", "hints": ["THE HINT"]}],
            "articles": [{"slug": "anything", "contentMd": "THE GUIDE"}],
        }
        result = build(load_catalog(data), (ROOT / "README.md").read_text(encoding="utf-8"), TODAY)
        holders = [p for p, c in result.files.items() if "THE STATEMENT" in c or "THE HINT" in c]
        self.assertEqual(len(holders), 1)
        self.assertTrue(holders[0].startswith("questions/"))
        self.assertFalse(any("THE GUIDE" in c for c in result.files.values()))
        # And the company row now links at that page.
        registry = json.loads(result.files["data/paths.json"])
        key = company_key(free["companies"][0], load_catalog(data).labels)
        self.assertIn(
            f"](../../{registry['question:' + free['slug']]}/README.md)",
            result.files[f"companies/{key}/README.md"],
        )


class TestPlurals(unittest.TestCase):
    def test_no_page_says_one_of_something_plural(self):
        offenders = []
        pattern = re.compile(r"(?<![\d\",])1 (questions|companies)\b")
        for path, contents in render().files.items():
            if not path.endswith(".md"):
                continue
            for match in pattern.finditer(contents):
                offenders.append(f"{path}: …{contents[max(0, match.start() - 40):match.end() + 10]}…")
        self.assertEqual(offenders, [])


class TestLinks(unittest.TestCase):
    def test_every_relative_link_points_at_a_file_that_exists(self):
        # A 404 in a published index is invisible to every other test here.
        # posixpath, not os.path: a generated path is a URL path on every OS.
        result = render()
        generated = set(result.files)
        folders = {posixpath.dirname(path) for path in generated}
        broken = []
        for path, contents in result.files.items():
            if not path.endswith(".md"):
                continue
            here = posixpath.dirname(path)
            for href in re.findall(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", contents):
                if href.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                if href.startswith(("../../../issues", "../../issues")):
                    continue
                target = posixpath.normpath(posixpath.join(here, href.split("#")[0]))
                if target in generated or target in folders or (ROOT / target).exists():
                    continue
                broken.append(f"{path} → {href}")
        self.assertEqual(broken, [])

    def test_the_readme_anchor_company_pages_link_back_to_exists(self):
        self.assertIn("\n## Companies\n", render().files["README.md"])


class TestPathSafety(unittest.TestCase):
    def test_a_rendered_path_outside_the_generated_directories_is_refused(self):
        # A question's `type` and a company's name are free text to the catalog
        # API and become paths, and the writer makes directories on the way.
        from sync import refuse_unwritable

        self.assertEqual(
            refuse_unwritable(["README.md", "companies/a/README.md", "companies/a/all.csv", "data/x.csv"]), []
        )
        for bad in ("companies/../../../etc/passwd.md", "/etc/x.md", "scripts/evil.md", ".git/config.md",
                    "companies/x.py", "companies/..%2f.md", "topics/x.md", "DESIGN.md"):
            self.assertEqual(len(refuse_unwritable([bad])), 1, bad)

    def test_every_path_the_real_build_produces_is_writable(self):
        from sync import refuse_unwritable

        self.assertEqual(refuse_unwritable(render().files), [])

    def test_a_hostile_format_cannot_leave_the_questions_directory(self):
        from sync import refuse_unwritable

        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        free = next(q for q in data["questions"] if q["accessTier"] == "free" and q["companies"])
        free["type"] = "../../scripts"
        data["content"] = {
            "questions": [{"slug": free["slug"], "type": free["type"], "contentMd": "x", "hints": []}],
            "articles": [],
        }
        result = build(load_catalog(data), (ROOT / "README.md").read_text(encoding="utf-8"), TODAY)
        self.assertTrue(refuse_unwritable(result.files))


if __name__ == "__main__":
    unittest.main()
