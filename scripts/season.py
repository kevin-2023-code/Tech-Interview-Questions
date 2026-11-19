#!/usr/bin/env python3
"""The season index: what was reported lately, across every company.

The company folders answer "what does this company ask". This module answers
the other question a candidate arrives with: "what is being asked right now".
It renders:

  * the README's generated blocks: the title, the stats line, *New this week*,
    *Recently reported*, *AI coding rounds*, the format links and the company
    table with its counts;
  * ``latest/README.md`` (+ ``latest/page-N.md``): every question reported in
    the past year, newest first;
  * ``formats/<format>.md``: the same table for one format.

Rules it holds, on top of the ones in ``render.py``:

  * **Titles, metadata and links only.** A row is a date, companies, a title,
    a format, rounds, levels and a count. Every title links to the question's
    page on the site, never to a statement here: this index covers the whole
    bank, and most of the bank is not published in this repository.
  * **Two different dates are never printed as one.** *Reported* is when a
    candidate said they were asked it. *Added* is when the row entered the
    bank. *New this week* is about the second and says so in its column name.
  * **A window with nothing in it says so.** No row is promoted to fill it.
  * **A count the catalog did not send is not printed as zero.** The Reports
    column appears only when the snapshot carries counts at all.

Pure: records and a date in, text out.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

from catalog import Question
from company import in_window, reports_cell
from labels import FORMAT_ORDER, company_key, company_label, format_label, level_label, round_short_label
from render import GENERATED_NOTICE, escape_cell, plural, reported_label, site_link, table

#: Rows in the README's *Recently reported* table. The full list is ``latest/``.
README_RECENT_ROWS = 50
#: Rows in the README's *New this week* table.
README_NEW_ROWS = 15
#: Rows in the README's *AI coding rounds* table.
README_AI_CODING_ROWS = 10
#: How far back *New this week* and the stats line's "added" count reach.
NEW_DAYS = 7
ADDED_DAYS = 30
#: The stats line's "reported" window. Ninety days rather than thirty because
#: most sightings carry a month and no day, and a month counts from its first:
#: a thirty-day window would leave out most of last month's reports.
REPORTED_DAYS = 90
#: ``latest/`` lists questions reported within this many days, this many a page.
LATEST_DAYS = 365
LATEST_PAGE_ROWS = 400
#: Rows on one ``formats/<format>.md`` page; ``data/questions.csv`` has the rest.
FORMAT_PAGE_ROWS = 500
#: Companies named in one row's Company cell before it says "+N".
ROW_COMPANIES = 2

AI_CODING = "ai-coding"


def season_name(today: date) -> str:
    """The recruiting seasons a snapshot serves: this year's and next year's.

    A question reported in autumn 2026 is prep for a 2026 start and a 2027 one,
    so the title names both and rolls over with the calendar year.
    """
    return f"{today.year} & {today.year + 1}"


def title_line(today: date) -> str:
    return f"# {season_name(today)} Tech Interview & OA Questions"


def added_within(question: Question, today: date, days: int) -> bool:
    """Did this row enter the bank within ``days`` of the snapshot date?"""
    added = question.added_date
    return added is not None and 0 <= (today - added).days <= days


def stats_line(questions: Sequence[Question], companies: int, today: date) -> str:
    """The dated line the README opens with. Every number is counted from the
    snapshot the tables below it were rendered from, on the date it names."""
    added = sum(1 for q in questions if added_within(q, today, ADDED_DAYS))
    reported = sum(1 for q in questions if in_window(q, today, REPORTED_DAYS))
    return (
        f"**{len(questions):,} questions · {companies:,} companies · "
        f"+{added:,} added in the last {ADDED_DAYS} days · "
        f"{reported:,} reported in the last {REPORTED_DAYS} days · "
        f"data as of {today.isoformat()}**"
    )


def has_report_counts(questions: Sequence[Question]) -> bool:
    return any(q.report_count is not None for q in questions)


def _company_cell(question: Question, api_labels: dict[str, str], homes: frozenset[str], root: str) -> str:
    seen: dict[str, str] = {}
    for raw in question.companies:
        key = company_key(raw, api_labels)
        if key and key not in seen:
            seen[key] = company_label(raw, api_labels)
    if not seen:
        return "—"
    shown = list(seen.items())[:ROW_COMPANIES]
    cell = ", ".join(
        f"[{escape_cell(name)}]({root}companies/{key}/)" if key in homes else escape_cell(name)
        for key, name in shown
    )
    extra = len(seen) - len(shown)
    return f"{cell} +{extra}" if extra else cell


def _rounds(question: Question) -> str:
    return ", ".join(escape_cell(round_short_label(r)) for r in question.rounds) or "—"


def _levels(question: Question) -> str:
    return ", ".join(escape_cell(level_label(level)) for level in question.seniority) or "—"


def _title_cell(question: Question) -> str:
    return f"[{escape_cell(question.title)}]({site_link(question.url)})"


def index_table(
    questions: Sequence[Question],
    *,
    api_labels: dict[str, str],
    homes: frozenset[str],
    root: str,
    show_reports: bool,
    lead: str = "reported",
) -> str:
    """One index table, in the order given.

    ``lead`` picks the first column: ``reported`` (the sighting date) or
    ``added`` (the day the row entered the bank, with the sighting date kept as
    its own column so neither is mistaken for the other).
    """
    headers = ["Reported", "Company", "Question", "Format", "Round", "Level"]
    aligns = [":--", ":--", ":--", ":--", ":--", ":--"]
    if lead == "added":
        headers = ["Added"] + headers[1:] + ["Last reported"]
        aligns = aligns + [":--"]
    if show_reports:
        headers.append("Reports")
        aligns.append("--:")
    rows = []
    for q in questions:
        cells = [
            (q.added_date.isoformat() if q.added_date else "—") if lead == "added" else (reported_label(q) or "—"),
            _company_cell(q, api_labels, homes, root),
            _title_cell(q),
            escape_cell(format_label(q.type)),
            _rounds(q),
            _levels(q),
        ]
        if lead == "added":
            cells.append(reported_label(q) or "—")
        if show_reports:
            cells.append(reports_cell(q, "—"))
        rows.append(cells)
    return table(headers, aligns, rows)


def recently_reported(ordered: Sequence[Question], today: date, days: int = LATEST_DAYS) -> list[Question]:
    """Dated, not future-dated, inside the window — in the order given (newest first)."""
    return [q for q in ordered if in_window(q, today, days)]


def new_this_week(ordered: Sequence[Question], today: date) -> list[Question]:
    """Rows added to the bank in the past week, most recently added first."""
    fresh = [q for q in ordered if added_within(q, today, NEW_DAYS)]
    return sorted(fresh, key=lambda q: (-(q.added_date.toordinal() if q.added_date else 0), -q.number, q.slug))


def new_block(ordered: Sequence[Question], today: date, **table_args) -> str:
    fresh = new_this_week(ordered, today)
    if not fresh:
        return f"No questions were added in the {NEW_DAYS} days to {today.isoformat()}."
    shown = fresh[:README_NEW_ROWS]
    lines = [
        f"{plural(len(fresh), 'question')} added to the bank in the {NEW_DAYS} days to {today.isoformat()}"
        + (f"; the {len(shown)} most recent:" if len(fresh) > len(shown) else ":"),
        "",
        index_table(shown, lead="added", **table_args),
    ]
    return "\n".join(lines)


def recent_block(ordered: Sequence[Question], today: date, **table_args) -> str:
    recent = recently_reported(ordered, today)
    if not recent:
        return f"No question has a reported date in the {LATEST_DAYS} days to {today.isoformat()}."
    shown = recent[:README_RECENT_ROWS]
    lines = [
        f"The {len(shown)} most recently reported of {len(recent):,} from the past 12 months. "
        "[See all of them](latest/README.md).",
        "",
        index_table(shown, **table_args),
    ]
    return "\n".join(lines)


def ai_coding_block(ordered: Sequence[Question], today: date, **table_args) -> str:
    rows = [q for q in ordered if q.type == AI_CODING]
    if not rows:
        return "No AI coding question is in this snapshot."
    shown = rows[:README_AI_CODING_ROWS]
    recent = sum(1 for q in rows if in_window(q, today, REPORTED_DAYS))
    lines = [
        f"{plural(len(rows), 'question')} from rounds where the candidate works with an AI assistant in a real "
        f"repository; {recent:,} reported in the last {REPORTED_DAYS} days. [All of them](formats/{AI_CODING}.md).",
        "",
        index_table(shown, **table_args),
    ]
    return "\n".join(lines)


def format_counts(questions: Sequence[Question]) -> list[tuple[str, int]]:
    """Format → count, in the catalog's shelf order, then anything new A to Z."""
    counts: dict[str, int] = {}
    for q in questions:
        counts[q.type] = counts.get(q.type, 0) + 1
    known = [f for f in FORMAT_ORDER if f in counts]
    rest = sorted(f for f in counts if f not in FORMAT_ORDER)
    return [(f, counts[f]) for f in known + rest]


