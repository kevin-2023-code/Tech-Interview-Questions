#!/usr/bin/env python3
"""Offline tests for the content layer (``content.py``) and its two fences.

The fixture carries no bodies, so every test here adds a small export to it —
the same shape ``/api/cron/content-export`` answers with — and renders the whole
repository from that. What each test defends is written beside it.

    python3 -m unittest discover scripts -v
"""

from __future__ import annotations

import io
import json
import posixpath
import re
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sync  # noqa: E402
from build import build  # noqa: E402
from catalog import CatalogError, load_catalog  # noqa: E402
from content import assign_paths, rewrite_links, slugify  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "scripts" / "fixtures" / "catalog-sample.json"
TODAY = date(2026, 9, 11)
LONG = "A GUIDE BODY that must never be published here. " * 20


def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def free_and_paid(data: dict):
    free = [q for q in data["questions"] if q["accessTier"] == "free" and q["companies"]]
    paid = [q for q in data["questions"] if q["accessTier"] != "free"]
    return free, paid


def with_content(data: dict | None = None, **overrides) -> tuple[dict, dict]:
    """The fixture plus an export: two free questions, one paid body, one article."""
    data = data or payload()
    free, paid = free_and_paid(data)
    guide = next(g for g in data["guides"] if g["companies"])
    content = {
        "questions": [
            {
                "slug": free[0]["slug"],
                "type": free[0]["type"],
                "contentMd": f"Solve it. See [the other one](/questions/{free[1]['slug']}) and "
                f"[a paid one](/questions/{paid[0]['slug']}) and [the guide](/study/{guide['slug']}).",
                "hints": ["First hint.", "Second hint."],
            },
            {"slug": free[1]["slug"], "type": free[1]["type"], "contentMd": "## Problem\n\nThe second one.", "hints": []},
            # The site should never send this. The repository must not publish it if it does.
            {"slug": paid[0]["slug"], "type": paid[0]["type"], "contentMd": "PAID BODY", "hints": []},
        ],
        "articles": [
            # Guides are read on the site. The repository must not publish one if it is sent.
            {"slug": guide["slug"], "contentMd": LONG + f"\n\n[Practise](/questions/{free[0]['slug']})"},
        ],
    }
    content.update(overrides)
    data["content"] = content
    return data, {"free": free, "paid": paid, "guide": guide}


def render(data: dict, paths: dict | None = None):
    template = (ROOT / "README.md").read_text(encoding="utf-8")
    return build(load_catalog(data), template, TODAY, paths)


def question_pages(result) -> dict[str, str]:
    return {p: c for p, c in result.files.items() if p.startswith("questions/") and p != "questions/README.md"}


class TestFences(unittest.TestCase):
    def test_a_body_the_metadata_does_not_call_free_is_never_published(self):
        data, rows = with_content()
        catalog = load_catalog(data)
        self.assertNotIn(rows["paid"][0]["slug"], catalog.content.questions)
        # The paid body and the article.
        self.assertEqual(catalog.content.dropped, 2)
        result = render(data)
        self.assertFalse(any("PAID BODY" in text for text in result.files.values()))
        self.assertEqual(len(question_pages(result)), 2)

    def test_a_body_filed_under_a_different_format_is_dropped(self):
        data, rows = with_content()
        data["content"]["questions"][1]["type"] = "not-its-format"
        self.assertEqual(load_catalog(data).content.dropped, 3)

    def test_a_malformed_export_stops_the_sync(self):
        data, _ = with_content()
        data["content"]["questions"].append({"slug": "x", "type": "algorithm", "contentMd": ""})
        with self.assertRaises(CatalogError):
            load_catalog(data)
        data["content"] = {"questions": "nope", "articles": []}
        with self.assertRaises(CatalogError):
            load_catalog(data)

    def test_no_export_is_not_an_empty_export(self):
        data = payload()
        self.assertIsNone(load_catalog(data).content)
        result = render(data)
        self.assertEqual(question_pages(result), {})
        self.assertFalse(result.stats["content_configured"])
        # Every company still gets its folder, each row linking at the site.
        homes = [p for p in result.files if p.endswith("/README.md") and p.startswith("companies/")]
        self.assertTrue(homes)
        for home in homes:
            self.assertNotIn("](../../questions/", result.files[home])


