#!/usr/bin/env python3
"""Assemble every generated file from one catalog snapshot.

Pure: takes records and a date, returns a path → contents map. Nothing here
touches the network or the filesystem, which is what lets the test suite render
the whole repository from a fixture and compare bytes.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

from catalog import Question
from labels import FORMAT_ORDER, company_key, company_label, format_label
from render import (
    GENERATED_NOTICE,
    GITHUB_RENDER_LIMIT,
    MONTH_NAMES,
    PAGE_MAX_BYTES,
    README_LATEST_ROWS,
    README_MAX_BYTES,
    RenderResult,
    columns_for,
    company_nav,
    escape_cell,
    format_nav,
    inject,
    month_nav,
    render_companies_csv,
    render_csv,
    render_jsonl,
    render_shard,
    render_table,
    sort_questions,
    _month_label,
)

SITE = "https://trueinterview.io"


def group_by_company(questions: Sequence[Question], api_labels: dict[str, str]):
    """Company slug → (display name, its questions).

    A question reported at several employers appears under each of them, so the
    per-company counts deliberately sum to MORE than the size of the bank. Every
    page that prints a company count says what it is counting for that reason.
    """
    groups: dict[str, tuple[str, list[Question]]] = {}
    for question in questions:
        for company in question.companies:
            key = company_key(company, api_labels)
            if not key:
                continue
            name, rows = groups.setdefault(key, (company_label(company, api_labels), []))
            rows.append(question)
    return groups


def group_by_format(questions: Sequence[Question]) -> dict[str, list[Question]]:
    groups: dict[str, list[Question]] = {}
    for question in questions:
        groups.setdefault(question.type, []).append(question)
    return groups


def group_by_month(questions: Sequence[Question]) -> dict[str, list[Question]]:
    """``YYYY-MM`` → questions reported that month.

    Undated questions are absent, not bucketed. A month page is a claim that
    these questions were seen in that month, and a row we have no sighting for
    cannot be put behind that claim.
    """
    groups: dict[str, list[Question]] = {}
    for question in questions:
        if question.reported_date is None:
            continue
        groups.setdefault(f"{question.reported_date.year:04d}-{question.reported_date.month:02d}", []).append(question)
    return groups


def build(
    questions: Sequence[Question],
    api_labels: dict[str, str],
    readme_template: str,
    today: date,
) -> RenderResult:
    ordered = sort_questions(questions, today)
    by_company = group_by_company(ordered, api_labels)
    by_format = group_by_format(ordered)
    by_month = group_by_month(ordered)

    company_rows = sorted(
        ((key, name, len(rows)) for key, (name, rows) in by_company.items()),
        key=lambda row: (-row[2], row[1].casefold()),
    )
    month_keys = sorted(by_month, reverse=True)

    files: dict[str, str] = {}

    # ── company pages ────────────────────────────────────────────────────────
    for key, (name, rows) in by_company.items():
        files.update(
            render_shard(
                base=f"companies/{key}",
                title=f"{name} interview & OA questions",
                lede=(
                    f"**{len(rows):,} questions** reported at {escape_cell(name)}. "
                    f"Every title opens the full problem, with a runnable workspace and a "
                    f"server-judged verdict, on [TrueInterview]({SITE}/problems/company/{key})."
                ),
                back="[← All companies](README.md) · [← Question bank](../README.md)",
                questions=rows,
                cols=columns_for("company", api_labels, today),
                today=today,
            )
        )

    files["companies/README.md"] = "\n".join(
        [
            GENERATED_NOTICE,
            "",
            "# Companies",
            "",
            f"**{len(company_rows)} companies**, busiest first. Counts are questions *reported at* that "
            "company, so a question reported at more than one employer is counted under each — the "
            "column therefore sums to more than the size of the bank.",
            "",
            "[← Question bank](../README.md)",
            "",
            "| Company | Questions | On TrueInterview |",
            "| :-- | --: | :-- |",
            *[
                f"| [{escape_cell(name)}](../companies/{key}.md) | {count:,} | "
                f"[{key}]({SITE}/problems/company/{key}) |"
                for key, name, count in company_rows
            ],
            "",
        ]
    )

    # ── format pages ─────────────────────────────────────────────────────────
    for fmt, rows in by_format.items():
        files.update(
            render_shard(
                base=f"formats/{fmt}",
                title=f"{format_label(fmt)} interview & OA questions",
                lede=(
                    f"**{len(rows):,} questions** in the {format_label(fmt)} format. "
                    f"Open one to practise it on [TrueInterview]({SITE}/problems?type={fmt})."
                ),
                back="[← All formats](README.md) · [← Question bank](../README.md)",
                questions=rows,
                cols=columns_for("format", api_labels, today),
                today=today,
            )
        )

    format_keys = [f for f in FORMAT_ORDER if f in by_format] + sorted(set(by_format) - set(FORMAT_ORDER))
    files["formats/README.md"] = "\n".join(
        [
            GENERATED_NOTICE,
            "",
            "# Formats",
            "",
            "Each question is asked in exactly one format, so these counts sum to the whole bank.",
            "",
            "[← Question bank](../README.md)",
            "",
            "| Format | Questions | On TrueInterview |",
            "| :-- | --: | :-- |",
            *[
                f"| [{format_label(fmt)}]({fmt}.md) | {len(by_format[fmt]):,} | "
                f"[{fmt}]({SITE}/problems?type={fmt}) |"
                for fmt in format_keys
            ],
            "",
        ]
    )

    # ── month pages ──────────────────────────────────────────────────────────
    for key in month_keys:
        rows = by_month[key]
        files.update(
            render_shard(
                base=f"by-month/{key}",
                title=f"Reported in {_month_label(key)}",
                lede=f"**{len(rows):,} questions** with a sighting recorded in {_month_label(key)}.",
                back="[← Every month](README.md) · [← Question bank](../README.md)",
                questions=rows,
                cols=columns_for("month", api_labels, today),
                today=today,
            )
        )

    undated = sum(1 for q in ordered if q.reported_date is None)
    files["by-month/README.md"] = "\n".join(
        [
            GENERATED_NOTICE,
            "",
            "# By month reported",
            "",
            f"**{len(ordered) - undated:,} questions** carry a recorded sighting date and are filed "
            f"below by the month they were reported in.",
            "",
            f"The other **{undated:,}** carry no sighting date. They are in the bank and on every "
            "company and format page; they are absent here because a month page is a claim about "
            "when a question was asked, and an undated row cannot be put behind that claim. It is "
            "not the same fact as *old*.",
            "",
            "[← Question bank](../README.md)",
            "",
            "| Month | Questions |",
            "| :-- | --: |",
            *[f"| [{_month_label(key)}]({key}.md) | {len(by_month[key]):,} |" for key in month_keys],
            "",
        ]
    )

    # ── data exports ─────────────────────────────────────────────────────────
    files["data/questions.jsonl"] = render_jsonl(ordered, api_labels)
    files["data/questions.csv"] = render_csv(ordered, api_labels)
    files["data/companies.csv"] = render_companies_csv(company_rows)

    # ── README ───────────────────────────────────────────────────────────────
    # The landing page's newest slice: a real sighting, on or before today.
    # A future-dated row is excluded here specifically — `_sort_key` already
    # denies it the top of the ordering, and this denies it the list whose
    # whole claim is that these questions were asked recently.
    latest = [
        q for q in ordered if q.reported_date is not None and q.reported_date <= today
    ][:README_LATEST_ROWS]
    readme = readme_template
    readme = inject(
        readme,
        "gen:stats",
        f"**{len(ordered):,} questions** · **{len(company_rows)} companies** · "
        f"**{len(format_keys)} formats** · synced from "
        f"[the live catalog]({SITE}/developers/api) every hour",
    )
    readme = inject(readme, "gen:formats", format_nav(by_format))
    readme = inject(readme, "gen:companies", company_nav(company_rows))
    readme = inject(readme, "gen:months", month_nav([(key, len(by_month[key])) for key in month_keys]))
    readme = inject(
        readme,
        "gen:latest",
        render_table(latest, columns_for("readme", api_labels, today)),
    )
    files["README.md"] = readme

    # A sighting dated after today is a data-entry error on the site, not a
    # reason to publish nothing: one mistyped date must not stop the other few
    # thousand rows from syncing. It is already denied a freshness marker, so all
    # that is left is to make it VISIBLE — counted here and printed in the run
    # summary, where an operator sees it. Silently correct output over bad input
    # is how the bad input survives.
    future_dated = [q.slug for q in ordered if q.reported_date and q.reported_date > today]

    stats = {
        "future_dated": future_dated,
        "questions": len(ordered),
        "companies": len(company_rows),
        "formats": len(format_keys),
        "months": len(month_keys),
        "undated": undated,
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

    The budgets are the product. A sync that silently produced a 600 KB README
    would publish a landing page GitHub refuses to finish rendering, and it would
    do it on a schedule, at an hour nobody is watching. So this fails the run
    instead — a stale repository is recoverable and a truncated one looks, to a
    visitor, exactly like a broken one.
    """
    problems: list[str] = []
    readme_bytes = len(result.files["README.md"].encode("utf-8"))
    if readme_bytes > README_MAX_BYTES:
        problems.append(
            f"README.md is {readme_bytes:,} bytes, over the {README_MAX_BYTES:,}-byte index budget "
            f"(GitHub stops rendering at {GITHUB_RENDER_LIMIT:,}). Lower README_LATEST_ROWS or "
            f"README_TOP_COMPANIES."
        )
    for path, content in sorted(result.files.items()):
        if not path.endswith(".md"):
            continue
        size = len(content.encode("utf-8"))
        if size > PAGE_MAX_BYTES:
            problems.append(
                f"{path} is {size:,} bytes, over the {PAGE_MAX_BYTES:,}-byte page budget. "
                f"Lower ROWS_PER_PAGE."
            )
    return problems
