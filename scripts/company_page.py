#!/usr/bin/env python3
"""The company page: how one employer interviews, before the list of questions.

── Why this page changed shape ──────────────────────────────────────────────

A company page used to be a table of questions with a list of guides on top.
That answers "what has been asked at Amazon", which is the second question
somebody has. The first one is **"what is the loop, and what should I do
first?"** — how many rounds, which of them is an online assessment, what they
lean on, whether any of it is recent, and which five questions to open tonight.

Every one of those is already in the metadata this repository carries; none of
it was being shown. So the page now opens with the shape of the loop and closes
with the catalog, and everything between is counted from the same records the
table below is rendered from — a number on this page and a row under it cannot
disagree, because they are the same rows.

── The rules, which are the repository's own ────────────────────────────────

  * **Nothing here is asserted about a company.** Every sentence is a count of
    what was REPORTED. "Amazon runs an online assessment" is not a claim this
    file makes; "88 of the questions reported at Amazon name an online
    assessment" is, and it is checkable from `data/questions.csv`.
  * **A share names its denominator.** Two thirds of the bank carries no topic
    label and nearly half carries no sighting date, so every share is a share
    of the rows that carry the thing, and the page says which number that is.
  * **Unmeasured is not zero.** A company with no dated sighting has an unknown
    recent-activity count, and it prints as a sentence rather than as `0`.
  * **An empty section is a sentence, never a table of zeros.**
  * **What a round IS is a definition, not a claim.** `ROUND_MEANINGS` describes
    the industry-standard shape of an online assessment or an onsite; it says
    nothing about this employer, which is exactly why it can be printed beside
    a count that does.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Iterable, Sequence

from catalog import Experience, Guide, Question
from insights import DIFFICULTIES, ROUND_LABELS, ROUND_ORDER, WINDOW_DAYS
from labels import company_key, company_label, difficulty_label, format_label
from render import (
    MONTH_NAMES,
    bar,
    plural,
    date_label,
    escape_cell,
    guide_rows,
    percent,
    question_link,
    table,
)
from segments import SECTOR_BY_ID, sector_chip, segment_of

SITE = "https://trueinterview.io"

#: Job lists for the same employers, kept in sibling repositories. A candidate
#: reading a company's loop is, more often than not, deciding whether to apply.
JOB_LISTS = (
    ("New-grad roles", "https://github.com/kevin-2023-code/New-Grad-Opportunities"),
    ("Internships", "https://github.com/kevin-2023-code/Internship-Opportunities"),
)

#: What each round IS. A definition of the format — true of the industry, not a
#: claim about any employer — printed beside a count that IS about the employer.
#: Keeping the two in one table is what stops the page drifting into assertion.
ROUND_MEANINGS: dict[str, str] = {
    "oa": "A timed set you sit alone, usually before a human has read your CV.",
    "phone-screen": "45–60 minutes with an engineer, one or two problems, shared editor.",
    "onsite": "The loop itself: several back-to-back rounds, on site or over video.",
    "take-home": "A project with a deadline, reviewed after you send it.",
}

#: How many rows each block prints. Long enough to be useful, short enough that
#: the page is still an introduction to the table underneath it.
RECENT_ROWS = 12
START_HERE_ROWS = 8
TOPIC_ROWS = 10
MONTHS_SHOWN = 18


def anchor(heading: str) -> str:
    """GitHub's own anchor derivation, transcribed.

    Lowercase, drop everything that is not a letter, digit, space, hyphen or
    underscore, then turn spaces into hyphens — and do NOT trim, because a
    heading that opens with an emoji anchors at a leading hyphen. Getting this
    one character wrong makes every jump link scroll to the top of the page
    instead, which is the kind of defect nobody reports.
    """
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", heading.lower(), flags=re.UNICODE))


def _counter(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _dated(questions: Sequence[Question], today: date) -> list[Question]:
    """Rows with a sighting on or before today.

    A future-dated sighting is a data-entry error upstream, and the rule the
    rest of the repository holds applies here too: it keeps its place in the
    table and loses its claim to *recent*.
    """
    return [q for q in questions if q.reported_date is not None and q.reported_date <= today]


def _in_window(questions: Sequence[Question], today: date, days: int) -> list[Question]:
    return [q for q in _dated(questions, today) if (today - q.reported_date).days <= days]


def _difficulty_cell(questions: Sequence[Question]) -> str:
    """`12 / 30 / 9` for easy/medium/hard, or `—` when nothing is graded."""
    counts = _counter(q.difficulty for q in questions if q.difficulty in DIFFICULTIES)
    if not counts:
        return "—"
    return " / ".join(str(counts.get(level, 0)) for level in DIFFICULTIES)


def _top_format(questions: Sequence[Question]) -> tuple[str | None, int]:
    counts = _counter(q.type for q in questions)
    if not counts:
        return None, 0
    key = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return key, counts[key]


def _glance(name: str, questions: Sequence[Question], guides: Sequence[Guide],
            experiences: Sequence[Experience], today: date) -> list[str]:
    dated = _dated(questions, today)
    window = _in_window(questions, today, WINDOW_DAYS)
    top_key, top_count = _top_format(questions)
    free = sum(1 for q in questions if q.access_tier == "free")
    rows = [
        ["Questions tracked", f"**{len(questions):,}**"],
        [
            "Most recent sighting",
            date_label(max((q.reported_date for q in dated), default=None)) if dated else "— _no sighting date on file_",
        ],
        [
            f"Reported in the last {WINDOW_DAYS} days",
            f"{len(window):,}" if dated else "— _unmeasured: no row here carries a date_",
        ],
        [
            "Most common format",
            f"[{format_label(top_key)}](../formats/{top_key}.md) "
            f"({percent(top_count, len(questions))} of {len(questions):,})" if top_key else "—",
        ],
        ["Difficulty (easy / medium / hard)", _difficulty_cell(questions)],
        ["Free to practise", f"[{free:,}](../free/README.md)" if free else "0"],
        ["Round-by-round guides", f"{len(guides):,}" if guides else "0"],
    ]
    if experiences:
        rows.append(["Interview reports on the board", f"{len(experiences):,} in this snapshot"])
    return [
        "## At a glance",
        "",
        table(["", ""], [":--", ":--"], rows),
        "",
        f"<sub>Counted from the {len(questions):,} questions reported at {escape_cell(name)}. "
        f"{len(dated):,} of them carry a sighting date; the other {len(questions) - len(dated):,} are "
        "*unmeasured*, which is a different fact from *old* — they are in every total here and in no "
        "window.</sub>",
        "",
    ]


def _loop(name: str, questions: Sequence[Question]) -> list[str]:
    """The rounds this employer's questions were reported in."""
    known = [q for q in questions if q.rounds]
    if not known:
        return [
            "## The loop, as reported",
            "",
            f"**No question reported at {escape_cell(name)} names which round it came from.** That is a gap "
            "in what has been reported rather than a company with one round — "
            "[add one](../../../issues/new?template=question-report.yml) and it lands here on the next sync.",
            "",
        ]
    by_round: dict[str, list[Question]] = {}
    for question in known:
        for round_key in question.rounds:
            by_round.setdefault(round_key, []).append(question)

    ordered = [key for key in ROUND_ORDER if key in by_round]
    ordered += sorted(set(by_round) - set(ROUND_ORDER))
    largest = max(len(rows) for rows in by_round.values())

    body = []
    for key in ordered:
        rows = by_round[key]
        top_key, top_count = _top_format(rows)
        body.append(
            [
                f"**{ROUND_LABELS.get(key, key)}**",
                f"{len(rows):,}",
                bar(len(rows), largest, width=10),
                f"[{format_label(top_key)}](../formats/{top_key}.md) ({percent(top_count, len(rows))})"
                if top_key
                else "—",
                _difficulty_cell(rows),
                ROUND_MEANINGS.get(key, "—"),
            ]
        )
    return [
        "## The loop, as reported",
        "",
        f"Which stage each question came from, for the **{len(known):,} of {len(questions):,}** questions at "
        f"{escape_cell(name)} that name one. A question can be reported in more than one round — the same "
        "problem turns up in a phone screen one year and onsite the next — so this column sums to more than "
        "that.",
        "",
        table(
            ["Round", "Questions", "", "Mostly", "E / M / H", "What this round is"],
            [":--", "--:", ":--", ":--", ":-:", ":--"],
            body,
        ),
        "",
        "<sub>*E / M / H* counts the rows in that round the catalog has graded; a question with no grade is "
        "in neither column. *What this round is* describes the format, which is the same everywhere — the "
        "counts beside it are what is specific to this employer.</sub>",
        "",
    ]


