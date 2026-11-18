#!/usr/bin/env python3
"""One folder per company: a table to read and four CSV files to sort.

``companies/<key>/`` holds:

  * ``README.md``: the company's questions as one markdown table, newest first.
  * ``thirty-days.csv``, ``three-months.csv``, ``six-months.csv``: the questions
    whose last reported date falls inside that window.
  * ``all.csv``: every question reported at the company, dated or not.

The windows are nested (the thirty-day file is a subset of the three-month
one) and are measured back from the snapshot date. A question with no reported
date is in ``all.csv`` only, and so is one dated after the snapshot: that is a
mistyped date upstream, not a recent sighting. An empty window still gets a
header-only file, so every company folder has the same five files.

Titles, metadata and links only. No statement, hint or solution is rendered
here; a row links to the question's page in this repository when one is
published (see ``content.py``) and to the site otherwise.

Pure: records in, a path → text map out.
"""

from __future__ import annotations

import csv
import io
import posixpath
from datetime import date
from typing import Sequence

from catalog import Question
from labels import company_key, difficulty_label, format_label, round_label
from render import GENERATED_NOTICE, escape_cell, plural, reported_label, sort_questions, table

#: File stem → how many days back from the snapshot date it reaches.
WINDOWS: tuple[tuple[str, int], ...] = (
    ("thirty-days", 30),
    ("three-months", 90),
    ("six-months", 180),
)

WINDOW_LABELS = {"thirty-days": "last 30 days", "three-months": "last 3 months", "six-months": "last 6 months"}

#: The columns of every per-company CSV. ``Companies Reporting`` is the number
#: of distinct companies the question was reported at. The catalog publishes no
#: per-question report count, so this is the only frequency signal it supports
#: and the column is named for exactly what it counts.
CSV_HEADER = ("Title", "Format", "Difficulty", "Round", "Companies Reporting", "Last Reported", "Link")

#: Rows shown in a company's README table. Past this the page says so and
#: points at ``all.csv``, which has no cap. It keeps the page far under the
#: byte budget without paginating, so a company's page never changes address.
TABLE_MAX_ROWS = 500


def companies_reporting(question: Question, api_labels: dict[str, str]) -> int:
    """Distinct companies a question was reported at (two spellings of one are one)."""
    return len({key for key in (company_key(c, api_labels) for c in question.companies) if key})


def in_window(question: Question, today: date, days: int) -> bool:
    """Was this question last reported within ``days`` of the snapshot date?

    Undated is never in a window, and neither is a date after today. A
    month-only sighting counts from the first of its month, which can only
    under-claim recency.
    """
    reported = question.reported_date
    return reported is not None and 0 <= (today - reported).days <= days


def _rounds(question: Question, separator: str) -> str:
    return separator.join(round_label(r) for r in question.rounds)


def render_company_csv(questions: Sequence[Question], api_labels: dict[str, str]) -> str:
    """One CSV, in the order given. Always has its header row, even when empty."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_HEADER)
    for q in questions:
        writer.writerow(
            [
                q.title,
                format_label(q.type),
                difficulty_label(q.difficulty) or "",
                _rounds(q, "; "),
                companies_reporting(q, api_labels),
                reported_label(q),
                q.url,
            ]
        )
    return buffer.getvalue()


def render_company_readme(
    *,
    key: str,
    name: str,
    questions: Sequence[Question],
    window_counts: dict[str, int],
    pages: dict[str, str],
    api_labels: dict[str, str],
    footer: str,
) -> str:
    """``companies/<key>/README.md``. ``questions`` is already newest first.

    ``pages`` maps a question slug to its page in this repository, for the
    questions that have one.
    """
    here = f"companies/{key}"
    shown = questions[:TABLE_MAX_ROWS]
    rows = [
        [
            f"[{escape_cell(q.title)}]({posixpath.relpath(pages[q.slug], here) if q.slug in pages else q.url})",
            escape_cell(format_label(q.type)),
            difficulty_label(q.difficulty) or "—",
            escape_cell(_rounds(q, ", ")) or "—",
            str(companies_reporting(q, api_labels)),
            reported_label(q) or "—",
        ]
        for q in shown
    ]
    files = " · ".join(
        [f"[{WINDOW_LABELS[stem]}]({stem}.csv) ({window_counts[stem]:,})" for stem, _ in WINDOWS]
        + [f"[all](all.csv) ({len(questions):,})"]
    )
    lines = [
        GENERATED_NOTICE,
        "",
        f"# {name} interview questions",
        "",
        f"{plural(len(questions), 'question')} reported at {escape_cell(name)}, most recently reported first.",
        "",
        f"CSV: {files}",
        "",
        "[All companies](../../README.md#companies)",
        "",
    ]
    if len(questions) > len(shown):
        lines += [
            f"Showing the {len(shown):,} most recently reported of {len(questions):,}. "
            "[all.csv](all.csv) has every row.",
            "",
        ]
    lines += [
        table(
            ["Question", "Format", "Difficulty", "Round", "Companies", "Last reported"],
            [":--", ":--", ":-:", ":--", "--:", ":--"],
            rows,
        ),
        "",
        footer,
    ]
    return "\n".join(lines)


def company_files(
    *,
    key: str,
    name: str,
    questions: Sequence[Question],
    pages: dict[str, str],
    api_labels: dict[str, str],
    today: date,
    footer: str,
) -> dict[str, str]:
    """The five files of one company folder."""
    ordered = sort_questions(questions, today)
    base = f"companies/{key}"
    files: dict[str, str] = {}
    counts: dict[str, int] = {}
    for stem, days in WINDOWS:
        rows = [q for q in ordered if in_window(q, today, days)]
        counts[stem] = len(rows)
        files[f"{base}/{stem}.csv"] = render_company_csv(rows, api_labels)
    files[f"{base}/all.csv"] = render_company_csv(ordered, api_labels)
    files[f"{base}/README.md"] = render_company_readme(
        key=key,
        name=name,
        questions=ordered,
        window_counts=counts,
        pages=pages,
        api_labels=api_labels,
        footer=footer,
    )
    return files
