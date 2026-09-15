#!/usr/bin/env python3
"""Turn the catalog into the statistics this repository publishes.

── Why this file exists ─────────────────────────────────────────────────────

Every other module here is an *index*: it takes the bank and gives a reader
three ways to walk it. None of that answers the question a candidate actually
arrives with, which is not "list me two thousand questions" but **"what is
being asked, where, right now, and what should I do first?"**

That answer is not in any single row. It is in the shape of the whole set —
which formats a company leans on, which topics recur across employers, which
question turns up at sixteen different loops, what moved this quarter. A
repository that carries the rows and not the shape is a directory; one that
carries both is worth opening on its own.

So this module is the only place in the pipeline that *aggregates*, and it is
pure: records and a date in, plain dataclasses out. `render.py` decides how a
number looks, `build.py` decides which page it lands on, and nothing here
touches the network, the clock or the filesystem.

── The rules every statistic here holds ─────────────────────────────────────

  * **A share names its denominator, and never invents one.** Only 45% of the
    bank carries a topic label and 58% carries a sighting date, so "hashing is
    18% of questions" is false and "18% of the 1,015 questions that carry a
    topic" is true. :class:`Share` carries both numbers for that reason, and a
    share of nothing is ``None`` rather than ``0%``.
  * **Unmeasured is not zero.** A company whose questions carry no sighting
    date at all has an unknown recent-activity count, not a zero one, and the
    two must not print the same way — a real 0 says "quiet this quarter" and an
    unknown says "we never knew". :class:`CompanyRow.dated` is what tells them
    apart downstream.
  * **An empty window is stated, never tabulated.** A quarter with no recorded
    sightings is a normal thing for a catalog whose newest sighting is six
    weeks old; printing a table of zeros for it reads as a broken site.
  * **Ordering is total.** Every ranking breaks its final tie on a slug or a
    key, so two equal rows cannot swap places between hourly runs and turn a
    quiet hour into a committed diff.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Sequence

from catalog import Guide, Question
from labels import FORMAT_ORDER, company_key, company_label, format_label

#: The primary window. A hiring quarter is the unit a candidate plans in, and
#: it is also long enough that one slow fortnight upstream does not empty it —
#: a 30-day window on this catalog is legitimately zero for weeks at a time,
#: and a section that reads "0" for a month is indistinguishable from a broken
#: pipeline.
WINDOW_DAYS = 90

#: The secondary window, for the "is this employer still hiring at all" cut.
YEAR_DAYS = 365

#: How many rows each ranking prints. Long enough to be a study list, short
#: enough that the page stays a page.
TOP_QUESTIONS = 30
TOP_COMPANIES = 25
TOP_TOPICS = 12
TOPIC_EXAMPLES = 3
MONTHS_SHOWN = 18

DIFFICULTIES: tuple[str, ...] = ("easy", "medium", "hard")

#: Interview rounds, in loop order rather than by count — the order a candidate
#: meets them in is the order the table should read in, and a count ranking
#: would reshuffle the rows between syncs.
ROUND_ORDER: tuple[str, ...] = ("oa", "phone-screen", "onsite", "take-home")
ROUND_LABELS: dict[str, str] = {
    "oa": "Online assessment",
    "phone-screen": "Phone screen",
    "onsite": "Onsite / virtual onsite",
    "take-home": "Take-home",
}

#: Access tiers, cheapest first, with what each one means to a reader.
TIER_ORDER: tuple[str, ...] = ("free", "pro", "insider")
TIER_LABELS: dict[str, str] = {"free": "Free", "pro": "Pro", "insider": "Insider"}


@dataclass(frozen=True)
class Share:
    """A count, and the population it is a share of.

    Both numbers travel together because one without the other is a claim this
    repository cannot support: the bank labels 45% of its rows with a topic, so
    every topic share is a share of the LABELLED rows and saying so is the
    difference between a statistic and a lie of omission.
    """

    count: int
    of: int

    @property
    def share(self) -> float | None:
        """The fraction, or ``None`` when there is nothing to be a share of."""
        return (self.count / self.of) if self.of else None


@dataclass(frozen=True)
class FormatRow:
    key: str
    label: str
    total: int
    window: int
    #: easy/medium/hard counts, and how many rows carried a difficulty at all.
    difficulty: dict[str, int]
    difficulty_known: int
    free: int


@dataclass(frozen=True)
class TopicRow:
    topic: str
    total: int
    difficulty: dict[str, int]
    difficulty_known: int
    window: int
    companies: tuple[tuple[str, str, int], ...]  # (key, label, count)
    examples: tuple[Question, ...]


@dataclass(frozen=True)
class CompanyRow:
    key: str
    name: str
    questions: int
    guides: int
    #: How many of this company's questions carry a sighting date at all. Zero
    #: means `window` and `last_seen` are UNKNOWN rather than empty, and the
    #: renderer must print them as such.
    dated: int
    window: int
    year: int
    last_seen: date | None
    top_format: str | None
    top_topic: str | None
    free: int


@dataclass(frozen=True)
class MonthRow:
    key: str
    total: int
    formats: dict[str, int]
    companies: int
    #: Companies whose FIRST recorded sighting falls in this month. A new
    #: employer showing up is the most interesting thing a month can carry.
    new_companies: tuple[str, ...]


@dataclass(frozen=True)
class Insights:
    """Everything the statistics pages print, computed once."""

    today: date
    total: int
    #: Rows carrying a sighting date of any kind, INCLUDING one dated in the
    #: future. `dated + undated == total` holds, which is what lets the page
    #: print both numbers in one sentence without them failing to add up.
    dated: int
    undated: int
    #: Rows whose sighting is dated after `today` — a data-entry error upstream.
    #: Counted so the page can say so; excluded from every window and from the
    #: earliest/latest pair, because an error is not the most recent thing that
    #: happened.
    future_dated: int
    latest_sighting: date | None
    earliest_sighting: date | None
    window_days: int
    window_total: int
    year_total: int
    formats: tuple[FormatRow, ...]
    window_formats: tuple[tuple[str, int], ...]
    difficulty: dict[str, int]
    difficulty_known: int
    rounds: tuple[tuple[str, int], ...]
    rounds_known: int
    tiers: dict[str, int]
    topics: tuple[TopicRow, ...]
    topics_known: int
    breadth: tuple[Question, ...]
    companies: tuple[CompanyRow, ...]
    window_companies: tuple[tuple[str, str, int], ...]
    months: tuple[MonthRow, ...]
    free_total: int
    guides_total: int
    guide_topics: tuple[tuple[str, int], ...]


def _in_window(question: Question, today: date, days: int) -> bool:
    """Was this question reported inside the last ``days``?

    A sighting dated in the FUTURE is excluded, exactly as it is excluded from
    the landing page's latest list: it is a data-entry error upstream, and the
    one thing a recency window must never do is rank an error as the most
    recent thing that happened.
    """
    stamp = question.reported_date
    if stamp is None or stamp > today:
        return False
    return (today - stamp).days <= days


def _counter(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _difficulty_mix(questions: Sequence[Question]) -> tuple[dict[str, int], int]:
    """(easy/medium/hard counts, how many rows carried a difficulty).

    The second number is the denominator every difficulty share on every page
    is taken over. A question the catalog has not graded is not an easy one.
    """
    mix = {level: 0 for level in DIFFICULTIES}
    known = 0
    for question in questions:
        if question.difficulty in mix:
            mix[question.difficulty] += 1
            known += 1
    return mix, known


def _top(counts: dict[str, int], limit: int) -> tuple[tuple[str, int], ...]:
    """Biggest first, ties broken on the key so the order is total."""
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit])


def compute(
    questions: Sequence[Question],
    guides: Sequence[Guide],
    api_labels: dict[str, str],
    today: date,
) -> Insights:
    """Every statistic this repository publishes, from one snapshot."""
    window_cut = [q for q in questions if _in_window(q, today, WINDOW_DAYS)]
    year_cut = [q for q in questions if _in_window(q, today, YEAR_DAYS)]

    sightings = [q.reported_date for q in questions if q.reported_date and q.reported_date <= today]

    # ── formats ──────────────────────────────────────────────────────────────
    by_format: dict[str, list[Question]] = {}
    for question in questions:
        by_format.setdefault(question.type, []).append(question)
    window_by_format = _counter(q.type for q in window_cut)

    format_keys = [f for f in FORMAT_ORDER if f in by_format] + sorted(set(by_format) - set(FORMAT_ORDER))
    formats = []
    for key in format_keys:
        rows = by_format[key]
        mix, known = _difficulty_mix(rows)
        formats.append(
            FormatRow(
                key=key,
                label=format_label(key),
                total=len(rows),
                window=window_by_format.get(key, 0),
                difficulty=mix,
                difficulty_known=known,
                free=sum(1 for q in rows if q.access_tier == "free"),
            )
        )

    # ── topics ───────────────────────────────────────────────────────────────
    by_topic: dict[str, list[Question]] = {}
    for question in questions:
        for topic in question.topics:
            by_topic.setdefault(topic, []).append(question)

    topics: list[TopicRow] = []
    for topic, rows in by_topic.items():
        mix, known = _difficulty_mix(rows)
        company_counts = _counter(
            company_key(c, api_labels) for q in rows for c in q.companies if company_key(c, api_labels)
        )
        named = tuple(
            (key, _label_for(key, rows, api_labels), count) for key, count in _top(company_counts, 5)
        )
        # Newest-first examples, so the sample rows on a topic page are the ones
        # someone was asked recently rather than whatever sorts first.
        ordered = sorted(
            rows,
            key=lambda q: (
                -(q.reported_date.toordinal() if q.reported_date and q.reported_date <= today else 0),
                q.slug,
            ),
        )
        topics.append(
            TopicRow(
                topic=topic,
                total=len(rows),
                difficulty=mix,
                difficulty_known=known,
                window=sum(1 for q in rows if _in_window(q, today, WINDOW_DAYS)),
                companies=named,
                examples=tuple(ordered[:TOPIC_EXAMPLES]),
            )
        )
    topics.sort(key=lambda row: (-row.total, row.topic))

    # ── companies ────────────────────────────────────────────────────────────
    guides_by_company: dict[str, int] = {}
    for guide in guides:
        for company in guide.companies:
            key = company_key(company, api_labels)
            if key:
                guides_by_company[key] = guides_by_company.get(key, 0) + 1

    by_company: dict[str, list[Question]] = {}
    names: dict[str, str] = {}
    for question in questions:
        for company in question.companies:
            key = company_key(company, api_labels)
            if not key:
                continue
            by_company.setdefault(key, []).append(question)
            names.setdefault(key, company_label(company, api_labels))

    companies: list[CompanyRow] = []
    for key, rows in by_company.items():
        stamps = [q.reported_date for q in rows if q.reported_date and q.reported_date <= today]
        topic_counts = _counter(t for q in rows for t in q.topics)
        format_counts = _counter(q.type for q in rows)
        companies.append(
            CompanyRow(
                key=key,
                name=names.get(key, key),
                questions=len(rows),
                guides=guides_by_company.get(key, 0),
                dated=len(stamps),
                window=sum(1 for q in rows if _in_window(q, today, WINDOW_DAYS)),
                year=sum(1 for q in rows if _in_window(q, today, YEAR_DAYS)),
                last_seen=max(stamps) if stamps else None,
                top_format=format_label(_top(format_counts, 1)[0][0]) if format_counts else None,
                top_topic=_top(topic_counts, 1)[0][0] if topic_counts else None,
                free=sum(1 for q in rows if q.access_tier == "free"),
            )
        )
    companies.sort(key=lambda row: (-row.questions, row.name.casefold(), row.key))

    window_company_counts = _counter(
        company_key(c, api_labels) for q in window_cut for c in q.companies if company_key(c, api_labels)
    )
    window_companies = tuple(
        (key, names.get(key, key), count) for key, count in _top(window_company_counts, TOP_COMPANIES)
    )

    # ── months ───────────────────────────────────────────────────────────────
    first_seen: dict[str, str] = {}
    for question in questions:
        if question.reported_date is None:
            continue
        month = f"{question.reported_date.year:04d}-{question.reported_date.month:02d}"
        for company in question.companies:
            key = company_key(company, api_labels)
            if not key:
                continue
            previous = first_seen.get(key)
            if previous is None or month < previous:
                first_seen[key] = month

    by_month: dict[str, list[Question]] = {}
    for question in questions:
        if question.reported_date is None:
            continue
        by_month.setdefault(
            f"{question.reported_date.year:04d}-{question.reported_date.month:02d}", []
        ).append(question)

    months: list[MonthRow] = []
    for key in sorted(by_month, reverse=True):
        rows = by_month[key]
        month_companies = {
            company_key(c, api_labels) for q in rows for c in q.companies if company_key(c, api_labels)
        }
        months.append(
            MonthRow(
                key=key,
                total=len(rows),
                formats=_counter(q.type for q in rows),
                companies=len(month_companies),
                new_companies=tuple(
                    sorted(
                        (names.get(c, c) for c in month_companies if first_seen.get(c) == key),
                        key=str.casefold,
                    )
                ),
            )
        )

    # ── the most-asked list ──────────────────────────────────────────────────
    # Ranked by how many DIFFERENT employers a question is reported at, which
    # is the one signal in this catalog that says "this is not one company's
    # quirk". A question at sixteen loops is the closest thing the data has to
    # an instruction about what to practise first, and it is a fact about the
    # bank rather than an opinion of ours.
    breadth = tuple(
        sorted(
            (q for q in questions if len(q.companies) > 1),
            key=lambda q: (-len(q.companies), -(q.reported_date.toordinal() if q.reported_date else 0), q.slug),
        )[:TOP_QUESTIONS]
    )

    guide_topics = _top(_counter(tag for guide in guides for tag in guide.tags), TOP_TOPICS)

    return Insights(
        today=today,
        total=len(questions),
        dated=sum(1 for q in questions if q.reported_date is not None),
        undated=sum(1 for q in questions if q.reported_date is None),
        future_dated=sum(1 for q in questions if q.reported_date and q.reported_date > today),
        latest_sighting=max(sightings) if sightings else None,
        earliest_sighting=min(sightings) if sightings else None,
        window_days=WINDOW_DAYS,
        window_total=len(window_cut),
        year_total=len(year_cut),
        formats=tuple(formats),
        window_formats=tuple(
            (key, window_by_format.get(key, 0)) for key in format_keys if window_by_format.get(key, 0)
        ),
        difficulty=_difficulty_mix(questions)[0],
        difficulty_known=_difficulty_mix(questions)[1],
        rounds=tuple(
            (key, count)
            for key, count in ((r, _counter(x for q in questions for x in q.rounds).get(r, 0)) for r in ROUND_ORDER)
            if count
        ),
        rounds_known=sum(1 for q in questions if q.rounds),
        tiers=_counter(q.access_tier for q in questions),
        topics=tuple(topics),
        topics_known=sum(1 for q in questions if q.topics),
        breadth=breadth,
        companies=tuple(companies),
        window_companies=window_companies,
        months=tuple(months),
        free_total=sum(1 for q in questions if q.access_tier == "free"),
        guides_total=len(guides),
        guide_topics=guide_topics,
    )


def _label_for(key: str, rows: Sequence[Question], api_labels: dict[str, str]) -> str:
    """The display name a company slug came from, taken from the rows in hand."""
    for question in rows:
        for company in question.companies:
            if company_key(company, api_labels) == key:
                return company_label(company, api_labels)
    return key


def window_start(today: date, days: int = WINDOW_DAYS) -> date:
    return today - timedelta(days=days)