def _topics(name: str, questions: Sequence[Question], today: date) -> list[str]:
    labelled = [q for q in questions if q.topics]
    if not labelled:
        return [
            "## What they ask about",
            "",
            f"**None of the {plural(len(questions), 'question')} reported at {escape_cell(name)} "
            "carries a topic label yet.** "
            "Unlabelled is not untopiced; the labels are added by hand and this employer's rows have not been "
            "reached.",
            "",
        ]
    by_topic: dict[str, list[Question]] = {}
    for question in labelled:
        for topic in question.topics:
            by_topic.setdefault(topic, []).append(question)
    ranked = sorted(by_topic.items(), key=lambda item: (-len(item[1]), item[0]))[:TOPIC_ROWS]
    largest = len(ranked[0][1])
    rows = []
    for topic, matches in ranked:
        dated = _dated(matches, today)
        rows.append(
            [
                f"`{escape_cell(topic)}`",
                f"{len(matches):,}",
                percent(len(matches), len(labelled)),
                bar(len(matches), largest, width=12),
                date_label(max((q.reported_date for q in dated), default=None)) if dated else "—",
            ]
        )
    return [
        "## What they ask about",
        "",
        f"Of the **{plural(len(labelled), 'question')} at {escape_cell(name)} that "
        f"{'carries' if len(labelled) == 1 else 'carry'} a topic label** "
        f"({percent(len(labelled), len(questions))} of them — the rest are unlabelled, which is not the same "
        "as having no topic):",
        "",
        table(
            ["Topic", "Questions", "Share of labelled", "", "Last seen"],
            [":--", "--:", "--:", ":--", ":--"],
            rows,
        ),
        "",
        "<sub>A question can carry more than one topic, so this column sums to more than the number of "
        "labelled questions. [Every topic across the whole bank →](../insights/topics.md)</sub>",
        "",
    ]


