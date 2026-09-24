#!/usr/bin/env python3
"""Read TrueInterview's public catalog API into plain Python records.

This module is the ONLY thing in the repository that talks to the network, and
it reads exactly one thing: the public, documented, read-only API at
``/api/v1`` (spec: https://trueinterview.io/openapi.json). That API publishes
metadata and never bodies — a title, a company, a format, a difficulty, a
sighting month, a URL.

The one exception is the FREE tier, which this repository republishes on
purpose: the statements and hints of free practice questions and the Study
articles, read from ``/api/cron/content-export`` behind the
``CONTENT_EXPORT_SECRET`` Actions secret (see ``CONTENT-DESIGN.md``). The site
decides what is free and never sends a solution, a test case or a paid row;
:func:`load_catalog` checks every body against the metadata again anyway. With
the secret unset the export is simply not read, and the repository renders the
metadata-only pages it always did.

Two properties the rest of the pipeline relies on:

  * **A read that failed is never an empty catalog.** Every failure raises
    :class:`CatalogError`. A sync that quietly rendered zero questions would
    commit an empty repository over a good one, and the hourly schedule means
    nobody would see it happen.
  * **Records are plain and total.** Every field a renderer reads exists on
    every record, with ``None`` where the catalog genuinely does not know —
    never a zero, never a guess. ``reported`` in particular is ``None`` for a
    question with no recorded sighting, and that is a different fact from "seen
    a long time ago".
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

DEFAULT_BASE_URL = "https://trueinterview.io"

# `/api/v1/questions` caps `limit` at 50 (API_MAX_LIMIT in the site's OpenAPI
# document). Asking for more is not an error there — it is silently clamped —
# so requesting exactly the cap keeps the page count honest.
PAGE_LIMIT = 50

# The published quota is 240 requests per 60s window (`RateLimit-Policy`). The
# whole catalog is ~45 pages, so the sync fits inside one window with room to
# spare; the pause exists so a catalog that grows fivefold still paces itself
# instead of discovering the limit by hitting it.
REQUEST_PAUSE_SECONDS = 0.2

# A runaway `hasMore` (or a catalog that grows past anything we planned for)
# must stop the sync rather than page forever inside a scheduled job.
MAX_PAGES = 400

USER_AGENT = "trueinterview-question-bank-sync/1 (+https://github.com/kevin-2023-code/Tech-Interview-Questions)"


class CatalogError(RuntimeError):
    """The catalog could not be read. Never raised for a legitimately empty page."""


@dataclass(frozen=True)
class Question:
    """One practice question, exactly as the public API publishes it.

    ``reported`` is the catalog's own sighting date (``YYYY-MM`` or
    ``YYYY-MM-DD``) and is ``None`` when no sighting was recorded. ``added_at``
    is when the row entered the bank, which is a fact about us rather than about
    the interview, so the renderers never print one as the other.
    """

    slug: str
    number: int
    title: str
    type: str
    type_label: str
    difficulty: str | None
    companies: tuple[str, ...]
    topics: tuple[str, ...]
    tags: tuple[str, ...]
    rounds: tuple[str, ...]
    reported: str | None
    reported_date: date | None
    reported_is_month_only: bool
    has_solution: bool
    access_tier: str
    url: str
    added_at: str | None
    added_date: date | None


def parse_catalog_date(value: str | None) -> tuple[date | None, bool]:
    """Parse a catalog date into ``(date, month_only)``.

    The bank stores a sighting as ``YYYY-MM-DD`` when the day is known and
    ``YYYY-MM`` when only the month is. A month-only value resolves to the FIRST
    of that month, which is the conservative reading in both places it is used:
    it sorts a month-only sighting behind a dated one in the same month, and it
    can only ever under-claim freshness. Rounding up to the month's end would
    put a 🔥 marker on a question nobody has said was seen this fortnight.

    Anything unparseable — including an out-of-range month or day — is ``None``.
    A date we could not read is not a date, and the row is treated as undated
    rather than being given an invented one.
    """
    if not value:
        return None, False
    text = value.strip()
    try:
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return date.fromisoformat(text[:10]), False
        if len(text) == 7 and text[4] == "-":
            return date(int(text[:4]), int(text[5:7]), 1), True
    except (ValueError, IndexError):
        return None, False
    return None, False


@dataclass(frozen=True)
class Guide:
    """One published article from the site's Study section.

    A different KIND of thing from a question: a question is one task you
    solve, and this is something to read around it. The catalog stores them as
    an ordinary row of a reading type, which is why they arrive from their own
    endpoint rather than in the question list.

    What a guide is ABOUT is not knowable from this row, and a renderer must
    not assume. Most are the "how does this company actually interview"
    writeups — a recruiter screen, a culture round, a deep dive — but the same
    endpoint also carries problems worked end to end and reviews of other
    products, and a company page that announced the first kind over a row of
    the third was wrong in print. `tags` is the only evidence here of what one
    covers; where there is none, say nothing.
    """

    slug: str
    title: str
    companies: tuple[str, ...]
    tags: tuple[str, ...]
    url: str
    added_at: str | None


@dataclass(frozen=True)
class Experience:
    """One candidate-written interview report from the 面经 board.

    The newest thing this repository can point at: a question enters the bank
    when someone curates it, but a report lands the week the loop happened. It
    is metadata like everything else here — company, role, title, date, URL —
    and the write-up itself stays on the site, which is both the rule this
    repository holds and the reason the board can meter its bodies at all.
    """

    id: str
    company: str
    role: str | None
    title: str
    url: str
    posted_at: str | None
    posted_date: date | None


@dataclass(frozen=True)
class QuestionBody:
    """A free question's statement and hints, as the site's export hands them over."""

    slug: str
    type: str
    content_md: str
    hints: tuple[str, ...]


@dataclass(frozen=True)
class Content:
    """The free tier's bodies, keyed by catalog slug.

    ``dropped`` counts bodies the export sent that the metadata did not vouch
    for — a slug the question list does not carry, or one it does not mark
    free. They are never rendered; the count is printed so a disagreement
    between the two endpoints is seen rather than silently resolved.
    """

    questions: dict[str, QuestionBody]
    articles: dict[str, str]
    dropped: int = 0


def _str_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def question_from_payload(item: dict[str, Any]) -> Question:
    """Build a :class:`Question` from one API object, refusing a malformed row.

    Strict on the three fields every page is keyed on (slug, title, url) and
    forgiving everywhere else, because a missing difficulty is a fact the
    catalog is allowed not to know while a missing slug means the payload is not
    what this script was written against.
    """
    slug = item.get("slug")
    title = item.get("title")
    url = item.get("url")
    if not isinstance(slug, str) or not slug.strip():
        raise CatalogError(f"catalog row has no slug: {item!r:.200}")
    if not isinstance(title, str) or not title.strip():
        raise CatalogError(f"catalog row {slug} has no title")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise CatalogError(f"catalog row {slug} has no absolute url")

    difficulty = item.get("difficulty")
    if difficulty is not None and difficulty not in ("easy", "medium", "hard"):
        raise CatalogError(f"catalog row {slug} has unsupported difficulty {difficulty!r}")

    reported = item.get("reported") if isinstance(item.get("reported"), str) else None
    reported_date, month_only = parse_catalog_date(reported)
    added_at = item.get("addedAt") if isinstance(item.get("addedAt"), str) else None
    added_date, _ = parse_catalog_date(added_at)

    primary = item.get("company")
    companies = _str_list(item.get("companies"))
    if isinstance(primary, str) and primary.strip() and primary.strip() not in companies:
        companies = (primary.strip(),) + companies

    number = item.get("number")
    return Question(
        slug=slug.strip(),
        number=number if isinstance(number, int) else 0,
        title=" ".join(title.split()),
        type=str(item.get("type") or "unknown"),
        type_label=str(item.get("typeLabel") or item.get("type") or "Unknown"),
        difficulty=difficulty,
        companies=companies,
        topics=_str_list(item.get("topics")),
        tags=_str_list(item.get("tags")),
        rounds=_str_list(item.get("rounds")),
        reported=reported,
        reported_date=reported_date,
        reported_is_month_only=month_only,
        has_solution=bool(item.get("hasSolution")),
        access_tier=str(item.get("accessTier") or "free"),
        url=url,
        added_at=added_at,
        added_date=added_date,
    )


def guide_from_payload(item: dict[str, Any]) -> Guide:
    """Build a :class:`Guide`, refusing a row with no address."""
    slug, title, url = item.get("slug"), item.get("title"), item.get("url")
    if not isinstance(slug, str) or not slug.strip():
        raise CatalogError(f"guide row has no slug: {item!r:.200}")
    if not isinstance(title, str) or not title.strip():
        raise CatalogError(f"guide row {slug} has no title")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise CatalogError(f"guide row {slug} has no absolute url")

    primary = item.get("company")
    companies = _str_list(item.get("companies"))
    if isinstance(primary, str) and primary.strip() and primary.strip() not in companies:
        companies = (primary.strip(),) + companies

    added_at = item.get("addedAt") if isinstance(item.get("addedAt"), str) else None
    return Guide(
        slug=slug.strip(),
        title=" ".join(title.split()),
        companies=companies,
        tags=_str_list(item.get("tags")),
        url=url,
        added_at=added_at,
    )


def experience_from_payload(item: dict[str, Any]) -> Experience:
    """Build an :class:`Experience`, refusing a row with no address."""
    identifier, title, url = item.get("id"), item.get("title"), item.get("url")
    if not isinstance(identifier, str) or not identifier.strip():
        raise CatalogError(f"experience row has no id: {item!r:.200}")
    if not isinstance(title, str) or not title.strip():
        raise CatalogError(f"experience row {identifier} has no title")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise CatalogError(f"experience row {identifier} has no absolute url")
    posted_at = item.get("postedAt") if isinstance(item.get("postedAt"), str) else None
    posted_date, _ = parse_catalog_date(posted_at)
    role = item.get("role")
    return Experience(
        id=identifier.strip(),
        company=str(item.get("company") or "").strip(),
        role=" ".join(role.split()) if isinstance(role, str) and role.strip() else None,
        title=" ".join(title.split()),
        url=url,
        posted_at=posted_at,
        posted_date=posted_date,
    )


def _get_json(url: str, timeout: int) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
        raise CatalogError(f"could not read {url}: {error}") from error
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        # The API's documented failure envelope is `{ok: false, error: {...}}`.
        # Reporting its own message beats reporting "unexpected shape", because
        # the two cases an operator hits — an unconfigured catalog (503) and a
        # refused parameter (400) — say so there and nowhere else.
        detail = ""
        if isinstance(payload, dict):
            error_obj = payload.get("error")
            if isinstance(error_obj, dict):
                detail = f": {error_obj.get('code')} {error_obj.get('message')}"
        raise CatalogError(f"{url} did not answer ok{detail}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise CatalogError(f"{url} answered ok with no data object")
    return data


def fetch_questions(base_url: str, *, timeout: int = 30) -> list[dict[str, Any]]:
    """Every practice question in the bank, as raw API objects.

    Pages until the API says there is no more. ``total`` is checked against what
    actually arrived: a truncated read that looked successful would silently
    delete rows from every generated page on the next commit, which is the one
    failure mode an hourly unattended job must not have.
    """
    rows: list[dict[str, Any]] = []
    page = 1
    total: int | None = None
    while page <= MAX_PAGES:
        data = _get_json(f"{base_url}/api/v1/questions?page={page}&limit={PAGE_LIMIT}", timeout)
        questions = data.get("questions")
        page_info = data.get("page")
        if not isinstance(questions, list) or not isinstance(page_info, dict):
            raise CatalogError("questions page is missing its questions or page object")
        rows.extend(item for item in questions if isinstance(item, dict))
        if total is None and isinstance(page_info.get("total"), int):
            total = page_info["total"]
        if not page_info.get("hasMore"):
            break
        if not questions:
            raise CatalogError(f"questions page {page} was empty but claimed more")
        page += 1
        time.sleep(REQUEST_PAUSE_SECONDS)
    else:
        raise CatalogError(f"questions did not terminate within {MAX_PAGES} pages")

    if not rows:
        raise CatalogError("the catalog returned no questions at all")
    if total is not None and len(rows) != total:
        raise CatalogError(f"read {len(rows)} questions but the catalog reports {total}")
    return rows


def fetch_company_labels(base_url: str, *, timeout: int = 30) -> dict[str, str]:
    """Slug → display name for as many companies as the API will hand over.

    `/api/v1/companies` returns a single page capped at the API's own limit and
    takes no offset, so on a bank with more companies than that cap this is the
    BUSIEST ones and not all of them. That is why it seeds a map rather than
    being one: `labels.company_label` falls back to the site's own display rules
    for everything this does not cover. Treated as best-effort on purpose — a
    company list that could not be read costs nicer casing on the long tail, and
    must not cost the sync.
    """
    try:
        data = _get_json(f"{base_url}/api/v1/companies?limit={PAGE_LIMIT}", timeout)
    except CatalogError:
        return {}
    companies = data.get("companies")
    if not isinstance(companies, list):
        return {}
    labels: dict[str, str] = {}
    for entry in companies:
        if not isinstance(entry, dict):
            continue
        name, slug = entry.get("name"), entry.get("slug")
        if isinstance(name, str) and isinstance(slug, str) and name.strip() and slug.strip():
            labels[slug.strip()] = name.strip()
    return labels


def fetch_guides(base_url: str, *, timeout: int = 30) -> tuple[list[dict[str, Any]], bool]:
    """Every published Study guide, as raw API objects.

    Pages like `fetch_questions`, with one extra job: it tolerates BOTH shapes
    `/api/v1/articles` can answer with, because this repository and that
    endpoint's paging shipped separately and either can be deployed first.

      * The paged shape — `{articles, page: {…, hasMore}}` — is followed to the
        end, and the row count is checked against `page.total` exactly as the
        question read is.
      * The older shape — `{articles, total}` — has no offset to page with, and
        its `total` is the length of the slice it just returned rather than the
        size of the Study section, so it cannot even report its own truncation.
        A full page under that shape therefore means "there are probably more
        and I cannot reach them".

    Returns `(rows, complete)`. An incomplete read is NOT an error: making it one
    stopped the whole sync — all two thousand questions included — because one
    endpoint could not page yet, which is a far worse outcome than a short list
    of guides. What the incompleteness must never be is SILENT, since a company
    page missing its guides looks exactly like a company that has none. So
    `complete` travels, and the renderer prints the shortfall on the guides page
    itself rather than the job dying to protect a reader who would then have no
    page to read at all.
    """
    rows: list[dict[str, Any]] = []
    page = 1
    total: int | None = None
    while page <= MAX_PAGES:
        data = _get_json(f"{base_url}/api/v1/articles?page={page}&limit={PAGE_LIMIT}", timeout)
        articles = data.get("articles")
        if not isinstance(articles, list):
            raise CatalogError("articles response has no articles array")
        rows.extend(item for item in articles if isinstance(item, dict))

        page_info = data.get("page")
        if not isinstance(page_info, dict):
            # The old shape. A full page means there are almost certainly more
            # and no offset exists to reach them; a short page is the whole set.
            return rows, len(articles) < PAGE_LIMIT
        if total is None and isinstance(page_info.get("total"), int):
            total = page_info["total"]
        if not page_info.get("hasMore"):
            break
        if not articles:
            raise CatalogError(f"articles page {page} was empty but claimed more")
        page += 1
        time.sleep(REQUEST_PAUSE_SECONDS)
    else:
        raise CatalogError(f"articles did not terminate within {MAX_PAGES} pages")

    if total is not None and len(rows) != total:
        raise CatalogError(f"read {len(rows)} guides but the catalog reports {total}")
    return rows, True


def fetch_experiences(base_url: str, *, timeout: int = 30) -> tuple[list[dict[str, Any]], int | None]:
    """The newest interview reports, and how many the board holds in total.

    `/api/v1/interview-experiences` takes a `limit` and nothing else — no page,
    no offset — so this read is a SLICE by construction and can never be the
    whole board. That is a documented limitation rather than a failure, and it
    is handled the way the guides read handles its own: the shortfall travels
    as data (`total`) and the page says which slice it is showing. A section
    headed "every interview report" that silently held fifty of two thousand
    would be the same lie the guides index was built to stop telling.

    A read that FAILS still raises, like every other read in this module. The
    difference is not arbitrary: a limitation is a fact about the endpoint that
    a caveat can state, while a failure is the absence of any fact at all, and
    publishing an empty section over a good one is how an hourly job quietly
    deletes a page nobody was watching.
    """
    data = _get_json(f"{base_url}/api/v1/interview-experiences?limit={PAGE_LIMIT}", timeout)
    rows = data.get("experiences")
    if not isinstance(rows, list):
        raise CatalogError("interview-experiences response has no experiences array")
    total = data.get("total") if isinstance(data.get("total"), int) else None
    return [item for item in rows if isinstance(item, dict)], total


def fetch_content(base_url: str, secret: str, *, timeout: int = 60) -> dict[str, Any]:
    """The free tier's bodies from the site's export.

    Not the public API, and not its ``{ok, data}`` envelope: a plain JSON
    object ``{generatedAt, questions, articles}``. Every failure raises — the
    renderer prunes question pages that stop appearing, so an unreadable export
    must stop the sync rather than read as "nothing is free any more".
    """
    url = f"{base_url.rstrip('/')}/api/cron/content-export"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Authorization": f"Bearer {secret}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
        raise CatalogError(f"could not read {url}: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list) or not isinstance(
        payload.get("articles"), list
    ):
        raise CatalogError(f"{url} did not answer with a content export")
    return payload


def fetch_catalog(base_url: str = DEFAULT_BASE_URL, *, timeout: int = 30) -> dict[str, Any]:
    """The whole snapshot this repository is rendered from."""
    base = base_url.rstrip("/")
    guides, guides_complete = fetch_guides(base, timeout=timeout)
    experiences, experiences_total = fetch_experiences(base, timeout=timeout)
    return {
        "source": base,
        "questions": fetch_questions(base, timeout=timeout),
        "guides": guides,
        "guidesComplete": guides_complete,
        "experiences": experiences,
        "experiencesTotal": experiences_total,
        "companyLabels": fetch_company_labels(base, timeout=timeout),
        # Absent (None) is "not configured", which renders the metadata-only
        # pages; it is never read as "nothing is free".
        "content": fetch_content(base, secret) if (secret := os.environ.get("CONTENT_EXPORT_SECRET")) else None,
    }


@dataclass(frozen=True)
class Catalog:
    """One validated snapshot: what was read, and what could not be."""

    questions: list[Question]
    guides: list[Guide]
    #: False when the API could not hand over the whole Study section. Carried
    #: rather than logged, because the renderer prints it on the page.
    guides_complete: bool
    #: The newest interview reports the board would hand over — a slice, never
    #: the board, since that endpoint takes no offset.
    experiences: list[Experience]
    #: How many reports the board holds, or ``None`` when it did not say. The
    #: page prints "the 50 newest of 2,426" from this pair and says plainly
    #: that it is a slice; `None` means the size is unknown, not zero.
    experiences_total: int | None
    labels: dict[str, str]
    #: The free tier's bodies, or ``None`` when the export was not read. ``None``
    #: renders the metadata-only pages; an empty :class:`Content` is a real
    #: answer ("nothing is free") and is treated as one.
    content: Content | None = None


def load_catalog(payload: Any) -> Catalog:
    """Validate a snapshot and turn it into records."""
    if not isinstance(payload, dict):
        raise CatalogError("catalog snapshot must be a JSON object")
    raw = payload.get("questions")
    if not isinstance(raw, list) or not raw:
        raise CatalogError("catalog snapshot has no questions array")
    labels = payload.get("companyLabels")
    if not isinstance(labels, dict):
        labels = {}
    questions = [question_from_payload(item) for item in raw if isinstance(item, dict)]
    seen: set[str] = set()
    for question in questions:
        if question.slug in seen:
            raise CatalogError(f"catalog snapshot has duplicate slug {question.slug!r}")
        seen.add(question.slug)

    # Guides are OPTIONAL in a snapshot, unlike questions. A deployment with an
    # empty Study section is a normal state; a deployment with no questions is
    # a failed read. The two must not be refused the same way.
    raw_guides = payload.get("guides")
    guides = (
        [guide_from_payload(item) for item in raw_guides if isinstance(item, dict)]
        if isinstance(raw_guides, list)
        else []
    )
    seen_guides: set[str] = set()
    for guide in guides:
        if guide.slug in seen_guides:
            raise CatalogError(f"catalog snapshot has duplicate guide slug {guide.slug!r}")
        seen_guides.add(guide.slug)

    # Interview reports are OPTIONAL in a snapshot for the same reason guides
    # are: a deployment whose board is empty is a normal state, and a fixture
    # written before this section existed must still render.
    raw_experiences = payload.get("experiences")
    experiences = (
        [experience_from_payload(item) for item in raw_experiences if isinstance(item, dict)]
        if isinstance(raw_experiences, list)
        else []
    )
    seen_experiences: set[str] = set()
    for experience in experiences:
        if experience.id in seen_experiences:
            raise CatalogError(f"catalog snapshot has duplicate experience id {experience.id!r}")
        seen_experiences.add(experience.id)
    experiences_total = payload.get("experiencesTotal")

    # Absent means complete: a hand-written fixture should not have to opt in to
    # the normal case, and only a live read that fell short sets it False.
    guides_complete = payload.get("guidesComplete")
    content = content_from_payload(payload.get("content"), questions, guides)
    return Catalog(
        content=content,
        questions=questions,
        guides=guides,
        guides_complete=guides_complete is not False,
        experiences=experiences,
        experiences_total=experiences_total if isinstance(experiences_total, int) else None,
        labels={str(k): str(v) for k, v in labels.items() if isinstance(v, str)},
    )


def content_from_payload(raw: Any, questions: list[Question], guides: list[Guide]) -> Content | None:
    """Validate the export against the metadata it will be rendered beside.

    The second fence. The site already exports only free rows; a body is kept
    here only if the public question list ALSO says that slug is free, or the
    public article list carries it. Anything else is dropped and counted — a
    disagreement between two endpoints is resolved toward publishing less.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CatalogError("content export must be a JSON object")
    raw_questions, raw_articles = raw.get("questions"), raw.get("articles")
    if not isinstance(raw_questions, list) or not isinstance(raw_articles, list):
        raise CatalogError("content export has no questions/articles arrays")
    free = {q.slug: q for q in questions if q.access_tier == "free"}
    guide_slugs = {g.slug for g in guides}
    bodies: dict[str, QuestionBody] = {}
    articles: dict[str, str] = {}
    dropped = 0
    for item in raw_questions:
        if not isinstance(item, dict):
            raise CatalogError("content export has a malformed question")
        slug, text = item.get("slug"), item.get("contentMd")
        if not isinstance(slug, str) or not isinstance(text, str) or not text.strip():
            raise CatalogError(f"content export question {slug!r} has no body")
        meta = free.get(slug)
        if meta is None or item.get("type") != meta.type:
            dropped += 1
            continue
        bodies[slug] = QuestionBody(slug=slug, type=meta.type, content_md=text.strip(), hints=_str_list(item.get("hints")))
    for item in raw_articles:
        if not isinstance(item, dict):
            raise CatalogError("content export has a malformed article")
        slug, text = item.get("slug"), item.get("contentMd")
        if not isinstance(slug, str) or not isinstance(text, str) or not text.strip():
            raise CatalogError(f"content export article {slug!r} has no body")
        if slug not in guide_slugs:
            dropped += 1
            continue
        articles[slug] = text.strip()
    return Content(questions=bodies, articles=articles, dropped=dropped)