def format_path(question_type: str) -> str:
    return f"formats/{question_type}.md"


def formats_block(questions: Sequence[Question]) -> str:
    return " · ".join(
        f"[{escape_cell(format_label(f))} ({count:,})]({format_path(f)})" for f, count in format_counts(questions)
    )


def companies_block(by_company: dict[str, tuple[str, list[Question]]], today: date) -> str:
    """Every company with its counts, busiest first, folded so the page stays short."""
    rows = []
    ordered = sorted(by_company.items(), key=lambda item: (-len(item[1][1]), item[1][0].casefold(), item[0]))
    for key, (name, questions) in ordered:
        dated = [q for q in questions if q.reported_date is not None and q.reported_date <= today]
        newest = max(dated, key=lambda q: q.reported_date) if dated else None  # type: ignore[arg-type,return-value]
        rows.append(
            [
                f"[{escape_cell(name)}](companies/{key}/)",
                f"{len(questions):,}",
                f"{sum(1 for q in questions if in_window(q, today, REPORTED_DAYS)):,}",
                reported_label(newest) if newest else "—",
            ]
        )
    return "\n".join(
        [
            "<details>",
            f"<summary>All {len(rows):,} companies, with question counts</summary>",
            "",
            table(["Company", "Questions", f"Reported in last {REPORTED_DAYS} days", "Last reported"], [":--", "--:", "--:", ":--"], rows),
            "",
            "</details>",
        ]
    )


