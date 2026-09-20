#!/usr/bin/env python3
"""The pages that carry the *value*, not the index: statistics, free practice,
free reading, and what candidates reported this week.

── Why these pages exist ────────────────────────────────────────────────────

The rest of this repository answers "where is the question about X". These four
surfaces answer the questions somebody actually arrives with:

  * ``insights/`` — **what is being asked, where, and what should I do first.**
    Derived entirely from metadata this repository already carries, which makes
    it the one thing here that exists nowhere else: the site has the questions,
    but the SHAPE of two thousand of them (which topic recurs across employers,
    which question turns up at sixteen different loops, which company went quiet
    this quarter) is only visible from the whole set at once.
  * ``free/`` — **what you can practise today without paying.** A tracker that
    links every row into a paywall is an advertisement; one that says plainly
    which 167 rows are open, and orders them into something you could work
    through, is a study plan. The tier comes from the catalog's own
    ``accessTier`` — it is never asserted here.
  * ``guides/by-topic.md`` — the free writeups, grouped by what they teach
    rather than by who they are about.
  * ``experiences/`` — the newest candidate-written reports. The freshest thing
    the catalog has: a question enters the bank when someone curates it, a
    report lands the week the loop happened.

── The rules ────────────────────────────────────────────────────────────────

Every rule :mod:`insights` holds about the numbers, this module holds about
their presentation:

  * **A share prints its denominator on the page**, not in a comment. Half the
    bank carries no topic label; a topic table that does not say so is a chart
    that invents its own axis.
  * **An unmeasured cell is ``—``, and a measured zero is ``0``.** A company
    whose questions carry no sighting date has an unknown recent-activity
    count, not a quiet quarter, and a reader cannot tell those apart unless the
    renderer refuses to.
  * **An empty window is a sentence, never a table of zeros.**
  * **Nothing here claims quality.** These pages say what the catalog counted.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

from catalog import Catalog, Experience, Guide, Question, parse_catalog_date
from insights import (
    DIFFICULTIES,
    Insights,
    ROUND_LABELS,
    ROUND_ORDER,
    TIER_LABELS,
    TIER_ORDER,
    TOP_COMPANIES,
    WINDOW_DAYS,
    window_start,
)
from labels import company_label, difficulty_label, format_label
from render import (
    GENERATED_NOTICE,
    MONTH_NAMES,
    bar,
    plural,
    columns_for,
    date_label,
    escape_cell,
    percent,
    question_link,
    render_shard,
    render_table,
    table,
)

SITE = "https://trueinterview.io"

#: Rows on the "most active this quarter" and "every company" tables.
COMPANY_ROWS = TOP_COMPANIES

#: How many months the trends page draws before it stops. Long enough to show a
#: hiring cycle twice over; the month pages themselves carry the rest.
TREND_MONTHS = 24

#: Free questions listed inline on the free index before a reader is better
#: served by the per-format page.
FREE_INDEX_ROWS = 40


def _stamp(today: date) -> str:
    return date_label(today)


def _window_phrase(insights: Insights) -> str:
    start = window_start(insights.today, insights.window_days)
    return f"{date_label(start)} → {_stamp(insights.today)}"


# ── insights/README.md ───────────────────────────────────────────────────────


def _window_section(insights: Insights) -> list[str]:
    """What moved this quarter — or a sentence saying nothing did.

    A window with no sightings in it is a real and recurring state for this
    catalog (the newest sighting is routinely several weeks old), and rendering
    it as a table of zeros makes a working pipeline look like a broken one. So
    the empty case gets prose, and the prose names the most recent sighting we
    do have, which is the fact a reader wanted the window for anyway.
    """
    body = [
        f"## The last {insights.window_days} days",
        "",
    ]
    if not insights.window_total:
        body += [
            f"**No sighting has been recorded in this window** ({_window_phrase(insights)}). "
            + (
                f"The most recent one in the whole bank is {date_label(insights.latest_sighting)}."
                if insights.latest_sighting
                else "The bank carries no sighting dates at all."
            ),
            "",
            "That is a statement about what has been *reported*, not about whether these companies "
            "are interviewing. [Report a sighting](../../../issues/new?template=question-report.yml) "
            "and it lands here on the next sync.",
            "",
        ]
        return body

    largest = max((count for _, count in insights.window_formats), default=0)
    body += [
        f"**{insights.window_total:,} sightings** recorded between {_window_phrase(insights)} — "
        f"{percent(insights.window_total, insights.dated)} of the "
        f"{insights.dated:,} questions in the bank that carry a sighting date at all.",
        "",
        "### By format",
        "",
        table(
            ["Format", "Sightings", "Share of the window", ""],
            [":--", "--:", "--:", ":--"],
            [
                [
                    f"[{format_label(key)}](../formats/{key}.md)",
                    f"{count:,}",
                    percent(count, insights.window_total),
                    bar(count, largest),
                ]
                for key, count in insights.window_formats
            ],
        ),
        "",
    ]

    if insights.window_companies:
        top = insights.window_companies[:15]
        largest_company = max(count for _, _, count in top)
        body += [
            "### Where",
            "",
            table(
                ["Company", "Sightings", ""],
                [":--", "--:", ":--"],
                [
                    [
                        f"[{escape_cell(name)}](../companies/{key}.md)",
                        f"{count:,}",
                        bar(count, largest_company),
                    ]
                    for key, name, count in top
                ],
            ),
            "",
            f"<sub>A question reported at several employers counts under each, so this column sums "
            f"to more than the {insights.window_total:,} sightings above. "
            f"[Every company →](companies.md)</sub>",
            "",
        ]
    return body


def _format_section(insights: Insights) -> list[str]:
    rows = []
    for row in insights.formats:
        rows.append(
            [
                f"[{row.label}](../formats/{row.key}.md)",
                f"{row.total:,}",
                percent(row.total, insights.total),
                f"{row.window:,}",
                f"{row.difficulty['easy']:,}",
                f"{row.difficulty['medium']:,}",
                f"{row.difficulty['hard']:,}",
                f"{row.difficulty_known:,}",
                f"{row.free:,}",
            ]
        )
    return [
        "## Formats",
        "",
        "Every question is asked in exactly one format, so this column sums to the whole bank.",
        "",
        table(
            ["Format", "Questions", "Share", f"Last {insights.window_days}d", "Easy", "Medium", "Hard", "Graded", "Free"],
            [":--", "--:", "--:", "--:", "--:", "--:", "--:", "--:", "--:"],
            rows,
        ),
        "",
        "<sub>*Graded* is how many of that format's questions carry a difficulty at all — the "
        "easy/medium/hard columns are counted out of it, never out of the whole format. *Free* is "
        "how many open without a paid plan.</sub>",
        "",
    ]


def _difficulty_section(insights: Insights) -> list[str]:
    known = insights.difficulty_known
    if not known:
        return []
    largest = max(insights.difficulty.values())
    return [
        "## Difficulty",
        "",
        f"Of the **{known:,} questions the catalog has graded** "
        f"({percent(known, insights.total)} of the bank):",
        "",
        table(
            ["Difficulty", "Questions", "Share of graded", ""],
            [":--", "--:", "--:", ":--"],
            [
                [
                    difficulty_label(level) or level,
                    f"{insights.difficulty[level]:,}",
                    percent(insights.difficulty[level], known),
                    bar(insights.difficulty[level], largest),
                ]
                for level in DIFFICULTIES
            ],
        ),
        "",
        f"<sub>The other {insights.total - known:,} carry no grade. That is not *easy* — it is "
        "ungraded, and the two are only the same number if you let them be.</sub>",
        "",
    ]


def _rounds_section(insights: Insights) -> list[str]:
    if not insights.rounds:
        return []
    largest = max(count for _, count in insights.rounds)
    return [
        "## Where in the loop",
        "",
        f"Of the **{insights.rounds_known:,} questions that name a round** "
        f"({percent(insights.rounds_known, insights.total)} of the bank):",
        "",
        table(
            ["Round", "Questions", ""],
            [":--", "--:", ":--"],
            [
                [ROUND_LABELS.get(key, key), f"{count:,}", bar(count, largest)]
                for key, count in insights.rounds
            ],
        ),
        "",
        "<sub>One question can be reported in more than one round — the same problem turns up in a "
        "phone screen at one company and onsite at another — so this column sums to more than the "
        "row above it.</sub>",
        "",
    ]


def _topics_section(insights: Insights, limit: int) -> list[str]:
    if not insights.topics:
        return []
    top = insights.topics[:limit]
    largest = top[0].total
    return [
        "## Topics",
        "",
        f"Of the **{insights.topics_known:,} questions that carry a topic label** "
        f"({percent(insights.topics_known, insights.total)} of the bank — the rest are unlabelled, "
        "which is not the same as having no topic):",
        "",
        table(
            ["Topic", "Questions", "Share of labelled", f"Last {insights.window_days}d", ""],
            [":--", "--:", "--:", "--:", ":--"],
            [
                [
                    f"`{escape_cell(row.topic)}`",
                    f"{row.total:,}",
                    percent(row.total, insights.topics_known),
                    f"{row.window:,}",
                    bar(row.total, largest),
                ]
                for row in top
            ],
        ),
        "",
        f"[**Every topic, with difficulty mix and who asks it →**](topics.md)",
        "",
    ]


def _breadth_section(insights: Insights, api_labels: dict[str, str]) -> list[str]:
    if not insights.breadth:
        return []
    return [
        "## Asked at the most companies",
        "",
        "The closest thing this data has to an instruction. A question reported at one employer "
        "may be that employer's habit; a question reported at a dozen is the industry's. These are "
        "ranked by **how many different companies** they have been reported at — a fact about the "
        "bank, not an opinion of ours.",
        "",
        table(
            ["Question", "Companies", "Format", "Difficulty", "Reported at"],
            [":--", "--:", ":--", ":-:", ":--"],
            [
                [
                    question_link(question),
                    f"**{len(question.companies)}**",
                    format_label(question.type),
                    difficulty_label(question.difficulty) or "—",
                    ", ".join(escape_cell(company_label(c, api_labels)) for c in question.companies[:4])
                    + (" …" if len(question.companies) > 4 else ""),
                ]
                for question in insights.breadth
            ],
        ),
        "",
    ]


def _tiers_section(insights: Insights) -> list[str]:
    counts = insights.tiers
    if not counts:
        return []
    rows = []
    for tier in TIER_ORDER:
        count = counts.get(tier, 0)
        if not count:
            continue
        rows.append([TIER_LABELS.get(tier, tier), f"{count:,}", percent(count, insights.total)])
    for tier in sorted(set(counts) - set(TIER_ORDER)):
        rows.append([tier, f"{counts[tier]:,}", percent(counts[tier], insights.total)])
    return [
        "## What it costs to open one",
        "",
        table(["Plan needed", "Questions", "Share of bank"], [":--", "--:", "--:"], rows),
        "",
        f"<sub>Straight from the catalog's own `accessTier`, never asserted here. The "
        f"**{insights.free_total:,} free ones are listed in full** — "
        f"[start there](../free/README.md).</sub>",
        "",
    ]


def _months_section(insights: Insights, limit: int) -> list[str]:
    if not insights.months:
        return []
    shown = [row for row in insights.months if row.key <= f"{insights.today.year:04d}-{insights.today.month:02d}"][
        :limit
    ]
    if not shown:
        return []
    largest = max(row.total for row in shown)
    return [
        "## Month by month",
        "",
        table(
            ["Month", "Sightings", "Companies", ""],
            [":--", "--:", "--:", ":--"],
            [
                [
                    f"[{_month_name(row.key)}](../by-month/{row.key}.md)",
                    f"{row.total:,}",
                    f"{row.companies:,}",
                    bar(row.total, largest),
                ]
                for row in shown
            ],
        ),
        "",
        "[**The full trend, with what each month's format mix was →**](trends.md)",
        "",
    ]


def _month_name(key: str) -> str:
    year, month = key.split("-")
    return f"{MONTH_NAMES[int(month) - 1]} {year}"


def insights_index(insights: Insights, api_labels: dict[str, str]) -> str:
    coverage = (
        f"Sightings run from {date_label(insights.earliest_sighting)} to "
        f"{date_label(insights.latest_sighting)}."
        if insights.latest_sighting
        else "No sighting dates are recorded in the bank."
    )
    body = [
        GENERATED_NOTICE,
        "",
        "# What companies are actually asking",
        "",
        f"**{insights.total:,} tracked questions** across **{len(insights.companies)} companies**, "
        f"counted rather than claimed. {coverage} Windows below are measured against "
        f"**{_stamp(insights.today)}**, and everything on this page is recomputed hourly.",
        "",
        "[← Question bank](../README.md) · [Topics](topics.md) · [Companies](companies.md) · "
        "[Trends](trends.md) · [Free to practise](../free/README.md)",
        "",
        f"> **What the numbers are counted over.** {insights.dated:,} of the "
        f"{insights.total:,} questions carry a sighting date and "
        f"{insights.undated:,} do not; an undated question is *unmeasured*, not *old*, so it is "
        "in every total below and in no window. Every share on this page names the population it "
        "is a share of, because most of them are not the whole bank."
        + (
            (
                f" {insights.future_dated:,} rows carry"
                if insights.future_dated != 1
                else " One row carries"
            )
            + " a sighting dated after today — a mistyped date upstream rather than a forecast, "
            "so it is counted as dated and excluded from every window."
            if insights.future_dated
            else ""
        ),
        "",
    ]
    body += _window_section(insights)
    body += _format_section(insights)
    body += _difficulty_section(insights)
    body += _rounds_section(insights)
    body += _topics_section(insights, 12)
    body += _breadth_section(insights, api_labels)
    body += _tiers_section(insights)
    body += _months_section(insights, 12)
    body += [
        "---",
        "",
        f"<sub>Generated by [`scripts/sync.py`](../scripts/sync.py) from the public catalog API. "
        f"The machine-readable version of this page is [`data/insights.json`](../data/insights.json).</sub>",
        "",
    ]
    return "\n".join(body)


# ── insights/topics.md ───────────────────────────────────────────────────────


def topics_page(insights: Insights) -> str:
    body = [
        GENERATED_NOTICE,
        "",
        "# Topics",
        "",
        f"What the bank is *about*, counted over the **{insights.topics_known:,} questions that "
        f"carry a topic label** — {percent(insights.topics_known, insights.total)} of "
        f"{insights.total:,}. The unlabelled rest are not a topic called *other*; they are rows "
        "nobody has labelled yet, and they are excluded from every share on this page rather than "
        "quietly bulking one out.",
        "",
        "[← Insights](README.md) · [← Question bank](../README.md)",
        "",
    ]
    if not insights.topics:
        body += ["_No topic labels are recorded in the bank yet._", ""]
        return "\n".join(body)

    body += [
        table(
            ["Topic", "Questions", "Share", f"Last {insights.window_days}d", "Easy", "Medium", "Hard", "Asked most at"],
            [":--", "--:", "--:", "--:", "--:", "--:", "--:", ":--"],
            [
                [
                    f"`{escape_cell(row.topic)}`",
                    f"{row.total:,}",
                    percent(row.total, insights.topics_known),
                    f"{row.window:,}",
                    f"{row.difficulty['easy']:,}",
                    f"{row.difficulty['medium']:,}",
                    f"{row.difficulty['hard']:,}",
                    ", ".join(f"[{escape_cell(name)}](../companies/{key}.md)" for key, name, _ in row.companies[:3])
                    or "—",
                ]
                for row in insights.topics
            ],
        ),
        "",
        "<sub>Easy/Medium/Hard are counted over the graded questions in that topic, so they sum to "
        "less than the topic's total wherever the catalog has not graded a row.</sub>",
        "",
        "## Where to start in each",
        "",
        "The most recently reported question carrying each label — newest sighting first, so this "
        "is what somebody was actually asked, not what sorts first alphabetically.",
        "",
    ]
    for row in insights.topics:
        if not row.examples:
            continue
        body += [
            f"**`{escape_cell(row.topic)}`** — {row.total:,} questions",
            "",
            "\n".join(
                f"- {question_link(q)} · {format_label(q.type)}"
                + (f" · {difficulty_label(q.difficulty)}" if q.difficulty else "")
                for q in row.examples
            ),
            "",
        ]
    return "\n".join(body)


# ── insights/companies.md ────────────────────────────────────────────────────


def companies_page(insights: Insights) -> str:
    body = [
        GENERATED_NOTICE,
        "",
        "# Companies, by what they are asking",
        "",
        f"**{len(insights.companies)} companies**. The first table is who has been *reported* most "
        f"in the last {insights.window_days} days; the second is every company the bank carries.",
        "",
        "[← Insights](README.md) · [← Question bank](../README.md)",
        "",
    ]

    # Ranked over EVERY company with a sighting in the window, then capped.
    # `insights.companies` is ordered by lifetime question count, so capping
    # first would have dropped a company that is busy this quarter and small
    # overall — which is the exact row this table exists to surface.
    active = sorted(
        (row for row in insights.companies if row.window),
        key=lambda r: (-r.window, r.name.casefold(), r.key),
    )[:COMPANY_ROWS]
    if active:
        largest = active[0].window
        body += [
            f"## Most reported in the last {insights.window_days} days",
            "",
            table(
                ["Company", f"Last {insights.window_days}d", "Questions", "Last sighting", ""],
                [":--", "--:", "--:", ":--", ":--"],
                [
                    [
                        f"[{escape_cell(row.name)}](../companies/{row.key}.md)",
                        f"{row.window:,}",
                        f"{row.questions:,}",
                        date_label(row.last_seen),
                        bar(row.window, largest),
                    ]
                    for row in active
                ],
            ),
            "",
        ]
    else:
        body += [
            f"## Most reported in the last {insights.window_days} days",
            "",
            "_No sighting has been recorded in that window._ Every company below is still in the "
            "bank; what is missing is a recent report, which is a fact about reporting rather than "
            "about hiring.",
            "",
        ]

    body += [
        "## Every company",
        "",
        table(
            [
                "Company",
                "Questions",
                "Guides",
                "Free",
                f"Last {insights.window_days}d",
                "Last sighting",
                "Most asked format",
                "Most asked topic",
            ],
            [":--", "--:", "--:", "--:", "--:", ":--", ":--", ":--"],
            [
                [
                    f"[{escape_cell(row.name)}](../companies/{row.key}.md)",
                    f"{row.questions:,}",
                    f"{row.guides:,}",
                    f"{row.free:,}",
                    # A company none of whose questions carry a date has an
                    # UNKNOWN recent count, not a zero one. Printing 0 there
                    # would say we looked and found nothing.
                    (f"{row.window:,}" if row.dated else "—"),
                    date_label(row.last_seen),
                    row.top_format or "—",
                    f"`{escape_cell(row.top_topic)}`" if row.top_topic else "—",
                ]
                for row in insights.companies
            ],
        ),
        "",
        f"<sub>A dash in the last-{insights.window_days}-days column means none of that company's "
        "questions carry a sighting date at all, so the window could not be measured — different "
        "from a measured zero. *Guides* counts the Study-section writeups in "
        "[guides/](../guides/README.md); *Free* counts questions that open without a paid plan.</sub>",
        "",
    ]
    return "\n".join(body)


# ── insights/trends.md ───────────────────────────────────────────────────────


def trends_page(insights: Insights) -> str:
    months = list(insights.months)[:TREND_MONTHS]
    body = [
        GENERATED_NOTICE,
        "",
        "# Trends by month",
        "",
        f"Counted over the **{insights.dated:,} questions that carry a sighting date**. The other "
        f"{insights.undated:,} are absent from every row here for the reason the month pages "
        "exclude them: a month is a claim about when something was asked, and an undated row "
        "cannot stand behind it.",
        "",
        "[← Insights](README.md) · [← Every month](../by-month/README.md)",
        "",
    ]
    if not months:
        body += ["_No sighting dates are recorded in the bank yet._", ""]
        return "\n".join(body)

    largest = max(row.total for row in months)
    format_keys = sorted({key for row in months for key in row.formats})
    body += [
        table(
            ["Month", "Sightings", "Companies", *[format_label(k) for k in format_keys], ""],
            [":--", "--:", "--:", *["--:" for _ in format_keys], ":--"],
            [
                [
                    f"[{_month_name(row.key)}](../by-month/{row.key}.md)",
                    f"{row.total:,}",
                    f"{row.companies:,}",
                    *[f"{row.formats.get(key, 0):,}" for key in format_keys],
                    bar(row.total, largest),
                ]
                for row in months
            ],
        ),
        "",
        "<sub>A month dated after today is a data-entry error upstream rather than a forecast; it "
        "is listed here for the same reason it keeps its month page — so the error is visible to "
        "the people who can fix it.</sub>",
        "",
        "## First seen",
        "",
        "The month a company's earliest recorded sighting falls in. A company appearing here is a "
        "new employer in the bank, not necessarily a new employer in the market.",
        "",
    ]
    new_rows = [row for row in months if row.new_companies]
    if new_rows:
        body += [
            table(
                ["Month", "Companies first reported"],
                [":--", ":--"],
                [
                    [f"[{_month_name(row.key)}](../by-month/{row.key}.md)", ", ".join(escape_cell(n) for n in row.new_companies)]
                    for row in new_rows
                ],
            ),
            "",
        ]
    else:
        body += ["_No company's first sighting falls inside this window._", ""]
    return "\n".join(body)


# ── free/ ────────────────────────────────────────────────────────────────────


def _free_sort_key(question: Question) -> tuple:
    """Easiest first, then most-reported, then slug.

    A free list is a starting point, so it is ordered the way somebody would
    work through it rather than by recency. Ungraded rows sort after the graded
    ones instead of being guessed at a level.
    """
    order = {"easy": 0, "medium": 1, "hard": 2}
    return (
        order.get(question.difficulty or "", 3),
        -len(question.companies),
        question.title.casefold(),
        question.slug,
    )


def free_pages(
    questions: Sequence[Question],
    insights: Insights,
    api_labels: dict[str, str],
    today: date,
) -> dict[str, str]:
    """The free tier: an index, and one paginated page per format."""
    free = [q for q in questions if q.access_tier == "free"]
    by_format: dict[str, list[Question]] = {}
    for question in free:
        by_format.setdefault(question.type, []).append(question)

    files: dict[str, str] = {}
    cols = columns_for("free", api_labels, today)
    for key, rows in by_format.items():
        files.update(
            render_shard(
                base=f"free/{key}",
                title=f"Free {format_label(key)} questions",
                lede=(
                    f"**{len(rows):,} {format_label(key)} questions** that open without a paid "
                    f"plan — full statement, editor and judged verdict. Easiest first."
                ),
                back="[← Free questions](README.md) · [← Question bank](../README.md)",
                questions=sorted(rows, key=_free_sort_key),
                cols=cols,
                today=today,
                # The lede says "easiest first"; the default shard order is
                # newest-sighting first, which would contradict it in place.
                preserve_order=True,
            )
        )

    ordered = sorted(free, key=_free_sort_key)
    format_keys = [row.key for row in insights.formats if row.key in by_format]
    body = [
        GENERATED_NOTICE,
        "",
        "# Free questions",
        "",
        f"**{len(free):,} of the {insights.total:,} tracked questions open without a paid plan** "
        f"({percent(len(free), insights.total)}) — the whole statement, the editor, the test cases "
        f"you can run, and a judged verdict. This list is the catalog's own `accessTier`, "
        "regenerated hourly: nothing here is a claim this repository makes on the site's behalf.",
        "",
        "[← Question bank](../README.md) · [What companies are asking](../insights/README.md) · "
        "[Free reading](../guides/README.md)",
        "",
    ]
    if not free:
        body += ["_No question in the bank is currently on the free tier._", ""]
        files["free/README.md"] = "\n".join(body)
        return files

    body += [
        "| Format | Free questions | Easy | Medium | Hard |",
        "| :-- | --: | --: | --: | --: |",
    ]
    for key in format_keys:
        rows = by_format[key]
        mix = {level: sum(1 for q in rows if q.difficulty == level) for level in DIFFICULTIES}
        body.append(
            f"| [{format_label(key)}]({key}.md) | {len(rows):,} | "
            f"{mix['easy']:,} | {mix['medium']:,} | {mix['hard']:,} |"
        )
    body += [
        "",
        "## Start here",
        "",
        f"The {min(FREE_INDEX_ROWS, len(ordered))} to open first: easiest first, and within a "
        "level the ones reported at the most companies, because a question several employers ask "
        "is worth more of an evening than one that has been seen once.",
        "",
        render_table(ordered[:FREE_INDEX_ROWS], cols),
        "",
        f"<sub>The rest are on the per-format pages above. A dash in *Reported* means no sighting "
        "date was recorded, which is not the same as old.</sub>",
        "",
    ]
    files["free/README.md"] = "\n".join(body)
    return files


# ── guides/by-topic.md ───────────────────────────────────────────────────────


def guides_by_topic(guides: Sequence[Guide], api_labels: dict[str, str]) -> str:
    """The free writeups, grouped by what they teach.

    A guide is filed under EVERY tag it carries, because "show me the
    system-design rounds" and "show me the behavioural ones" are both questions
    somebody has, and a guide about a machine-learning deep dive answers both.
    The count of untagged guides is printed rather than bucketed into a
    synthetic *other*: they are reachable by company, and saying where beats
    inventing a label for them.
    """
    by_tag: dict[str, list[Guide]] = {}
    untagged = 0
    for guide in guides:
        if not guide.tags:
            untagged += 1
            continue
        for tag in guide.tags:
            by_tag.setdefault(tag, []).append(guide)

    body = [
        GENERATED_NOTICE,
        "",
        "# Free reading, by topic",
        "",
        f"**{len(guides):,} writeups**, grouped by what each one is about. They are "
        "free to read on the site. A guide carrying several topics is listed under each.",
        "",
        "[← By company](README.md) · [← Question bank](../README.md)",
        "",
    ]
    if untagged:
        body += [
            f"> **{untagged:,} of them carry no topic label** and are therefore absent from this "
            "page. They are not missing: every one is on [its company's "
            "section](README.md), which is the other way into the same set.",
            "",
        ]
    if not by_tag:
        body += ["_No guide carries a topic label yet._", ""]
        return "\n".join(body)

    for tag in sorted(by_tag, key=lambda t: (-len(by_tag[t]), t)):
        rows = sorted(by_tag[tag], key=lambda g: (g.title.casefold(), g.slug))
        body += [
            f"### `{escape_cell(tag)}`",
            "",
            f"<sub>{plural(len(rows), 'guide')}</sub>",
            "",
            table(
                ["Guide", "Company"],
                [":--", ":--"],
                [
                    [
                        f"[{escape_cell(guide.title)}]({guide.url})",
                        " / ".join(escape_cell(company_label(c, api_labels)) for c in guide.companies) or "—",
                    ]
                    for guide in rows
                ],
            ),
            "",
        ]
    return "\n".join(body)


# ── experiences/ ─────────────────────────────────────────────────────────────


def experiences_page(catalog: Catalog, api_labels: dict[str, str], today: date) -> str:
    """The newest candidate-written reports.

    A SLICE by construction — the endpoint takes a limit and no offset — so the
    page says which slice, and how much of the board it is not showing. A
    section headed "interview reports" that silently held fifty of two thousand
    would be exactly the failure the guides index was rebuilt to stop.
    """
    rows = sorted(
        catalog.experiences,
        key=lambda e: (-(e.posted_date.toordinal() if e.posted_date else 0), e.title.casefold(), e.id),
    )
    total = catalog.experiences_total
    body = [
        GENERATED_NOTICE,
        "",
        "# Latest interview reports",
        "",
        "What candidates say happened in the room — written up by the people who sat the loop, "
        "and the freshest thing this repository points at: a question enters the bank when "
        "somebody curates it, a report lands the week the interview happened.",
        "",
        "[← Question bank](../README.md) · [What companies are asking](../insights/README.md)",
        "",
    ]
    if not rows:
        body += ["_No interview report was published in this snapshot._", ""]
        return "\n".join(body)

    if total and total > len(rows):
        body += [
            f"> **These are the {len(rows)} newest of {total:,} reports on the board.** The "
            f"catalog API hands over one capped page and takes no offset, so this page cannot "
            f"carry the rest; they are all at [the board]({SITE}/interviews).",
            "",
        ]
    body += [
        table(
            ["Company", "Role", "Report", "Posted"],
            [":--", ":--", ":--", ":--"],
            [
                [
                    f"**{escape_cell(company_label(row.company, api_labels)) or '—'}**",
                    escape_cell(row.role) if row.role else "—",
                    f"[{escape_cell(row.title)}]({row.url})",
                    date_label(row.posted_date),
                ]
                for row in rows
            ],
        ),
        "",
        f"[**Every report on the board →**]({SITE}/interviews)",
        "",
    ]
    return "\n".join(body)


# ── data/insights.json ───────────────────────────────────────────────────────


def insights_json(insights: Insights) -> str:
    """The statistics pages as data, so nobody has to parse the markdown.

    Sorted keys and two-space indent on purpose: this file is regenerated
    hourly, and a stable key order is what makes its diff a list of the numbers
    that actually moved rather than a re-emitted blob. Every share is left OUT
    and its two inputs are published instead — a consumer can divide, and a
    published percentage would be the one place a denominator could go missing.
    """
    import json

    payload = {
        "generated": insights.today.isoformat(),
        "windowDays": insights.window_days,
        "totals": {
            "questions": insights.total,
            "companies": len(insights.companies),
            "guides": insights.guides_total,
            "dated": insights.dated,
            "undated": insights.undated,
            "free": insights.free_total,
            "inWindow": insights.window_total,
            "inLastYear": insights.year_total,
            "futureDated": insights.future_dated,
            "gradedDifficulty": insights.difficulty_known,
            "labelledTopic": insights.topics_known,
            "namingARound": insights.rounds_known,
        },
        "latestSighting": insights.latest_sighting.isoformat() if insights.latest_sighting else None,
        "earliestSighting": insights.earliest_sighting.isoformat() if insights.earliest_sighting else None,
        "difficulty": dict(insights.difficulty),
        "accessTiers": dict(insights.tiers),
        "rounds": [{"round": key, "questions": count} for key, count in insights.rounds],
        "formats": [
            {
                "format": row.key,
                "label": row.label,
                "questions": row.total,
                "inWindow": row.window,
                "graded": row.difficulty_known,
                "free": row.free,
                **{level: row.difficulty[level] for level in DIFFICULTIES},
            }
            for row in insights.formats
        ],
        "topics": [
            {
                "topic": row.topic,
                "questions": row.total,
                "inWindow": row.window,
                "graded": row.difficulty_known,
                **{level: row.difficulty[level] for level in DIFFICULTIES},
                "topCompanies": [{"slug": key, "name": name, "questions": count} for key, name, count in row.companies],
            }
            for row in insights.topics
        ],
        "companies": [
            {
                "slug": row.key,
                "name": row.name,
                "questions": row.questions,
                "guides": row.guides,
                "free": row.free,
                "dated": row.dated,
                "inWindow": row.window if row.dated else None,
                "inLastYear": row.year if row.dated else None,
                "lastSighting": row.last_seen.isoformat() if row.last_seen else None,
                "topFormat": row.top_format,
                "topTopic": row.top_topic,
            }
            for row in insights.companies
        ],
        "months": [
            {
                "month": row.key,
                "questions": row.total,
                "companies": row.companies,
                "formats": dict(row.formats),
                "firstReportedCompanies": list(row.new_companies),
            }
            for row in insights.months
        ],
        "mostAskedAcrossCompanies": [
            {
                "slug": question.slug,
                "title": question.title,
                "companies": len(question.companies),
                "format": question.type,
                "difficulty": question.difficulty,
                "accessTier": question.access_tier,
                "url": question.url,
            }
            for question in insights.breadth
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


# ── README blocks ────────────────────────────────────────────────────────────


def readme_insights_block(insights: Insights, api_labels: dict[str, str]) -> str:
    """The landing page's at-a-glance line: what moved, where, and what recurs.

    Three claims, each one a link away from the page that shows its working.
    This is the block that answers a visitor who has not decided to browse
    anything yet — the navigation below it is useless to somebody who does not
    yet know which company to click.
    """
    lines: list[str] = []
    if insights.window_total:
        mix = " · ".join(
            f"{format_label(key)} {count:,}" for key, count in insights.window_formats
        )
        lines.append(
            f"**Last {insights.window_days} days:** {insights.window_total:,} sightings at "
            f"{sum(1 for row in insights.companies if row.window):,} companies — {mix}."
        )
        if insights.window_companies:
            named = " · ".join(
                f"[{escape_cell(name)} ({count})](companies/{key}.md)"
                for key, name, count in insights.window_companies[:8]
            )
            lines.append(f"**Reported most:** {named}")
    else:
        lines.append(
            f"**Last {insights.window_days} days:** no sighting recorded"
            + (
                f" — the most recent in the bank is {date_label(insights.latest_sighting)}."
                if insights.latest_sighting
                else "."
            )
        )
    if insights.breadth:
        asked = " · ".join(
            f"[{escape_cell(q.title)}]({q.url}) ({len(q.companies)})" for q in insights.breadth[:5]
        )
        lines.append(f"**Asked at the most companies:** {asked}")
    lines.append("")
    lines.append(
        "[**What companies are actually asking →**](insights/README.md) &nbsp;·&nbsp; "
        "[Topics](insights/topics.md) &nbsp;·&nbsp; [Companies](insights/companies.md) "
        "&nbsp;·&nbsp; [Trends](insights/trends.md) &nbsp;·&nbsp; "
        "[`insights.json`](data/insights.json)"
    )
    return "\n\n".join(line for line in lines if line != "") + "\n"


def readme_free_block(insights: Insights) -> str:
    if not insights.free_total:
        return "_No question in the bank is currently on the free tier._"
    mix = " · ".join(
        f"[{row.label} ({row.free:,})](free/{row.key}.md)" for row in insights.formats if row.free
    )
    return (
        f"**{insights.free_total:,} of the {insights.total:,} tracked questions open without a "
        f"paid plan** — the full statement, a runnable editor and a judged verdict.\n\n"
        f"{mix}\n\n"
        f"[**Every free question, easiest first →**](free/README.md)"
    )


def readme_experiences_block(catalog: Catalog, api_labels: dict[str, str], rows: int) -> str:
    """The newest reports, or nothing at all.

    An empty block rather than an empty table: a heading over "no reports" on
    the landing page reads as a broken section, and the section is optional.
    """
    ordered = sorted(
        catalog.experiences,
        key=lambda e: (-(e.posted_date.toordinal() if e.posted_date else 0), e.title.casefold(), e.id),
    )[:rows]
    if not ordered:
        return "_No interview report was published in this snapshot._"
    total = catalog.experiences_total
    body = table(
        ["Company", "Role", "Report", "Posted"],
        [":--", ":--", ":--", ":--"],
        [
            [
                f"**{escape_cell(company_label(row.company, api_labels)) or '—'}**",
                escape_cell(row.role) if row.role else "—",
                f"[{escape_cell(row.title)}]({row.url})",
                date_label(row.posted_date),
            ]
            for row in ordered
        ],
    )
    tail = (
        f"[**{total:,} reports on the board →**](experiences/README.md)"
        if total
        else "[**Every report →**](experiences/README.md)"
    )
    return f"{body}\n\n{tail}"
