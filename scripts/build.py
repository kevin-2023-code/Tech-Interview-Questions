#!/usr/bin/env python3
"""Assemble every generated file from one catalog snapshot.

Pure: takes records and a date, returns a path → contents map. Nothing here
touches the network or the filesystem, which is what lets the test suite render
the whole repository from a fixture and compare bytes.

The layout has one axis, the company:

    README.md                      landing page: stats line and the season index
    latest/README.md, page-N.md    everything reported in the past year, newest first
    formats/<format>.md            the same table for one format
    companies/<key>/README.md      the company's questions as one table
    companies/<key>/*.csv          the same rows by recency (see company.py)
    questions/...                  statements of the allowlisted questions (content.py)
    data/questions.{csv,jsonl}     the whole bank, one row per question
    data/paths.json                the folder each published question was given
"""

from __future__ import annotations

import json
from datetime import date
from typing import Sequence

from catalog import Catalog, Question
from company import WINDOWS, company_files, in_window
from content import footer, render_content
from labels import company_key, company_label
from render import (
    GITHUB_RENDER_LIMIT,
    PAGE_MAX_BYTES,
    README_MAX_BYTES,
    RenderResult,
    inject,
    render_csv,
    render_jsonl,
    sort_questions,
)
from season import (
    ai_coding_block,
    companies_block,
    format_files,
    formats_block,
    has_report_counts,
    latest_files,
    new_block,
    new_this_week,
    recent_block,
    recently_reported,
    stats_line,
    title_line,
)


def group_by_company(questions: Sequence[Question], api_labels: dict[str, str]):
    """Company slug → (display name, its questions).

    A question reported at several employers appears under each of them, so the
    per-company counts sum to MORE than the size of the bank.
    """
    groups: dict[str, tuple[str, list[Question]]] = {}
    for question in questions:
        # Deduped by KEY within the question. The alias tables fold `SpaceX`
        # and `Spacex` onto one key, which is exactly what would let a row
        # carrying both spellings be appended to that key twice.
        for key in _keys_of(question.companies, api_labels):
            _name, rows = groups.setdefault(key, (_name_for(key, question.companies, api_labels), []))
            rows.append(question)
    return groups


def _keys_of(companies: Sequence[str], api_labels: dict[str, str]) -> list[str]:
    """The distinct company keys a row names, in the order it named them."""
    return list(dict.fromkeys(filter(None, (company_key(c, api_labels) for c in companies))))


def _name_for(key: str, companies: Sequence[str], api_labels: dict[str, str]) -> str:
    for company in companies:
        if company_key(company, api_labels) == key:
            return company_label(company, api_labels)
    return key


