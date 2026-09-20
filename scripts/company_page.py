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
from datetime import date
from typing import Iterable, Sequence

from catalog import Experience, Guide, Question
from insights import DIFFICULTIES, ROUND_LABELS, ROUND_ORDER, WINDOW_DAYS
from labels import company_key, company_label, difficulty_label, format_label
from render import (
    MONTH_NAMES,
    bar,
    date_label,
    escape_cell,
    guide_rows,
    percent,
    plural,
    question_link,
    table,
)
from segments import SECTOR_BY_ID, is_big_tech, sector_chip, segment_of

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

#: The heading AND the "At a glance" row label for the writeups block, spelled
#: once so the jump link, the summary row and the section can never drift. It
#: used to read "Round-by-round guides", which named a content type nothing
#: verified — see `_guides_section`.
GUIDES_HEADING = "Guides & writeups"


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


def _census_note(name: str, questions: Sequence[Question], dated: Sequence[Question]) -> str:
    """The line under the glance table: how many of these rows carry a date.

    Three states, not two. This counted `_dated` — "a sighting ON OR BEFORE
    today" — and called its complement *unmeasured*, which is the UNDATED set.
    A row dated in the future is in neither, so it was silently reported as
    carrying no date at all: the LinkedIn page said "34 of them carry a
    sighting date; the other 21 are unmeasured" while the table below it showed
    35 dates and 20 dashes, one of them reading "Feb 23, 2126". Both numbers
    were wrong, in opposite directions, on the one line whose whole job is to
    let a reader audit the table under it.

    It also contradicted the rest of the repository, which is what settles it:
    `insights/README.md` says of that same row "it is counted as dated and
    excluded from every window", and `by-month/` counts it as dated.

    So the census asks whether there IS a date, the window cells go on asking
    whether it has happened, and when the two disagree the page says so rather
    than picking one.
    """
    on_file = [q for q in questions if q.reported_date is not None]
    future = len(on_file) - len(dated)
    carried = (
        f"{len(on_file):,} of them carry a sighting date"
        if not future
        else f"{len(on_file):,} of them carry a sighting date "
        f"({plural(future, 'of those is', 'of those are')} dated after today, so "
        f"{'it is' if future == 1 else 'they are'} in no window)"
    )
    return (
        f"<sub>Counted from the {plural(len(questions), 'question')} reported at {escape_cell(name)}. "
        f"{carried}; the other {len(questions) - len(on_file):,} are *unmeasured*, which is a different "
        "fact from *old* — they are in every total here and in no window.</sub>"
    )


def _glance(name: str, questions: Sequence[Question], guides: Sequence[Guide],
            guides_complete: bool, experiences: Sequence[Experience], today: date) -> list[str]:
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
        # `17+` when the catalog could only hand over one page of guides. The
        # index says so in a standing notice; a bare count on this page reads as
        # a total, which is the same lie in a smaller font.
        [GUIDES_HEADING, (f"{len(guides):,}" + ("" if guides_complete else "+")) if guides else "0"],
    ]
    if experiences:
        rows.append(["Interview reports on the board", f"{len(experiences):,} in this snapshot"])
    return [
        "## At a glance",
        "",
        table(["", ""], [":--", ":--"], rows),
        "",
        _census_note(name, questions, dated),
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
        # No dates in this sentence. Both bounds moved with the clock, so every
        # company page was rewritten once a day with one line changed and
        # nothing about the company different — and the heading above already
        # says the window, while the Reported column dates every row in it.
        f"**{plural(len(window), 'sighting')}** in this window. Newest first.",
        "",
        table(
            ["Question", "Format", "Difficulty", "Round", "Reported"],
            [":--", ":--", ":-:", ":--", ":--"],
            rows,
        ),
        "",
        *([f"<sub>{more:,} more in this window are in the table below.</sub>", ""] if more > 0 else []),
    ]


def _start_here_rule(name: str, ranked: Sequence[Question], today: date) -> str:
    """The sentence above the table, naming only the keys that ordered THESE rows.

    The rule has two keys — recency, then how many other employers ask the same
    question — and on a thin company neither of them separates anything: every
    row prints `—` in both of the columns the sentence names, and what actually
    decided the order was difficulty and then the slug. Announcing a ranking the
    table visibly contradicts is worse than announcing no ranking, so the claim
    is narrowed to whichever key did work, and dropped entirely when neither
    did. The tie-break in force is always named, because it is what a reader is
    actually looking at.
    """
    dated = any(q.reported_date and q.reported_date <= today for q in ranked)
    shared = any(len(q.companies) > 1 for q in ranked)
    opening = (
        f"The {plural(len(ranked), 'question')} to open first if you are preparing "
        f"for {escape_cell(name)}"
    )
    trailer = "🆓 opens without a paid plan."

    if dated and shared:
        return (
            f"{opening}, ranked by **the most recently reported, then the ones the most other companies also "
            f"ask**. Both are facts about the bank rather than an opinion of ours, and an undated question "
            f"sorts last rather than being guessed at a date. {trailer}"
        )
    if dated:
        return (
            f"{opening}, ranked by **the most recently reported** — a fact about the bank rather than an "
            f"opinion of ours, and an undated question sorts last rather than being guessed at a date. No row "
            f"here is recorded at another employer, so the usual second key separates nothing and the easier "
            f"questions come first instead. {trailer}"
        )
    if shared:
        return (
            f"{opening}, ranked by **the ones the most other companies also ask** — a fact about the bank "
            f"rather than an opinion of ours. No row here carries a sighting date, so recency could not order "
            f"them; after that key the easier questions come first. {trailer}"
        )
    return (
        f"{opening}. **This is not a ranking:** no row here carries a sighting date and none is recorded at "
        f"another employer, so neither of the keys this section normally uses separates them. They are the "
        f"{plural(len(ranked), 'question')} on file, easiest first. {trailer}"
    )


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
        _start_here_rule(name, ranked, today),
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


