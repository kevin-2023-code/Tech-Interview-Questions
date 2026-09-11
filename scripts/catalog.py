#!/usr/bin/env python3
"""Read TrueInterview's public catalog API into plain Python records.

This module is the ONLY thing in the repository that talks to the network, and
it reads exactly one thing: the public, documented, read-only API at
``/api/v1`` (spec: https://trueinterview.io/openapi.json). That API publishes
metadata and never bodies — a title, a company, a format, a difficulty, a
sighting month, a URL — so this repository is a pointer into the site, not a
copy of it. Nothing here fetches a question statement, a solution, or a test
case, and nothing may be added that does: the moment this directory holds the
content instead of the address, it stops being an index and starts being a
mirror of a product that people pay for.

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
    """One published guide from the site's Study section.

    These are the "how does this company actually interview" writeups — a
    recruiter screen, a culture round, a technical deep dive — and they are a
    different KIND of thing from a question: a question is one task you solve,
    a guide is the shape of the loop it sits inside. The catalog stores them as
    an ordinary row of a reading type, which is why they arrive from their own
    endpoint rather than in the question list.
    """

    slug: str
    title: str
    companies: tuple[str, ...]
    tags: tuple[str, ...]
    url: str
    added_at: str | None


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


def fetch_catalog(base_url: str = DEFAULT_BASE_URL, *, timeout: int = 30) -> dict[str, Any]:
    """The whole snapshot this repository is rendered from."""
    base = base_url.rstrip("/")
    guides, guides_complete = fetch_guides(base, timeout=timeout)
    return {
        "source": base,
        "questions": fetch_questions(base, timeout=timeout),
        "guides": guides,
        "guidesComplete": guides_complete,
        "companyLabels": fetch_company_labels(base, timeout=timeout),
    }


@dataclass(frozen=True)
class Catalog:
    """One validated snapshot: what was read, and what could not be."""

    questions: list[Question]
    guides: list[Guide]
    #: False when the API could not hand over the whole Study section. Carried
    #: rather than logged, because the renderer prints it on the page.
    guides_complete: bool
    labels: dict[str, str]


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

    # Absent means complete: a hand-written fixture should not have to opt in to
    # the normal case, and only a live read that fell short sets it False.
    guides_complete = payload.get("guidesComplete")
    return Catalog(
        questions=questions,
        guides=guides,
        guides_complete=guides_complete is not False,
        labels={str(k): str(v) for k, v in labels.items() if isinstance(v, str)},
    )


def snapshot(catalog: "Catalog", source: str) -> dict[str, Any]:
    """Round-trip a set of records back into the snapshot shape (for --snapshot-out)."""
    questions, guides, labels = catalog.questions, catalog.guides, catalog.labels
    return {
        "source": source,
        "companyLabels": labels,
        "guidesComplete": catalog.guides_complete,
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