def _timeline(name: str, questions: Sequence[Question], today: date) -> list[str]:
    dated = _dated(questions, today)
    if not dated:
        return []
    by_month: dict[str, int] = {}
    for question in dated:
        by_month[f"{question.reported_date.year:04d}-{question.reported_date.month:02d}"] = (
            by_month.get(f"{question.reported_date.year:04d}-{question.reported_date.month:02d}", 0) + 1
        )
    keys = sorted(by_month, reverse=True)[:MONTHS_SHOWN]
    largest = max(by_month[key] for key in keys)
    rows = [
        [
            f"[{MONTH_NAMES[int(key[5:]) - 1]} {key[:4]}](../by-month/{key}.md)",
            f"{by_month[key]:,}",
            bar(by_month[key], largest, width=24),
        ]
        for key in keys
    ]
    first, last = min(q.reported_date for q in dated), max(q.reported_date for q in dated)
    return [
        "## When they asked it",
        "",
        f"Every recorded sighting at {escape_cell(name)}, by the month it was reported in — "
        f"{date_label(first)} to {date_label(last)}. A quiet month is a month nobody reported, which is not "
        "the same as a month nobody interviewed.",
        "",
        table(["Month", "Sightings", ""], [":--", "--:", ":--"], rows),
        "",
    ]


def _recent(name: str, questions: Sequence[Question], today: date) -> list[str]:
    """What has been reported at this employer in the current window."""
    dated = _dated(questions, today)
    window = _in_window(questions, today, WINDOW_DAYS)
    heading = f"## Asked here in the last {WINDOW_DAYS} days"
    if not window:
        latest = max((q.reported_date for q in dated), default=None)
        return [
            heading,
            "",
            (
                f"**Nothing has been reported at {escape_cell(name)} since {date_label(latest)}.** "
                if latest
                else f"**No sighting has ever been dated at {escape_cell(name)}.** "
            )
            + "That is a statement about what candidates have reported, not about whether this company is "
            "interviewing. [Report one](../../../issues/new?template=question-report.yml) and it appears here "
            "within the hour.",
            "",
        ]
    ordered = sorted(window, key=lambda q: (-q.reported_date.toordinal(), q.slug))[:RECENT_ROWS]
    rows = [
        [
            question_link(question),
            format_label(question.type),
            difficulty_label(question.difficulty) or "—",
            ", ".join(ROUND_LABELS.get(r, r) for r in question.rounds) or "—",
            date_label(question.reported_date, month_only=question.reported_is_month_only),
        ]
        for question in ordered
    ]
    more = len(window) - len(ordered)
    return [
        heading,
        "",
        f"**{plural(len(window), 'sighting')}** recorded between "
        f"{date_label(today - timedelta(days=WINDOW_DAYS))} and {date_label(today)}. Newest first.",
        "",
        table(
            ["Question", "Format", "Difficulty", "Round", "Reported"],
            [":--", ":--", ":-:", ":--", ":--"],
            rows,
        ),
        "",
        *([f"<sub>{more:,} more in this window are in the table below.</sub>", ""] if more > 0 else []),
    ]