def snapshot(catalog: "Catalog", source: str) -> dict[str, Any]:
    """Round-trip a set of records back into the snapshot shape (for --snapshot-out)."""
    questions, guides, labels = catalog.questions, catalog.guides, catalog.labels
    return {
        "source": source,
        "companyLabels": labels,
        "guidesComplete": catalog.guides_complete,
        "experiencesTotal": catalog.experiences_total,
        "content": None
        if catalog.content is None
        else {
            "questions": [
                {"slug": b.slug, "type": b.type, "contentMd": b.content_md, "hints": list(b.hints)}
                for b in sorted(catalog.content.questions.values(), key=lambda b: b.slug)
            ],
            "articles": [
                {"slug": slug, "contentMd": text} for slug, text in sorted(catalog.content.articles.items())
            ],
        },
        "experiences": [
            {
                "id": e.id,
                "company": e.company,
                "role": e.role,
                "title": e.title,
                "url": e.url,
                "postedAt": e.posted_at,
            }
            for e in catalog.experiences
        ],
        "guides": [
            {
                "slug": g.slug,
                "title": g.title,
                "companies": list(g.companies),
                "tags": list(g.tags),
                "url": g.url,
                "addedAt": g.added_at,
            }
            for g in guides
        ],
        "questions": [
            {
                "slug": q.slug,
                "number": q.number,
                "title": q.title,
                "type": q.type,
                "typeLabel": q.type_label,
                "difficulty": q.difficulty,
                "companies": list(q.companies),
                "topics": list(q.topics),
                "tags": list(q.tags),
                "rounds": list(q.rounds),
                "reported": q.reported,
                "hasSolution": q.has_solution,
                "accessTier": q.access_tier,
                "url": q.url,
                "addedAt": q.added_at,
            }
            for q in questions
        ],
    }