def _guides_section(name: str, guides: Sequence[Guide], guides_complete: bool,
                    api_labels: dict[str, str]) -> list[str]:
    """The writeups filed under this employer, described by what they ARE.

    The version before this said every one of them covered "the recruiter
    screen, the hiring-manager round, the culture interview, the project
    deep-dive" — a list of round names nothing had checked. Across the bank a
    guide is as often a problem worked end to end or a review of somebody
    else's product, and on a company with a single guide the claim was visibly
    false about the one row printed under it. The *Topics* column is the
    evidence, so the prose points at it instead of speaking for it.
    """
    if not guides:
        return []
    count = f"{len(guides)}{'' if guides_complete else ' or more'}"
    noun = "writeup" if len(guides) == 1 else "writeups"
    this = "it" if len(guides) == 1 else "each one"
    return [
        f"## {GUIDES_HEADING}",
        "",
        f"**{count} {noun}** filed under {escape_cell(name)} in the Study section — how a round runs, a "
        f"problem worked end to end, or notes on the process. The *Topics* column says what {this} covers; "
        "open the one closest to what you have next.",
        "",
        guide_rows(guides, api_labels, with_company=False),
        "",
        *([] if guides_complete else [
            "<sub>The catalog API could only hand over one page of guides on this run, so this is what it "
            f"reached rather than every guide — see [the index](../guides/README.md) and "
            f"[the Study section]({SITE}/study).</sub>",
            "",
        ]),
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
        f"reports. Everything in the table below opens in a runnable workspace on "
        f"[TrueInterview]({SITE}/problems) — judged server-side on the algorithm, low-level-design and SQL "
        "formats.",
        "",
        f"## Every question reported across {escape_cell(title)}",
    ]
    return "\n".join(body)


def _before_the_table(name: str, key: str, sector_label: str | None) -> list[str]:
    """The block between the summary sections and the full question table.

    NOT a footer, though it was called one and read like one: it is emitted
    just before `## Every question reported at <Name>`, so all 99 company pages
    put a horizontal rule and a sign-off directly above their largest section —
    a reader hit what looked like the end of the document and then found
    another 60 to 230 rows. "Every title **above**" was false there too: on the
    Google page 20 of the 161 question links are above this block and 141 are
    below it, and the thing immediately above it is the month histogram, whose
    "titles" are months.

    So it points FORWARD, which is what the company-type page beside it has
    always done ("Everything in the table below opens in a runnable
    workspace"). That makes it a lead-in to the table rather than a closing
    note in front of one, and costs no change to how the page is assembled.
    """
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
        f"**Practise these on TrueInterview.** Every title on this page — including every row of the "
        f"table below — opens the full problem in a runnable workspace, judged server-side on the "
        f"algorithm, low-level-design and SQL formats: "
        f"[{escape_cell(name)} on TrueInterview]({SITE}/problems/company/{key}).",
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
    guides_complete: bool,
    experiences: Sequence[Experience],
    experiences_total: int | None,
    api_labels: dict[str, str],
    cut_ids: frozenset[str] = frozenset(),
    today: date,
) -> str:
    """Everything above the question table on one company's page."""
    sector, size = segment_of(name, api_labels)
    # The chip is the one line on this page that is not a count, so it carries
    # its rule and links at the cut that states it in full. A bare
    # "🛒 E-commerce & marketplaces · 10,000+ people · Big Tech" is an assertion
    # printed under a docstring promising the page makes none.
    chip = sector_chip(sector, size)
    if chip and sector and sector in cut_ids:
        chip = chip.replace(
            SECTOR_BY_ID[sector].label,
            f"[{SECTOR_BY_ID[sector].label}](../company-types/{sector}.md)",
            1,
        )
    if chip and is_big_tech(sector, size) and "big-tech" in cut_ids:
        chip = chip.replace("Big Tech", "[Big Tech](../company-types/big-tech.md)", 1)
    if chip and is_big_tech(sector, size):
        chip += " — a derived cut: a technology-sector employer with 10,000+ people"

    blocks: list[tuple[str, list[str]]] = [
        ("At a glance", _glance(name, questions, guides, guides_complete, experiences, today)),
        ("The loop, as reported", _loop(name, questions)),
        (f"Asked here in the last {WINDOW_DAYS} days", _recent(name, questions, today)),
        ("What they ask about", _topics(name, questions, today)),
        ("When they asked it", _timeline(name, questions, today)),
        ("Start here", _start_here(name, questions, today)),
        (GUIDES_HEADING, _guides_section(name, guides, guides_complete, api_labels)),
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
    body += _before_the_table(name, key, SECTOR_BY_ID[sector].label if sector else None)
    body += [f"## Every question reported at {escape_cell(name)}"]
    return "\n".join(body)
