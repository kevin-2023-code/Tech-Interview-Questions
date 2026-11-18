#!/usr/bin/env python3
"""Read TrueInterview's public catalog API into plain Python records.

This module is the ONLY thing in the repository that talks to the network, and
it reads exactly one thing: the public, documented, read-only API at
``/api/v1`` (spec: https://trueinterview.io/openapi.json). That API publishes
metadata and never bodies — a title, a company, a format, a difficulty, a
sighting month, a URL.

The one exception: the statements and hints of the questions listed in
``scripts/common_questions.txt`` — well-known classics and questions already
public elsewhere — read from ``/api/cron/content-export`` behind the
``CONTENT_EXPORT_SECRET`` Actions secret. Everything else in that export is
discarded on arrival (:func:`common_only`), guides included. With the secret
unset the export is not read and every page is index-only.

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
from pathlib import Path
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
class QuestionBody:
    """A free question's statement and hints, as the site's export hands them over."""

    slug: str
    type: str
    content_md: str
    hints: tuple[str, ...]


@dataclass(frozen=True)
class Content:
    """The published statements, keyed by catalog slug.

    ``dropped`` counts bodies the export sent that are never rendered: a slug
    the question list does not carry or does not mark free, and every article
    (guides are read on the site). The count is printed so a disagreement
    between the two endpoints is seen rather than silently resolved.
    """

    questions: dict[str, QuestionBody]
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


def fetch_content(base_url: str, secret: str, *, timeout: int = 60) -> dict[str, Any]:
    """The question bodies from the site's export.

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


COMMON_QUESTIONS_FILE = Path(__file__).resolve().parent / "common_questions.txt"


def common_question_slugs(path: Path = COMMON_QUESTIONS_FILE) -> frozenset[str]:
    """The allowlist of questions whose statement may be published here."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return frozenset(line.strip() for line in lines if line.strip() and not line.startswith("#"))


def common_only(payload: dict[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    """The export, cut down to what this repository publishes.

    Only an allowlisted question keeps its body, and no article does: guides
    are read on the site. Applied to the export before anything else sees it,
    so a body that is not published is never rendered, counted or written to a
    snapshot either.
    """
    return {
        **payload,
        "questions": [q for q in payload["questions"] if isinstance(q, dict) and q.get("slug") in allowed],
        "articles": [],
    }


def fetch_catalog(base_url: str = DEFAULT_BASE_URL, *, timeout: int = 30) -> dict[str, Any]:
    """The whole snapshot this repository is rendered from."""
    base = base_url.rstrip("/")
    return {
        "source": base,
        "questions": fetch_questions(base, timeout=timeout),
        "companyLabels": fetch_company_labels(base, timeout=timeout),
        # Absent (None) is "not configured", which renders the index-only pages.
        # Configured, only the allowlisted questions' bodies are kept.
        "content": common_only(fetch_content(base, secret), common_question_slugs())
        if (secret := os.environ.get("CONTENT_EXPORT_SECRET"))
        else None,
    }


@dataclass(frozen=True)
class Catalog:
    """One validated snapshot."""

    questions: list[Question]
    labels: dict[str, str]
    #: The published statements, or ``None`` when the export was not read.
    #: ``None`` renders the metadata-only pages; an empty :class:`Content` is a
    #: real answer ("nothing is published") and is treated as one.
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
    return Catalog(
        content=content_from_payload(payload.get("content"), questions),
        questions=questions,
        labels={str(k): str(v) for k, v in labels.items() if isinstance(v, str)},
    )


def content_from_payload(raw: Any, questions: list[Question]) -> Content | None:
    """Validate the export against the metadata it will be rendered beside.

    The second fence. The site already exports only free rows and
    :func:`common_only` keeps only the allowlisted ones; a body is kept here
    only if the public question list ALSO says that slug is free. Anything else
    is dropped and counted, and so is every article: no guide text is ever
    published here. A disagreement is resolved toward publishing less.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CatalogError("content export must be a JSON object")
    raw_questions, raw_articles = raw.get("questions"), raw.get("articles")
    if not isinstance(raw_questions, list) or not isinstance(raw_articles, list):
        raise CatalogError("content export has no questions/articles arrays")
    free = {q.slug: q for q in questions if q.access_tier == "free"}
    bodies: dict[str, QuestionBody] = {}
    dropped = len(raw_articles)
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
    return Content(questions=bodies, dropped=dropped)


def snapshot(catalog: "Catalog", source: str) -> dict[str, Any]:
    """Round-trip a set of records back into the snapshot shape (for --snapshot-out)."""
    return {
        "source": source,
        "companyLabels": catalog.labels,
        "content": None
        if catalog.content is None
        else {
            "questions": [
                {"slug": b.slug, "type": b.type, "contentMd": b.content_md, "hints": list(b.hints)}
                for b in sorted(catalog.content.questions.values(), key=lambda b: b.slug)
            ],
            "articles": [],
        },
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
            for q in catalog.questions
        ],
    }