def _start_here(name: str, questions: Sequence[Question], today: date) -> list[str]:
    """A short ordered list, with the rule that ordered it printed above it.

    The rule is deliberately dull and checkable — recency, then how many other
    employers ask the same question — because the alternative is a ranking
    nobody can audit, on a page whose whole argument is that it counts rather
    than claims.
    """
    if not questions:
        return []
    ranked = sorted(
        questions,
        key=lambda q: (
            -(q.reported_date.toordinal() if q.reported_date and q.reported_date <= today else 0),
            -len(q.companies),
            {"easy": 0, "medium": 1, "hard": 2}.get(q.difficulty or "", 3),
            q.slug,
        ),
    )[:START_HERE_ROWS]
    rows = [
        [
            f"**{index}**",
            question_link(question) + (" 🆓" if question.access_tier == "free" else ""),
            format_label(question.type),
            difficulty_label(question.difficulty) or "—",
            # The company whose page this is counts for nothing here: "also
            # asked at 3" has to mean three OTHER employers, or the column is
            # wrong by one on every row of every page.
            f"{len(question.companies) - 1}" if len(question.companies) > 1 else "—",
            date_label(question.reported_date, month_only=question.reported_is_month_only),
        ]
        for index, question in enumerate(ranked, start=1)
    ]
    return [
        "## Start here",
        "",
        f"The {len(ranked)} questions to open first if you are preparing for {escape_cell(name)}, ranked by "
        "**the most recently reported, then the ones the most other companies also ask**. Both are facts "
        "about the bank rather than an opinion of ours, and an undated question sorts last rather than being "
        "guessed at a date. 🆓 opens without a paid plan.",
        "",
        table(
            ["#", "Question", "Format", "Difficulty", "Also asked at", "Reported"],
            ["--:", ":--", ":--", ":-:", "--:", ":--"],
            rows,
        ),
        "",
        "<sub>*Also asked at* counts the other employers this same question is reported at — `—` means this "
        "one is only recorded here.</sub>",
        "",
    ]


def _guides_section(name: str, guides: Sequence[Guide], api_labels: dict[str, str]) -> list[str]:
    if not guides:
        return []
    return [
        "## Round-by-round guides",
        "",
        f"**{len(guides)} {'writeup' if len(guides) == 1 else 'writeups'}** on what each stage of the "
        f"{escape_cell(name)} loop actually is — the "
        "recruiter screen, the hiring-manager round, the culture interview, the project deep-dive. Read the "
        "one for the round you have next.",
        "",
        guide_rows(guides, api_labels, with_company=False),
        "",
    ]


def _reports(name: str, experiences: Sequence[Experience], total: int | None, today: date) -> list[str]:
    if not experiences:
        return []
    ordered = sorted(
        experiences,
        key=lambda e: (-(e.posted_date.toordinal() if e.posted_date else 0), e.title.casefold(), e.id),
    )
    rows = [
        [
            escape_cell(row.role) if row.role else "—",
            f"[{escape_cell(row.title)}]({row.url})",
            date_label(row.posted_date),
        ]
        for row in ordered
    ]
    return [
        "## Interview reports",
        "",
        f"What candidates said happened in the room at {escape_cell(name)} — written up by the people who sat "
        "the loop. The freshest thing this page points at: a question enters the bank when somebody curates "
        "it, a report lands the week the interview happened.",
        "",
        table(["Role", "Report", "Posted"], [":--", ":--", ":--"], rows),
        "",
        "<sub>These are the reports that were in the newest slice the catalog API hands over"
        + (f" (it holds {total:,} in total)" if total else "")
        + f". [Every report at {escape_cell(name)} and everywhere else →]({SITE}/interviews)</sub>",
        "",
    ]