class TestQuestionPage(unittest.TestCase):
    def setUp(self):
        self.data, self.rows = with_content()
        self.result = render(self.data)

    def page(self, row) -> str:
        registry = json.loads(self.result.files["data/paths.json"])
        return self.result.files[registry[f"question:{row['slug']}"] + "/README.md"]

    def test_statement_and_hints_are_published_and_the_solution_is_not(self):
        text = self.page(self.rows["free"][0])
        self.assertIn("Solve it.", text)
        self.assertIn("<summary>Hint 2</summary>", text)
        self.assertIn("It is not reproduced here", text)
        self.assertIn(f"]({self.rows['free'][0]['url']})", text)

    def test_links_land_on_a_page_here_or_on_the_site(self):
        text = self.page(self.rows["free"][0])
        registry = json.loads(self.result.files["data/paths.json"])
        here = registry[f"question:{self.rows['free'][0]['slug']}"]
        other = registry[f"question:{self.rows['free'][1]['slug']}"]
        self.assertIn(f"]({posixpath.relpath(other + '/README.md', here)})", text)
        self.assertIn(f"](https://trueinterview.io/questions/{self.rows['paid'][0]['slug']})", text)
        self.assertNotRegex(text, r"\]\(/")

    def test_every_relative_link_resolves(self):
        generated = set(self.result.files)
        broken = []
        for path, contents in self.result.files.items():
            if not path.endswith(".md"):
                continue
            here = posixpath.dirname(path)
            for href in re.findall(r"\]\(([^)\s]+)\)", contents):
                if href.startswith(("http://", "https://", "#", "../../issues", "../../../issues")):
                    continue
                target = posixpath.normpath(posixpath.join(here, href.split("#")[0]))
                if target not in generated and not (ROOT / target).exists():
                    broken.append(f"{path} → {href}")
        self.assertEqual(broken, [])

    def test_no_guide_or_article_body_is_ever_published(self):
        self.assertFalse(any("A GUIDE BODY" in text for text in self.result.files.values()))
        self.assertFalse(any("/guides/" in path or "interview-process" in path for path in self.result.files))
        # A link at the guide from a statement lands on the site, not on a page here.
        text = self.page(self.rows["free"][0])
        self.assertIn(f"](https://trueinterview.io/study/{self.rows['guide']['slug']})", text)

    def test_the_company_rows_link_at_the_published_pages(self):
        registry = json.loads(self.result.files["data/paths.json"])
        row = self.rows["free"][0]
        target = registry[f"question:{row['slug']}"] + "/README.md"
        homes = [
            text
            for path, text in self.result.files.items()
            if path.startswith("companies/") and path.endswith("/README.md") and f"](../../{target})" in text
        ]
        self.assertTrue(homes)
        # The CSV keeps the site URL: a relative path means nothing outside GitHub.
        self.assertTrue(
            any(row["url"] in text for path, text in self.result.files.items() if path.endswith("/all.csv"))
        )