def build(
    catalog: Catalog,
    readme_template: str,
    today: date,
    paths: dict[str, str] | None = None,
) -> RenderResult:
    """Every generated file. ``paths`` is the committed ``data/paths.json``: the
    folder each published question was given, which must not move."""
    questions, api_labels = catalog.questions, catalog.labels
    ordered = sort_questions(questions, today)
    by_company = group_by_company(ordered, api_labels)

    files: dict[str, str] = {}

    # ── question pages ───────────────────────────────────────────────────────
    # First, because a company row links at a question's page here when it has
    # one. The path registry is an output as well as an input: a question
    # published for the first time is given its folder here, and keeps it.
    content_files, registry, pages, content_stats = render_content(
        questions=ordered,
        homes=frozenset(by_company),
        content=catalog.content,
        registry=dict(paths or {}),
        api_labels=api_labels,
    )
    files.update(content_files)
    files["data/paths.json"] = json.dumps(registry, indent=1, sort_keys=True, ensure_ascii=False) + "\n"

    # One switch for every table: the Reports column is printed only when this
    # snapshot carries counts, so an API that does not send them yet renders a
    # narrower table rather than a column of dashes.
    show_reports = has_report_counts(ordered)

    # ── company folders ──────────────────────────────────────────────────────
    for key, (name, rows) in by_company.items():
        files.update(
            company_files(
                key=key,
                name=name,
                questions=rows,
                pages=pages,
                api_labels=api_labels,
                today=today,
                show_reports=show_reports,
                footer=footer(f"companies/{key}/README.md"),
            )
        )

    # ── the whole bank ───────────────────────────────────────────────────────
    files["data/questions.jsonl"] = render_jsonl(ordered, api_labels)
    files["data/questions.csv"] = render_csv(ordered, api_labels)

    # ── the season index ─────────────────────────────────────────────────────
    table_args = {
        "api_labels": api_labels,
        "homes": frozenset(by_company),
        "show_reports": show_reports,
    }
    files.update(latest_files(ordered, today, **table_args))
    files.update(format_files(ordered, today, **table_args))

    # ── README ───────────────────────────────────────────────────────────────
    readme = readme_template
    for marker, block in (
        ("gen:title", title_line(today)),
        ("gen:snapshot", stats_line(ordered, len(by_company), today)),
        ("gen:new", new_block(ordered, today, root="", **table_args)),
        ("gen:recent", recent_block(ordered, today, root="", **table_args)),
        ("gen:ai-coding", ai_coding_block(ordered, today, root="", **table_args)),
        ("gen:formats", formats_block(ordered)),
        ("gen:companies", companies_block(by_company, today)),
    ):
        readme = inject(readme, marker, block)
    files["README.md"] = readme

    # A sighting dated after today is a data-entry error on the site, not a
    # reason to publish nothing. It is already kept out of every recency file,
    # so all that is left is to make it VISIBLE: counted here and printed in
    # the run summary, where an operator sees it.
    future_dated = [q.slug for q in ordered if q.reported_date and q.reported_date > today]

    stats = {
        **{f"content_{k}": v for k, v in content_stats.items()},
        "content_configured": catalog.content is not None,
        "future_dated": future_dated,
        "questions": len(ordered),
        "companies": len(by_company),
        "undated": sum(1 for q in ordered if q.reported_date is None),
        "new_this_week": len(new_this_week(ordered, today)),
        "reported_past_year": len(recently_reported(ordered, today)),
        "report_counts": has_report_counts(ordered),
        "windows": {stem: sum(1 for q in ordered if in_window(q, today, days)) for stem, days in WINDOWS},
        "files": len(files),
        "readme_bytes": len(files["README.md"].encode("utf-8")),
        "largest_page": max(
            ((len(content.encode("utf-8")), path) for path, content in files.items() if path.endswith(".md")),
            default=(0, ""),
        ),
    }
    return RenderResult(files=files, stats=stats)


def check_budgets(result: RenderResult) -> list[str]:
    """Every byte budget this repository promises, checked before anything is written.

    A sync that silently produced an oversized page would publish something
    GitHub refuses to finish rendering, on a schedule, at an hour nobody is
    watching. So this fails the run instead: a stale repository is recoverable
    and a truncated one looks, to a visitor, exactly like a broken one.
    """
    problems: list[str] = []
    readme_bytes = len(result.files["README.md"].encode("utf-8"))
    if readme_bytes > README_MAX_BYTES:
        problems.append(
            f"README.md is {readme_bytes:,} bytes, over the {README_MAX_BYTES:,}-byte landing-page budget "
            f"(GitHub stops rendering at {GITHUB_RENDER_LIMIT:,}). Lower the row caps in season.py, or raise "
            f"README_MAX_BYTES deliberately."
        )
    for path, content in sorted(result.files.items()):
        if not path.endswith(".md") or path == "README.md":
            continue
        size = len(content.encode("utf-8"))
        if size > PAGE_MAX_BYTES:
            problems.append(
                f"{path} is {size:,} bytes, over the {PAGE_MAX_BYTES:,}-byte page budget. "
                f"Lower company.TABLE_MAX_ROWS, season.LATEST_PAGE_ROWS or season.FORMAT_PAGE_ROWS."
            )
    return problems