# ── company-type pages ───────────────────────────────────────────────────────
#
# The same machinery pointed at a GROUP of employers, because "I am preparing
# for quant" is a question the bank could not answer: it knew what Citadel asks
# and what Optiver asks and had no way to say what the nine of them ask.


def _cut_companies(question: Question, company_keys: frozenset[str], api_labels: dict[str, str]) -> str:
    """The employers on this row that are IN this cut, then the rest.

    `q.companies[:3]` was the question's whole list, unordered with respect to
    the cut, so a row on the quant page could name Microsoft, Amazon and Apple
    and none of the quant firms that put it there — on a page whose entire
    promise is what THIS kind of company asks.
    """
    inside = [c for c in question.companies if company_key(c, api_labels) in company_keys]
    outside = [c for c in question.companies if company_key(c, api_labels) not in company_keys]
    named = inside[:3]
    cell = ", ".join(escape_cell(company_label(c, api_labels)) for c in named)
    hidden = len(inside) - len(named) + len(outside)
    return (cell or "—") + (" …" if hidden else "")


def company_type_preamble(
    *,
    title: str,
    note: str,
    companies: Sequence[tuple[str, str, int]],
    questions: Sequence[Question],
    api_labels: dict[str, str],
    today: date,
) -> str:
    """Everything above the question table on a company-type page."""
    company_keys = frozenset(key for key, _, _ in companies)
    dated = _dated(questions, today)
    window = _in_window(questions, today, WINDOW_DAYS)
    formats = _counter(q.type for q in questions)
    largest = max(formats.values()) if formats else 0
    free = sum(1 for q in questions if q.access_tier == "free")

    body = [
        f"> {note}",
        "",
        "## The companies in this cut",
        "",
        " · ".join(f"[{escape_cell(name)} ({count:,})](../companies/{key}.md)" for key, name, count in companies),
        "",
        f"<sub>{plural(len(companies), 'employer')}. A question reported at two of them is counted once "
        "here and appears on both of their pages.</sub>",
        "",
        "## What this cut asks",
        "",
        table(
            ["Format", "Questions", "Share", "", "Free"],
            [":--", "--:", "--:", ":--", "--:"],
            [
                [
                    f"[{format_label(key)}](../formats/{key}.md)",
                    f"{formats[key]:,}",
                    percent(formats[key], len(questions)),
                    bar(formats[key], largest, width=14),
                    f"{sum(1 for q in questions if q.type == key and q.access_tier == 'free'):,}",
                ]
                for key in sorted(formats, key=lambda k: (-formats[k], k))
            ],
        ),
        "",
        f"<sub>Difficulty across the cut (easy / medium / hard): **{_difficulty_cell(questions)}**, over the "
        f"rows the catalog has graded. {free:,} of the {len(questions):,} open without a paid plan.</sub>",
        "",
    ]

    labelled = [q for q in questions if q.topics]
    if labelled:
        by_topic = _counter(topic for q in labelled for topic in q.topics)
        ranked = sorted(by_topic.items(), key=lambda item: (-item[1], item[0]))[:TOPIC_ROWS]
        top = ranked[0][1]
        body += [
            "## What they ask about",
            "",
            f"Of the **{plural(len(labelled), 'question')} in this cut that "
            f"{'carries' if len(labelled) == 1 else 'carry'} a topic label** "
            f"({percent(len(labelled), len(questions))} of it):",
            "",
            table(
                ["Topic", "Questions", "Share of labelled", ""],
                [":--", "--:", "--:", ":--"],
                [
                    [f"`{escape_cell(topic)}`", f"{count:,}", percent(count, len(labelled)), bar(count, top, width=12)]
                    for topic, count in ranked
                ],
            ),
            "",
            # The same caveat the company page carries. Without it the column
            # visibly sums past 100% and the page looks like it cannot add up,
            # when what is actually true is that a question has two topics.
            "<sub>A question can carry more than one topic, so this column sums to more than the number "
            "of labelled questions. [Every topic across the whole bank →](../insights/topics.md)</sub>",
            "",
        ]

    if window:
        recent = sorted(window, key=lambda q: (-q.reported_date.toordinal(), q.slug))[:RECENT_ROWS]
        more = len(window) - len(recent)
        body += [
            f"## Asked here in the last {WINDOW_DAYS} days",
            "",
            f"**{plural(len(window), 'sighting')}** across this cut. Newest first.",
            "",
            table(
                ["Question", "In this cut", "Format", "Reported"],
                [":--", ":--", ":--", ":--"],
                [
                    [
                        question_link(q),
                        _cut_companies(q, company_keys, api_labels),
                        format_label(q.type),
                        date_label(q.reported_date, month_only=q.reported_is_month_only),
                    ]
                    for q in recent
                ],
            ),
            "",
            # The same note the company page carries under the same table. A
            # sentence promising 115 sightings over a table of 12, with nothing
            # saying so, is a page a reader can count and catch out.
            *([f"<sub>{more:,} more in this window are in the table below.</sub>", ""] if more > 0 else []),
        ]
    elif dated:
        body += [
            f"## Asked here in the last {WINDOW_DAYS} days",
            "",
            "**Nothing in this cut has been reported in the window.** The most recent sighting across these "
            f"employers is {date_label(max(q.reported_date for q in dated))}. That is a statement about what "
            "candidates have reported, not about whether these companies are interviewing.",
            "",
        ]

    body += [
        "---",
        "",
        f"**Preparing for one of these?** Open a company page above for its own loop, its rounds and its "
        f"reports. Everything in the table below is practisable on [TrueInterview]({SITE}/problems) with a "
        "runnable workspace and a server-judged verdict.",
        "",
        f"## Every question reported across {escape_cell(title)}",
    ]
    return "\n".join(body)