def _latest_path(page: int) -> str:
    return "latest/README.md" if page == 1 else f"latest/page-{page}.md"


def latest_files(ordered: Sequence[Question], today: date, **table_args) -> dict[str, str]:
    """``latest/``: every question reported in the past year, newest first, paged."""
    recent = recently_reported(ordered, today)
    pages = [recent[i : i + LATEST_PAGE_ROWS] for i in range(0, len(recent), LATEST_PAGE_ROWS)] or [[]]
    files: dict[str, str] = {}
    for number, rows in enumerate(pages, start=1):
        nav = []
        if number > 1:
            nav.append(f"[← Newer]({_latest_path(number - 1).split('/', 1)[1]})")
        nav.append(f"Page {number} of {len(pages)}")
        if number < len(pages):
            nav.append(f"[Older →]({_latest_path(number + 1).split('/', 1)[1]})")
        lines = [
            GENERATED_NOTICE,
            "",
            "# Recently reported interview questions",
            "",
            f"{plural(len(recent), 'question')} reported in the {LATEST_DAYS} days to {today.isoformat()}, "
            "newest first. A month without a day means only the month was reported.",
            "",
            "[Home](../README.md) · [By company](../README.md#companies) · [By format](../README.md#formats)",
            "",
            " · ".join(nav),
            "",
            index_table(rows, root="../", **table_args) if rows else "Nothing in this window.",
            "",
            " · ".join(nav),
            "",
        ]
        files[_latest_path(number)] = "\n".join(lines)
    return files


def format_files(ordered: Sequence[Question], today: date, **table_args) -> dict[str, str]:
    """``formats/<format>.md``: one format's questions, newest first."""
    files: dict[str, str] = {}
    for question_type, count in format_counts(ordered):
        rows = [q for q in ordered if q.type == question_type]
        shown = rows[:FORMAT_PAGE_ROWS]
        label = format_label(question_type)
        lines = [
            GENERATED_NOTICE,
            "",
            f"# {label} interview questions",
            "",
            f"{plural(count, 'question')} as of {today.isoformat()}, most recently reported first; "
            "questions with no reported date come last.",
            "",
            "[Home](../README.md) · [Newest across all formats](../latest/README.md)",
            "",
        ]
        if len(rows) > len(shown):
            lines += [
                f"Showing the {len(shown):,} most recently reported of {len(rows):,}. "
                "[data/questions.csv](../data/questions.csv) has every row.",
                "",
            ]
        lines += [index_table(shown, root="../", **table_args), ""]
        files[format_path(question_type)] = "\n".join(lines)
    return files
