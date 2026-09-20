#!/usr/bin/env python3
"""Assemble every generated file from one catalog snapshot.

Pure: takes records and a date, returns a path → contents map. Nothing here
touches the network or the filesystem, which is what lets the test suite render
the whole repository from a fixture and compare bytes.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import report
from catalog import Catalog, Experience, Guide, Question, parse_catalog_date
from company_page import company_preamble, company_type_preamble
from segments import SECTORS, SECTOR_BY_ID, SIZE_LABELS, is_big_tech, segment_of
from insights import compute as compute_insights
from labels import FORMAT_ORDER, JUDGED_FORMATS, company_key, company_label, format_label
from render import (
    GENERATED_NOTICE,
    date_label,
    plural,
    table,
    guide_rows,
    sort_guides,
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
    render_guides_csv,
    render_jsonl,
    render_shard,
    render_table,
    sort_questions,
    _month_label,
)

SITE = "https://trueinterview.io"

# Guides shown in the "recently published" block above the company sections.
# Short on purpose: it is a what-is-new block, and a long one is just the index
# again in a different order.
RECENT_GUIDES = 12

# Interview reports named on the landing page. Enough to show the board is
# moving, short enough that it is a teaser rather than a second index.
README_EXPERIENCE_ROWS = 8


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


def group_experiences_by_company(experiences: Sequence[Experience], api_labels: dict[str, str]):
    """Company slug → the interview reports filed under it.

    A SLICE of the board by construction — the catalog endpoint takes a limit
    and no offset — so most companies have none and the page says so rather
    than implying the board is empty.
    """
    groups: dict[str, list[Experience]] = {}
    for experience in experiences:
        key = company_key(experience.company, api_labels)
        if key:
            groups.setdefault(key, []).append(experience)
    return groups


def group_guides_by_company(guides: Sequence[Guide], api_labels: dict[str, str]):
    """Company slug → its interview-process guides.

    Same multi-company rule as questions: a guide tagged at two employers is
    filed under both, because "how does Robinhood run its recruiter screen" is
    the question being asked and the answer is the same document either way.
    """
    groups: dict[str, list[Guide]] = {}
    for guide in guides:
        for company in guide.companies:
            key = company_key(company, api_labels)
            if key:
                groups.setdefault(key, []).append(guide)
    return groups


#: A cut needs at least this many questions to be worth a page of its own.
#: Below it the page is a heading, a caveat and four rows, and every link to it
#: breaks the week a company is reclassified.
COMPANY_TYPE_MIN_QUESTIONS = 5

#: The size cuts, derived from the sector and the headcount band rather than
#: declared. Same rule and same words as the job-list repositories use.
SIZE_CUTS = (
    ("big-tech", "🏛️", "Big Tech", ("mega",),
     "A technology-sector employer with 10,000+ people."),
    ("large-tech", "🏗️", "Large tech (1,000–9,999)", ("large",),
     "A technology-sector employer with between 1,000 and 9,999 people."),
    ("mid-size-tech", "🏤", "Mid-sized tech (200–999)", ("mid",),
     "A technology-sector employer with between 200 and 999 people."),
    ("startups", "🌱", "Startups (under 200)", ("startup",),
     "A technology-sector employer with fewer than 200 people."),
)


def company_type_cuts(by_company, api_labels: dict[str, str]):
    """Every company-type cut with enough behind it to be a page.

    A cut is a set of EMPLOYERS, and its questions are the union of theirs —
    deduplicated, because a question reported at Google and at Meta is one
    question to somebody preparing for Big Tech, and counting it twice would
    make every share on the page wrong in the same direction.
    """
    segments = {key: segment_of(name, api_labels) for key, (name, _) in by_company.items()}

    def cut(identifier, emoji, title, note, keys):
        companies = sorted(
            ((key, by_company[key][0], len(by_company[key][1])) for key in keys),
            key=lambda row: (-row[2], row[1].casefold()),
        )
        seen: set[str] = set()
        questions = []
        for key, _, _ in companies:
            for question in by_company[key][1]:
                if question.slug in seen:
                    continue
                seen.add(question.slug)
                questions.append(question)
        return {
            "id": identifier,
            "emoji": emoji,
            "title": title,
            "note": note,
            "companies": companies,
            "questions": questions,
        }

    cuts = []
    for identifier, emoji, title, sizes, rule in SIZE_CUTS:
        keys = [
            key
            for key, (sector, size) in segments.items()
            if sector and SECTOR_BY_ID[sector].tech and size in sizes
        ]
        if keys:
            cuts.append(cut(identifier, emoji, title, f"{rule} A derived cut, not a hand-picked list — "
                            "an employer the company registry does not cover is in no size cut at all.", keys))
    for sector in SECTORS:
        keys = [key for key, (found, _) in segments.items() if found == sector.id]
        if keys:
            cuts.append(
                cut(
                    sector.id,
                    sector.emoji,
                    sector.label,
                    f"Every employer the company registry files under {sector.label}, at any size. "
                    "The sector is a fact about the company recorded once, never inferred from a question.",
                    keys,
                )
            )
    return [c for c in cuts if len(c["questions"]) >= COMPANY_TYPE_MIN_QUESTIONS]


def _companies_index(stats_view, api_labels: dict[str, str]) -> str:
    """`companies/README.md`: every employer, and the sector each one is in.

    Two ways in, because there are two questions. "Where is Stripe" is answered
    by the table; "which of these ninety-nine are the quant firms" is answered
    by the sector blocks above it, and it was not answerable at all before —
    which is the question somebody preparing for a KIND of loop actually has.

    The sector comes from a hand-written registry and covers what it covers, so
    the page prints the coverage rather than implying the taxonomy is complete.
    An employer it does not know is under *Not classified*, in the table like
    everybody else, and no worse off than it was.
    """
    rows = list(stats_view.companies)
    by_sector: dict[str, list] = {}
    unclassified: list = []
    big_tech: list = []
    for row in rows:
        sector, size = segment_of(row.name, api_labels)
        if sector is None:
            unclassified.append(row)
        else:
            by_sector.setdefault(sector, []).append(row)
        if is_big_tech(sector, size):
            big_tech.append(row)

    def link(row) -> str:
        return f"[{escape_cell(row.name)} ({row.questions:,})]({row.key}.md)"

    body = [
        GENERATED_NOTICE,
        "",
        "# Companies",
        "",
        f"**{len(rows)} companies**, busiest first. Counts are questions *reported at* that company, so a "
        "question reported at more than one employer is counted under each — the column therefore sums to "
        "more than the size of the bank.",
        "",
        "[← Question bank](../README.md) · [What companies are asking](../insights/README.md) · "
        "[How each company interviews](../guides/README.md)",
        "",
        "## Browse by company type",
        "",
        f"The sector and size of an employer are facts about the company rather than about a question, so "
        f"they come from a hand-written registry, which covers **{len(rows) - len(unclassified)} of "
        f"{len(rows)}** of the companies here. An employer it does not cover is under *Not classified* "
        "below and in the table like everybody else — guessing a sector from a company's name is how a "
        "reader preparing for one kind of loop ends up with the wrong shortlist.",
        "",
    ]
    if big_tech:
        body += [
            f"🏛️ **Big Tech** ({len(big_tech)}) — "
            + " · ".join(link(row) for row in big_tech),
            "",
            "<sub>A derived cut rather than a list of opinions: a technology-sector employer with 10,000+ "
            "people. Every company in it also appears under its own sector below.</sub>",
            "",
        ]
    for sector in SECTORS:
        matches = by_sector.get(sector.id)
        if not matches:
            continue
        body += [
            f"{sector.emoji} **{escape_cell(sector.label)}** ({len(matches)}) — "
            + " · ".join(link(row) for row in matches),
            "",
        ]
    if unclassified:
        body += [
            f"❔ **Not classified** ({len(unclassified)}) — " + " · ".join(link(row) for row in unclassified),
            "",
            "<sub>The registry has no entry for these. [Adding one](../CONTRIBUTING.md#adding-a-company-to-the-registry) "
            "is a two-line change.</sub>",
            "",
        ]

    body += [
        "## Every company",
        "",
        table(
            ["Company", "Type", "Questions", "Guides", f"Last {stats_view.window_days}d", "Last reported", "Free"],
            [":--", ":--", "--:", "--:", "--:", ":--", "--:"],
            [
                [
                    f"[{escape_cell(row.name)}]({row.key}.md)",
                    escape_cell(_type_cell(row.name, api_labels)),
                    f"{row.questions:,}",
                    f"{row.guides:,}" if row.guides else "—",
                    # A company with no dated sighting has an UNKNOWN recent
                    # count, not a zero one. Printing 0 says we looked and found
                    # nothing, which is a different claim.
                    f"{row.window:,}" if row.dated else "—",
                    date_label(row.last_seen) if row.last_seen else "—",
                    f"{row.free:,}" if row.free else "—",
                ]
                for row in rows
            ],
        ),
        "",
        f"<sub>*Last {stats_view.window_days}d* and *Last reported* are `—` where none of that company's "
        "questions carries a sighting date at all: unmeasured, which is not the same as quiet.</sub>",
        "",
    ]
    return "\n".join(body)


def _type_cell(name: str, api_labels: dict[str, str]) -> str:
    sector, size = segment_of(name, api_labels)
    if sector is None and size is None:
        return "—"
    parts = [SECTOR_BY_ID[sector].label] if sector else []
    if size:
        parts.append(SIZE_LABELS[size])
    return " · ".join(parts)


def build(catalog: Catalog, readme_template: str, today: date) -> RenderResult:
    questions, guides, api_labels = catalog.questions, catalog.guides, catalog.labels
    ordered = sort_questions(questions, today)
    guides_by_company = group_guides_by_company(guides, api_labels)
    by_company = group_by_company(ordered, api_labels)
    by_format = group_by_format(ordered)
    by_month = group_by_month(ordered)

    company_rows = sorted(
        ((key, name, len(rows)) for key, (name, rows) in by_company.items()),
        key=lambda row: (-row[2], row[1].casefold()),
    )
    month_keys = sorted(by_month, reverse=True)

    files: dict[str, str] = {}

    # Computed here rather than further down because the company INDEX reads it:
    # the per-company recency and coverage numbers on that page are the same
    # ones the insights pages print, and computing them twice is how two pages
    # end up disagreeing about one company.
    stats_view = compute_insights(ordered, guides, api_labels, today)

    # ── company pages ────────────────────────────────────────────────────────
    # The interview PROCESS goes above the questions, because it is the thing
    # you need first: which rounds this company runs, what each one is for,
    # what has actually been reported this quarter, and which five questions to
    # open tonight. A table of two hundred questions is what you use once you
    # know that — see `company_page.py` for the rules it holds.
    experiences_by_company = group_experiences_by_company(catalog.experiences, api_labels)
    # Computed before the pages that LINK at it: a company page's sector chip
    # points at the cut that states its rule, and a cut with too little behind
    # it has no page to point at.
    cuts = company_type_cuts(by_company, api_labels)
    cut_ids = frozenset(cut["id"] for cut in cuts)
    for key, (name, rows) in by_company.items():
        company_guides = guides_by_company.get(key, [])
        company_experiences = experiences_by_company.get(key, [])
        files.update(
            render_shard(
                base=f"companies/{key}",
                title=f"{name} interview process, OA & interview questions",
                lede=(
                    f"**{plural(len(rows), 'question')}** reported at {escape_cell(name)}"
                    + (
                        f" · **{len(company_guides)} interview "
                        f"{'guide' if len(company_guides) == 1 else 'guides'}**"
                        if company_guides
                        else ""
                    )
                    + (
                        f" · **{len(company_experiences)} interview "
                        f"{'report' if len(company_experiences) == 1 else 'reports'}**"
                        if company_experiences
                        else ""
                    )
                    + f". How the loop runs, what has been asked lately, and where to start — counted from "
                    f"what candidates reported, never asserted. Every title opens the full problem in a "
                    f"runnable workspace on [TrueInterview]({SITE}/problems/company/{key})"
                    + (
                        ", judged server-side."
                        if all(q.type in JUDGED_FORMATS for q in rows)
                        else ", judged server-side on the algorithm, low-level-design and SQL formats."
                    )
                ),
                back="[← All companies](README.md) · [← Question bank](../README.md)",
                questions=rows,
                cols=columns_for("company", api_labels, today),
                today=today,
                preamble=company_preamble(
                    name=name,
                    key=key,
                    questions=rows,
                    guides=company_guides,
                    guides_complete=catalog.guides_complete,
                    experiences=company_experiences,
                    cut_ids=cut_ids,
                    experiences_total=catalog.experiences_total,
                    api_labels=api_labels,
                    today=today,
                ),
            )
        )

    files["companies/README.md"] = _companies_index(stats_view, api_labels)

    # ── company-type pages ───────────────────────────────────────────────────
    # "What do quant firms ask" was not a question this repository could answer:
    # it knew what Citadel asks and what Optiver asks, and had no way to put the
    # nine of them together. A candidate prepares for a KIND of loop.
    cuts = company_type_cuts(by_company, api_labels)
    for cut in cuts:
        files.update(
            render_shard(
                base=f"company-types/{cut['id']}",
                title=f"{cut['emoji']} {cut['title']} — interview & OA questions",
                lede=(
                    f"**{plural(len(cut['questions']), 'question')}** reported across the "
                    f"**{len(cut['companies'])} {escape_cell(cut['title'])} "
                    f"{'employer' if len(cut['companies']) == 1 else 'employers'}** in this bank. "
                    "What this kind of company asks, counted from what candidates reported."
                ),
                back="[← All company types](README.md) · [← All companies](../companies/README.md) · "
                "[← Question bank](../README.md)",
                questions=cut["questions"],
                cols=columns_for("format", api_labels, today),
                today=today,
                preamble=company_type_preamble(
                    title=cut["title"],
                    note=cut["note"],
                    companies=cut["companies"],
                    questions=cut["questions"],
                    api_labels=api_labels,
                    today=today,
                ),
            )
        )
    # Written even when there are no cuts, because the README's hand-written
    # nav links at it unconditionally — and an empty registry is a reachable
    # state (it is what a fresh checkout has before the registry lands). An
    # empty section is a sentence here, the way it is everywhere else.
    if True:
        files["company-types/README.md"] = "\n".join(
            [
                GENERATED_NOTICE,
                "",
                "# Browse by company type",
                "",
                "The same bank cut by **what kind of company asks**, because that is how preparation "
                "actually works: you are getting ready for a quant loop, a Big Tech loop or a "
                "twenty-person startup's loop, not for one employer at a time.",
                "",
                "[← Question bank](../README.md) · [← All companies](../companies/README.md)",
                "",
                table(
                    ["Company type", "Companies", "Questions", "What it selects"],
                    [":--", "--:", "--:", ":--"],
                    [
                        [
                            f"[{cut['emoji']} {escape_cell(cut['title'])}]({cut['id']}.md)",
                            f"{len(cut['companies']):,}",
                            f"{len(cut['questions']):,}",
                            escape_cell(cut["note"]),
                        ]
                        for cut in cuts
                    ],
                )
                if cuts
                else "**No employer in the bank is in the company registry yet**, so there is nothing to cut "
                "by. That is a gap in the registry rather than in the bank — every question is still on its "
                "company page. [Adding a company](../CONTRIBUTING.md#adding-a-company-to-the-registry) is a "
                "two-line change.",
                "",
                f"<sub>A cut with fewer than {COMPANY_TYPE_MIN_QUESTIONS} questions behind it does not get a "
                "page: it would appear for an hour and break every link anybody had shared. The sector and "
                "size of an employer come from a hand-written registry — "
                "[adding one](../CONTRIBUTING.md#adding-a-company-to-the-registry) is a two-line change.</sub>",
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
                    f"**{plural(len(rows), 'question')}** in the {format_label(fmt)} format. "
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
                lede=f"**{plural(len(rows), 'question')}** with a sighting recorded in "
                f"{_month_label(key)}.",
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

    # ── the guides index ─────────────────────────────────────────────────────
    if guides:
        ordered_guides = sort_guides(guides, api_labels)
        with_company = [g for g in ordered_guides if g.companies]
        unattributed = [g for g in ordered_guides if not g.companies]
        body = [
            GENERATED_NOTICE,
            "",
            "# How each company interviews",
            "",
            f"**{len(guides):,} round-by-round guides** across "
            f"**{len(guides_by_company)} companies** — what each stage of the loop actually is: "
            "the recruiter screen, the hiring-manager round, the culture interview, the "
            "project deep-dive. Read one before you practise for it.",
            "",
            "[← Question bank](../README.md)",
            "",
        ]
        if not catalog.guides_complete:
            # On the page, not only in a log. A short list of guides looks
            # exactly like a site that publishes few of them, and the only
            # person who can tell the difference is the one reading this.
            body += [
                "> **This list is incomplete.** The catalog API can only hand over one page of "
                "guides, so these are the first "
                f"{len(guides):,} and there are more on "
                f"[the Study section]({SITE}/study). It fills in automatically once the API "
                "can page.",
                "",
            ]
        # Newest first, above the company sections. The index below is
        # reference material sorted alphabetically on purpose (nobody opens it
        # asking what changed this week), which leaves nowhere for a guide
        # published yesterday to be seen — so it gets its own short block, and
        # the hourly job has something new to show on a day with no questions.
        # A publication date after today is a mistyped date upstream, and the
        # rule the sightings hold applies here too: it loses its claim to
        # *recent* rather than being handed the top of the block.
        dated_guides = [
            (stamp, g)
            for g, stamp in ((g, parse_catalog_date(g.added_at)[0]) for g in ordered_guides)
            if stamp is not None and stamp <= today
        ]
        newest = [
            g
            for _, g in sorted(
                dated_guides, key=lambda row: (-row[0].toordinal(), row[1].title.casefold(), row[1].slug)
            )
        ][:RECENT_GUIDES]
        if newest:
            body += [
                "## Recently published",
                "",
                guide_rows(newest, api_labels, with_company=True, preserve_order=True),
                "",
                "[Grouped by topic instead →](by-topic.md)",
                "",
                "## By company",
                "",
            ]
        for key in sorted(guides_by_company, key=lambda k: (-len(guides_by_company[k]), k)):
            rows = guides_by_company[key]
            name = next(
                (company_label(c, api_labels) for g in rows for c in g.companies
                 if company_key(c, api_labels) == key),
                key,
            )
            # Company pages are generated from QUESTIONS and this index is built
            # from GUIDES, so an employer with a guide and no question has no
            # page — and this line linked at one anyway. `companies/general.md`
            # was a live 404 in the published index.
            has_page = key in by_company
            body += [
                f"### {escape_cell(name)}",
                "",
                f"<sub>{plural(len(rows), 'guide')}"
                + (f" · [questions at {escape_cell(name)}](../companies/{key}.md)" if has_page
                   else " · no questions reported at this employer yet")
                + "</sub>",
                "",
                guide_rows(rows, api_labels, with_company=False),
                "",
            ]
        if unattributed:
            body += [
                "### Not tied to one company",
                "",
                guide_rows(unattributed, api_labels, with_company=False),
                "",
            ]
        files["guides/README.md"] = "\n".join(body)

    # ── statistics, free practice, free reading, latest reports ──────────────
    # The four surfaces that carry value rather than navigation. Computed from
    # the same records everything above is rendered from, so a number on an
    # insights page and a row on a company page can never disagree.
    files["insights/README.md"] = report.insights_index(stats_view, api_labels)
    files["insights/topics.md"] = report.topics_page(stats_view)
    files["insights/companies.md"] = report.companies_page(stats_view)
    files["insights/trends.md"] = report.trends_page(stats_view)
    files.update(report.free_pages(ordered, stats_view, api_labels, today))
    if guides:
        files["guides/by-topic.md"] = report.guides_by_topic(guides, api_labels)
    if catalog.experiences:
        files["experiences/README.md"] = report.experiences_page(catalog, api_labels, today)

    # ── data exports ─────────────────────────────────────────────────────────
    files["data/questions.jsonl"] = render_jsonl(ordered, api_labels)
    files["data/questions.csv"] = render_csv(ordered, api_labels)
    files["data/companies.csv"] = render_companies_csv(company_rows)
    files["data/insights.json"] = report.insights_json(stats_view)
    if guides:
        files["data/guides.csv"] = render_guides_csv(guides, api_labels)

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
        f"**{len(ordered):,} questions** · **{len(guides):,} interview guides** · "
        f"**{len(company_rows)} companies** · **{stats_view.free_total:,} free to practise** · "
        f"**{stats_view.window_total:,} reported in the last {stats_view.window_days} days** · "
        f"synced from [the live catalog]({SITE}/developers/api) every hour",
    )
    readme = inject(readme, "gen:insights", report.readme_insights_block(stats_view, api_labels))
    readme = inject(readme, "gen:free", report.readme_free_block(stats_view))
    readme = inject(
        readme,
        "gen:experiences",
        report.readme_experiences_block(catalog, api_labels, README_EXPERIENCE_ROWS)
        if catalog.experiences
        else "_No interview report was published in this snapshot._",
    )
    readme = inject(readme, "gen:formats", format_nav(by_format))
    readme = inject(
        readme,
        "gen:guides",
        (
            f"**Interview process:** [How {len(guides_by_company)} companies interview, "
            f"round by round ({len(guides):,} guides)](guides/README.md) &nbsp;·&nbsp; "
            f"[the same guides by topic](guides/by-topic.md)"
            + ("" if catalog.guides_complete else " — _partial, see the note there_")
        )
        if guides
        else "_No interview guides published yet._",
    )
    readme = inject(
        readme,
        "gen:company-types",
        (
            " · ".join(
                f"[{cut['emoji']} {escape_cell(cut['title'])} ({len(cut['questions']):,})]"
                f"(company-types/{cut['id']}.md)"
                for cut in cuts
            )
            + f"\n\n[**Every company type, with what each one selects →**](company-types/README.md)"
        )
        if cuts
        else "_No employer in the bank is in the company registry yet._",
    )
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
        "guides": len(guides),
        "guides_complete": catalog.guides_complete,
        "guide_companies": len(guides_by_company),
        "companies": len(company_rows),
        "formats": len(format_keys),
        "months": len(month_keys),
        "undated": undated,
        "free": stats_view.free_total,
        "window": stats_view.window_total,
        "window_days": stats_view.window_days,
        "experiences": len(catalog.experiences),
        "experiences_total": catalog.experiences_total,
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