def _footer(name: str, key: str, sector_label: str | None) -> list[str]:
    jobs = " · ".join(f"[{label}]({url})" for label, url in JOB_LISTS)
    # The sector is NAMED rather than linked. Both sibling repositories carry a
    # page per sector, but a cut with too little behind it does not get one —
    # and a cross-repository link cannot be checked by this repository's own
    # link test, so the one that could 404 is the one not worth having.
    where = (
        f"Both are filtered by the same company types this page is labelled with, so "
        f"*{escape_cell(sector_label)}* is one click in from their filter hub."
        if sector_label
        else "Both are filtered by company type, role and metro."
    )
    return [
        "---",
        "",
        f"**Practise these on TrueInterview.** Every title above opens the full problem in a runnable "
        f"workspace with a server-judged verdict: [{escape_cell(name)} on TrueInterview]({SITE}/problems/company/{key}).",
        "",
        f"**Hiring right now?** Open roles are in the sibling lists, refreshed hourly: {jobs}. {where}",
        "",
    ]


def company_preamble(
    *,
    name: str,
    key: str,
    questions: Sequence[Question],
    guides: Sequence[Guide],
    experiences: Sequence[Experience],
    experiences_total: int | None,
    api_labels: dict[str, str],
    today: date,
) -> str:
    """Everything above the question table on one company's page."""
    sector, size = segment_of(name, api_labels)
    chip = sector_chip(sector, size)

    blocks: list[tuple[str, list[str]]] = [
        ("At a glance", _glance(name, questions, guides, experiences, today)),
        ("The loop, as reported", _loop(name, questions)),
        (f"Asked here in the last {WINDOW_DAYS} days", _recent(name, questions, today)),
        ("What they ask about", _topics(name, questions, today)),
        ("When they asked it", _timeline(name, questions, today)),
        ("Start here", _start_here(name, questions, today)),
        ("Round-by-round guides", _guides_section(name, guides, api_labels)),
        ("Interview reports", _reports(name, experiences, experiences_total, today)),
    ]
    present = [(title, lines) for title, lines in blocks if lines]

    body: list[str] = []
    if chip:
        body += [f"> {chip}", ""]
    if len(present) > 2:
        jump = " · ".join(f"[{title}](#{anchor(title)})" for title, _ in present)
        body += [
            f"**On this page:** {jump} · "
            f"[Every question](#{anchor(f'Every question reported at {name}')})",
            "",
        ]
    for _, lines in present:
        body += lines
    body += _footer(name, key, SECTOR_BY_ID[sector].label if sector else None)
    body += [f"## Every question reported at {escape_cell(name)}"]
    return "\n".join(body)