class TestPaths(unittest.TestCase):
    def test_a_published_path_survives_a_title_change(self):
        data, rows = with_content()
        first = render(data)
        registry = json.loads(first.files["data/paths.json"])
        slug = rows["free"][0]["slug"]
        for q in data["questions"]:
            if q["slug"] == slug:
                q["title"] = "A completely different title"
        second = render(data, registry)
        self.assertEqual(json.loads(second.files["data/paths.json"])[f"question:{slug}"], registry[f"question:{slug}"])

    def test_rendering_from_its_own_registry_is_a_no_op(self):
        data, _ = with_content()
        first = render(data)
        second = render(data, json.loads(first.files["data/paths.json"]))
        self.assertEqual(first.files, second.files)

    def test_collisions_and_retired_paths(self):
        registry = assign_paths({"question:old": "questions/algorithm/two-sum"}, [
            ("question:a", "questions/algorithm", "two-sum"),
            ("question:b", "questions/algorithm", "two-sum"),
        ])
        # A retired question's path is never handed to a different one.
        self.assertEqual(registry["question:a"], "questions/algorithm/two-sum-2")
        self.assertEqual(registry["question:b"], "questions/algorithm/two-sum-3")

    def test_a_registry_entry_this_run_did_not_render_is_kept(self):
        # Path stability across a run with no export, and for entries from an
        # older layout: nothing the registry has ever held is dropped or reused.
        old = {"question:gone": "questions/algorithm/gone", "guide:legacy": "companies/meta/guides/legacy"}
        result = build(load_catalog(payload()), (ROOT / "README.md").read_text(encoding="utf-8"), TODAY, old)
        self.assertEqual(json.loads(result.files["data/paths.json"]), old)
        data, _ = with_content()
        kept = json.loads(render(data, old).files["data/paths.json"])
        self.assertEqual({k: kept[k] for k in old}, old)

    def test_a_question_that_changes_format_moves(self):
        registry = assign_paths({"question:a": "questions/algorithm/x"}, [("question:a", "questions/system-design", "x")])
        self.assertEqual(registry["question:a"], "questions/system-design/x")

    def test_slugify(self):
        self.assertEqual(slugify("Design A Top-K System (v2) & More"), "design-a-top-k-system-v2-and-more")
        self.assertEqual(slugify("???"), "untitled")


class TestRewrite(unittest.TestCase):
    def test_reference_links_attributes_and_fragments(self):
        local = {"/questions/a": "questions/algorithm/a/README.md"}
        text = rewrite_links(
            "[x](/questions/a#examples) [y](/pricing)\n[ref]: /questions/a\n<a href=\"/questions/a\">z</a>",
            "companies/meta/README.md",
            local,
        )
        self.assertIn("[x](../../questions/algorithm/a/README.md#examples)", text)
        self.assertIn("[y](https://trueinterview.io/pricing)", text)
        self.assertIn("[ref]: ../../questions/algorithm/a/README.md", text)
        self.assertIn('href="../../questions/algorithm/a/README.md"', text)


class TestCommonOnly(unittest.TestCase):
    def test_only_allowlisted_questions_keep_a_body_and_no_article_does(self):
        from catalog import common_only

        payload = {
            "generatedAt": "x",
            "questions": [{"slug": "lru-cache", "contentMd": "a"}, {"slug": "our-own", "contentMd": "b"}],
            "articles": [{"slug": "meta-interview-process", "contentMd": "c"}],
        }
        cut = common_only(payload, frozenset({"lru-cache"}))
        self.assertEqual([q["slug"] for q in cut["questions"]], ["lru-cache"])
        self.assertEqual(cut["articles"], [])

    def test_a_missing_allowlist_publishes_nothing(self):
        from pathlib import Path

        from catalog import common_question_slugs

        self.assertEqual(common_question_slugs(Path("does-not-exist.txt")), frozenset())


class TestSyncGuard(unittest.TestCase):
    def run_sync(self, *published: str) -> int:
        argv = ["sync.py", "--catalog-file", str(FIXTURE), "--today", TODAY.isoformat(), "--check"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
            sync, "generated_paths", return_value=set(published)
        ), mock.patch.object(sync, "PATHS_FILE", ROOT / "does-not-exist.json"), redirect_stdout(
            io.StringIO()
        ), redirect_stderr(io.StringIO()) as err:
            code = sync.main()
        self.stderr = err.getvalue()
        return code

    def test_an_unconfigured_run_never_deletes_published_question_pages(self):
        self.assertEqual(self.run_sync("questions/algorithm/x/README.md"), 2)
        self.assertIn("CONTENT_EXPORT_SECRET", self.stderr)

    def test_an_unconfigured_run_over_no_pages_is_fine(self):
        # The index is always rendered, so it must not count as a published page.
        self.assertNotEqual(self.run_sync(), 2)
        self.assertNotEqual(self.run_sync("questions/README.md"), 2)


if __name__ == "__main__":
    unittest.main()
