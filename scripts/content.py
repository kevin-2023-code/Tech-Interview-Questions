#!/usr/bin/env python3
"""The free tier, rendered as pages you can read here.

Everything else in this repository is an index: titles, counts and dates, each
row a link into trueinterview.io. This module renders the part that is CONTENT,
from the free tier's bodies (``catalog.Content``):

  * ``companies/<key>/README.md`` — a company's home: how it interviews, its
    free questions, its guides. Rendered for every company, with or without
    bodies; without them every link simply points at the site.
  * ``companies/<key>/interview-process.md`` (+ ``interview-process-<role>.md``)
    — the site's interview-process guides, in full.
  * ``companies/<key>/guides/<name>.md`` — every other Study guide filed there.
  * ``questions/<format>/<name>/README.md`` — one page per free question: the
    statement and the hints. Never the solution: that stays on the site, next to
    the editor that runs an attempt against the hidden tests, and it is the
    reason to click through.

Three rules, each with a reason:

  * **A path, once published, does not move.** A question's folder is named
    from its slug (or, for a UUID slug, its title) the first time it appears,
    and recorded in ``data/paths.json``. A later title change leaves the file
    where it is, because a moved file is a broken link on every page that
    shared it. See :func:`assign_paths`.
  * **A body is stored once.** A free question is reported at up to a dozen
    employers; its page lives under its format and every company links to it,
    rather than a dozen copies drifting apart.
  * **Links in a body land somewhere real.** The site writes site-relative
    links (``/questions/x``, ``/study/y``). Each one becomes the page here when
    this repository publishes that target, and the absolute site URL when it
    does not. See :func:`rewrite_links`.

Pure, like ``build.py``: records in, a path → text map out.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from catalog import Content, Guide, Question
from insights import ROUND_LABELS
from labels import company_key, company_label, difficulty_label, format_label
from render import GENERATED_NOTICE, date_label, escape_cell, plural, sort_questions, table

SITE = "https://trueinterview.io"

LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"

REPO = "https://github.com/kevin-2023-code/Tech-Interview-Questions"

#: Formats whose runs get a server-judged verdict on the site. Object-oriented
#: programming routes into the algorithm workspace, so it is judged too.
JUDGED = frozenset({"algorithm", "object-oriented-programming", "sql"})

#: A guide shorter than this is a prompt filed as an article, not a writeup, and
#: gets no page of its own: a page of two sentences is what makes a repository
#: look scraped. It is still listed, linking to the site.
MIN_GUIDE_CHARS = 600

#: How much of the interview-process guide the company home quotes before
#: "Read the full process". The first section is the loop itself.
PROCESS_EXCERPT_CHARS = 3500

MAX_NAME_CHARS = 72

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PROCESS_SUFFIX = "interview-process"


def slugify(text: str) -> str:
    """A file-name-safe slug, the site's own shape (``[a-z0-9-]``)."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower().replace("&", " and ")).strip("-")
    return slug[:MAX_NAME_CHARS].rstrip("-") or "untitled"


def _readable(slug: str, title: str) -> str:
    return slugify(title) if _UUID.match(slug) else slugify(slug)


# ── paths ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GuidePlan:
    """Where one guide lives here, if anywhere, and what kind it is."""

    guide: Guide
    company: str | None  # the company home it is filed under
    kind: str  # "process" | "role" | "guide"
    role: str | None
    path: str | None  # None: listed, but no page of its own


def assign_paths(
    registry: dict[str, str],
    wanted: Sequence[tuple[str, str, str]],
) -> dict[str, str]:
    """Stable paths for ``(key, directory, name)`` requests.

    An entry already in ``registry`` keeps its path as long as it is still in
    the directory asked for — a question whose FORMAT changed moves, because
    its old folder now sits under the wrong heading. Every path the registry has
    ever held stays reserved, so a retired question's URL is never handed to a
    different question. New entries are named in the order given, which the
    caller makes deterministic, with ``-2``, ``-3`` on a collision.
    """
    taken = set(registry.values())
    out = dict(registry)
    for key, directory, name in wanted:
        current = registry.get(key)
        if current is not None and posixpath.dirname(current) == directory:
            continue
        candidate, n = name, 1
        while True:
            path = f"{directory}/{candidate}"
            if path not in taken:
                break
            n += 1
            candidate = f"{name}-{n}"
        taken.add(path)
        out[key] = path
    return out


def question_path(registry: dict[str, str], question: Question) -> str:
    return f"{registry[f'question:{question.slug}']}/README.md"


def _process_role(guide: Guide) -> tuple[str, str | None] | None:
    """``("process", None)``, ``("role", "software-engineer")`` or ``None``.

    The site names a company's process guide ``<company>-interview-process`` and
    a role's ``<company>-<role>-interview-process``, where ``<company>`` is the
    STORED company value (``akunacapital``, ``scale.ai``), not this repository's
    page key — so the prefix is derived from the raw values the guide carries.
    """
    if not guide.slug.endswith(PROCESS_SUFFIX):
        return None
    stem = guide.slug[: -len(PROCESS_SUFFIX)].rstrip("-")
    for raw in guide.companies:
        # `Akuna Capital` is stored as `akunacapital`: try the slug both with and
        # without its word breaks, so a display name matches as well as a raw value.
        for prefix in dict.fromkeys((slugify(raw), slugify(raw).replace("-", ""))):
            if stem == prefix:
                return "process", None
            if stem.startswith(prefix + "-"):
                return "role", stem[len(prefix) + 1 :]
    return None


def plan_guides(
    guides: Sequence[Guide],
    homes: frozenset[str],
    content: Content | None,
    registry: dict[str, str],
    api_labels: dict[str, str],
) -> tuple[list[GuidePlan], dict[str, str]]:
    """Decide where every guide goes. Returns the plans and the updated registry."""
    bodies = content.articles if content else {}
    plans: list[GuidePlan] = []
    wanted: list[tuple[str, str, str]] = []
    taken_process: set[tuple[str, str | None]] = set()
    pending: list[tuple[Guide, str | None, str, str | None]] = []
    for guide in sorted(guides, key=lambda g: g.slug):
        home = next(
            (key for key in (company_key(c, api_labels) for c in guide.companies) if key in homes),
            None,
        )
        kind, role = "guide", None
        detected = _process_role(guide) if home else None
        if detected and (home, detected[1]) not in taken_process:
            kind, role = detected
            taken_process.add((home, role))
        pending.append((guide, home, kind, role))
        body = bodies.get(guide.slug)
        # A guide filed under no company home gets no page: nothing here would
        # link to it, and the guides index already points at the site.
        if kind == "guide" and home and body is not None and len(body) >= MIN_GUIDE_CHARS:
            wanted.append((f"guide:{guide.slug}", f"companies/{home}/guides", _readable(guide.slug, guide.title)))
    registry = assign_paths(registry, wanted)
    for guide, home, kind, role in pending:
        path = None
        if guide.slug in bodies:
            if kind == "process":
                path = f"companies/{home}/interview-process.md"
            elif kind == "role":
                path = f"companies/{home}/interview-process-{role}.md"
            elif home and f"guide:{guide.slug}" in registry and len(bodies[guide.slug]) >= MIN_GUIDE_CHARS:
                path = f"{registry[f'guide:{guide.slug}']}.md"
        plans.append(GuidePlan(guide=guide, company=home, kind=kind, role=role, path=path))
    return plans, registry


# ── bodies ───────────────────────────────────────────────────────────────────

_INLINE = re.compile(r"(\]\(\s*<?)(/[^)\s>]*)")
_REFERENCE = re.compile(r"^(\s*\[[^\]]+\]:\s*<?)(/\S*?)(>?\s*)$", re.M)
_ATTRIBUTE = re.compile(r"""((?:href|src)=["'])(/[^"']*)""")


def rewrite_links(markdown: str, from_path: str, local: dict[str, str]) -> str:
    """Point every site-relative link at the page here, or at the site.

    ``local`` maps a site path (``/questions/two-sum``) to a repository path.
    Anything not in it becomes absolute, so nothing is left pointing at a path
    that does not exist in this repository.
    """
    here = posixpath.dirname(from_path)

    def target(path: str) -> str:
        split = re.match(r"^([^?#]*)(.*)$", path)
        assert split is not None
        bare, rest = split.group(1).rstrip("/") or "/", split.group(2)
        if bare in local:
            return posixpath.relpath(local[bare], here) + (rest if rest.startswith("#") else "")
        return SITE + path

    markdown = _INLINE.sub(lambda m: m.group(1) + target(m.group(2)), markdown)
    markdown = _REFERENCE.sub(lambda m: m.group(1) + target(m.group(2)) + m.group(3), markdown)
    return _ATTRIBUTE.sub(lambda m: m.group(1) + target(m.group(2)), markdown)


def demote_headings(markdown: str) -> str:
    """One heading level down, outside code fences — for a body quoted under an H2."""
    out, fenced = [], False
    for line in markdown.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        if not fenced and re.match(r"^#{1,5} ", line):
            line = "#" + line
        out.append(line)
    return "\n".join(out)


def process_excerpt(markdown: str) -> tuple[str, bool]:
    """The opening of a process guide — up to its second section — and whether it was cut."""
    headings = [m.start() for m in re.finditer(r"^## ", markdown, re.M)]
    end = headings[1] if len(headings) > 1 else len(markdown)
    if end > PROCESS_EXCERPT_CHARS:
        cut = markdown.rfind("\n\n", 0, PROCESS_EXCERPT_CHARS)
        end = cut if cut > 0 else PROCESS_EXCERPT_CHARS
    return markdown[:end].rstrip(), end < len(markdown.rstrip())


# ── pieces ───────────────────────────────────────────────────────────────────


def _root(path: str) -> str:
    """The relative prefix from ``path`` back to the repository root."""
    depth = path.count("/")
    return "../" * depth


def footer(path: str) -> str:
    issues = f"{REPO}/issues/new?template=correction.yml"
    return (
        "---\n\n"
        f"<sub>From [TrueInterview]({SITE}) — the free part of its question bank and company interview "
        f"guides, synced every hour. Text licensed [CC BY 4.0]({LICENSE_URL}): share or adapt it anywhere, "
        f"crediting TrueInterview with a link back ([how]({_root(path)}LICENSE-CONTENT.md)). "
        f"[Report a problem with this page]({issues}).</sub>\n"
    )


def _companies_line(names: Sequence[tuple[str, str]], homes: frozenset[str], from_path: str) -> str:
    root = _root(from_path)
    return " · ".join(
        f"[{escape_cell(name)}]({root}companies/{key}/README.md)" if key in homes else escape_cell(name)
        for key, name in names
    )


def _question_companies(question: Question, api_labels: dict[str, str]) -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for raw in question.companies:
        key = company_key(raw, api_labels)
        if key and key not in seen:
            seen[key] = company_label(raw, api_labels)
    return list(seen.items())


def _rounds(question: Question) -> str:
    return ", ".join(ROUND_LABELS.get(r, r) for r in question.rounds) or "—"


def _reported(question: Question) -> str:
    return date_label(question.reported_date, month_only=True)


# ── pages ────────────────────────────────────────────────────────────────────


def question_page(
    question: Question,
    body_md: str,
    hints: Sequence[str],
    path: str,
    local: dict[str, str],
    homes: frozenset[str],
    api_labels: dict[str, str],
) -> str:
    companies = _question_companies(question, api_labels)
    shown = companies[:6]
    asked = " · ".join(escape_cell(name) for _key, name in shown) + (
        f" · +{len(companies) - len(shown)}" if len(companies) > len(shown) else ""
    )
    meta = table(
        ["Format", "Difficulty", "Asked at", "Round", "Topics", "Last reported"],
        ["---"] * 6,
        [
            [
                format_label(question.type),
                difficulty_label(question.difficulty) or "—",
                asked or "—",
                _rounds(question),
                escape_cell(", ".join(question.topics)) or "—",
                _reported(question),
            ]
        ],
    )
    offer = (
        "a runnable editor, the sample and hidden tests, a judged verdict, and the reference solution"
        if question.type in JUDGED
        else "the interview workspace, an AI interviewer to push back on your design, and the reference solution"
    )
    lines = [
        GENERATED_NOTICE,
        "",
        f"# {question.title}",
        "",
        "<!-- meta:begin -->",
        meta,
        "<!-- meta:end -->",
        "",
        f"> **▶ [Solve it on TrueInterview]({question.url})** — free, no card: {offer}.",
        "",
        "## Problem",
        "",
        rewrite_links(demote_headings(body_md) if re.search(r"^# ", body_md, re.M) else body_md, path, local),
        "",
    ]
    if hints:
        lines += ["## Hints", ""]
        for index, hint in enumerate(hints, start=1):
            lines += [
                "<details>",
                f"<summary>Hint {index}</summary>",
                "",
                rewrite_links(hint, path, local),
                "",
                "</details>",
                "",
            ]
    lines += [
        "## Solution",
        "",
        f"The reference solution is on [the question page]({question.url}), beside an editor that runs your "
        "own attempt first. It is not reproduced here — try the problem before you read the answer.",
        "",
    ]
    if companies:
        lines += ["## Asked at", "", _companies_line(companies, homes, path), ""]
    lines.append(footer(path))
    return "\n".join(lines)


def guide_page(plan: GuidePlan, body_md: str, name: str, key: str | None, local: dict[str, str],
               siblings: Sequence[GuidePlan] = ()) -> str:
    path = plan.path
    assert path is not None
    guide = plan.guide
    root = _root(path)
    if key:
        home = posixpath.relpath(f"companies/{key}/README.md", posixpath.dirname(path))
        stats = posixpath.relpath(f"companies/{key}.md", posixpath.dirname(path))
        back = (
            f"[← {escape_cell(name)}]({home}) · [Every {escape_cell(name)} question]({stats}) · "
            f"[Read it on TrueInterview]({guide.url})"
        )
    else:
        back = f"[← All guides]({root}guides/README.md) · [Read it on TrueInterview]({guide.url})"
    lines = [GENERATED_NOTICE, "", f"# {guide.title}", "", back, ""]
    if guide.tags:
        lines += [" ".join(f"`{escape_cell(tag)}`" for tag in guide.tags), ""]
    roles = [s for s in siblings if s.kind == "role" and s.path]
    if plan.kind == "process" and roles:
        lines += [
            "**By role:** "
            + " · ".join(
                f"[{escape_cell(_role_label(s.role))}]({posixpath.relpath(s.path, posixpath.dirname(path))})"
                for s in roles
            ),
            "",
        ]
    lines += ["---", "", rewrite_links(body_md, path, local), "", footer(path)]
    return "\n".join(lines)


def _role_label(role: str | None) -> str:
    words = (role or "").split("-")
    small = {"ai": "AI", "ml": "ML", "sre": "SRE"}
    return " ".join(small.get(w, w.capitalize()) for w in words if w)


def company_home(
    *,
    key: str,
    name: str,
    questions: Sequence[Question],
    plans: Sequence[GuidePlan],
    content: Content | None,
    local: dict[str, str],
    registry: dict[str, str],
    today: date,
) -> str:
    path = f"companies/{key}/README.md"
    free = [q for q in sort_questions(questions, today) if q.access_tier == "free"]
    readable = [q for q in free if content and q.slug in content.questions]
    process = next((p for p in plans if p.kind == "process"), None)
    roles = [p for p in plans if p.kind == "role"]
    others = [p for p in plans if p.kind == "guide"]
    dated = [q.reported_date for q in questions if q.reported_date and q.reported_date <= today]

    glance = table(
        ["", ""],
        [":--", ":--"],
        [
            ["Questions reported", f"[{len(questions):,}](../{key}.md)"],
            ["Free to read here", f"{len(readable):,}" if content else f"{len(free):,} (on TrueInterview)"],
            ["Interview-process guides", f"{int(process is not None) + len(roles)}"],
            ["Other guides", f"{len(others):,}"],
            ["Most recent sighting", date_label(max(dated)) if dated else "—"],
        ],
    )
    lines = [
        GENERATED_NOTICE,
        "",
        f"# {name} interview process & questions",
        "",
        f"How {escape_cell(name)} interviews, and the questions candidates reported there. Free questions are "
        "published here in full, statement and hints; everything else links to TrueInterview.",
        "",
        f"[← All companies](../README.md) · [Every {escape_cell(name)} question, with statistics](../{key}.md) · "
        f"[Practise on TrueInterview]({SITE}/problems/company/{key})",
        "",
        glance,
        "",
        f"## How {name} interviews",
        "",
    ]
    process_body = content.articles.get(process.guide.slug) if (content and process) else None
    if process and process.path and process_body:
        excerpt, cut = process_excerpt(process_body)
        lines += [
            rewrite_links(demote_headings(excerpt), path, local),
            "",
            f"**[Read the full {escape_cell(name)} interview process →](interview-process.md)**"
            if cut
            else f"<sub>From [{escape_cell(process.guide.title)}](interview-process.md).</sub>",
            "",
        ]
    elif process:
        lines += [f"[{escape_cell(process.guide.title)}]({process.guide.url}) — the full guide, on TrueInterview.", ""]
    else:
        lines += [
            f"No written process guide yet. [The loop, as reported](../{key}.md#the-loop-as-reported) counts which "
            "round each reported question came from.",
            "",
        ]
    if roles:
        lines += [
            "**By role:** "
            + " · ".join(
                f"[{escape_cell(_role_label(p.role))}]({posixpath.relpath(p.path, f'companies/{key}') if p.path else p.guide.url})"
                for p in roles
            ),
            "",
        ]

    lines += [f"## Free {name} questions", ""]
    if free:
        rows = []
        for q in free:
            target = (
                posixpath.relpath(question_path(registry, q), f"companies/{key}")
                if content and q.slug in content.questions and f"question:{q.slug}" in registry
                else q.url
            )
            rows.append(
                [
                    f"[{escape_cell(q.title)}]({target})",
                    format_label(q.type),
                    difficulty_label(q.difficulty) or "—",
                    _rounds(q),
                    _reported(q),
                    f"[Solve]({q.url})",
                ]
            )
        lines += [
            f"{plural(len(free), 'question')} reported at {escape_cell(name)} open without a paid plan"
            + (" — the statement and hints are on each linked page here." if readable else ".")
            + " Newest sighting first.",
            "",
            table(["Question", "Format", "Difficulty", "Round", "Reported", ""],
                  [":--", ":--", ":-:", ":--", ":--", ":--"], rows),
            "",
        ]
    else:
        lines += ["None of the questions reported here is on the free tier yet.", ""]

    if others:
        lines += [f"## Guides", ""]
        guide_rows = []
        for p in sorted(others, key=lambda p: p.guide.title.casefold()):
            target = posixpath.relpath(p.path, f"companies/{key}") if p.path else p.guide.url
            guide_rows.append(
                [f"[{escape_cell(p.guide.title)}]({target})", escape_cell(", ".join(p.guide.tags)) or "—"]
            )
        lines += [table(["Guide", "Tags"], [":--", ":--"], guide_rows), ""]

    lines += [
        "## Everything else",
        "",
        f"- [All {len(questions):,} questions reported at {escape_cell(name)}](../{key}.md) — the loop by round, "
        "topics, what was asked in the last 90 days, and where to start.",
        f"- [Practise every {escape_cell(name)} question on TrueInterview]({SITE}/problems/company/{key}).",
        "",
        footer(path),
    ]
    return "\n".join(lines)


def questions_index(publishable: Sequence[Question], registry: dict[str, str], api_labels: dict[str, str]) -> str:
    """``questions/README.md``: every free question published here, by format, easiest first."""
    path = "questions/README.md"
    lines = [
        GENERATED_NOTICE,
        "",
        "# Free interview questions",
        "",
        "Every free question in the TrueInterview bank, published here in full — the statement and the "
        "hints. The reference solution, and an editor that runs your attempt against the hidden tests, are "
        "one click away on each page.",
        "",
        "[← Question bank](../README.md) · [Companies](../companies/README.md) · "
        "[Free questions, with statistics](../free/README.md)",
        "",
    ]
    if not publishable:
        lines += [
            "No question pages are published yet: the sync has not been given the free-content export. "
            "[Every free question is on TrueInterview](../free/README.md) in the meantime.",
            "",
            footer(path),
        ]
        return "\n".join(lines)
    order = {"easy": 0, "medium": 1, "hard": 2}
    by_format: dict[str, list[Question]] = {}
    for q in publishable:
        by_format.setdefault(q.type, []).append(q)
    for fmt in sorted(by_format, key=lambda f: (-len(by_format[f]), f)):
        rows = sorted(by_format[fmt], key=lambda q: (order.get(q.difficulty or "", 3), q.title.casefold(), q.slug))
        lines += [f"## {format_label(fmt)} ({len(rows)})", ""]
        lines += [
            table(
                ["Question", "Difficulty", "Asked at"],
                [":--", ":-:", ":--"],
                [
                    [
                        f"[{escape_cell(q.title)}]({posixpath.relpath(question_path(registry, q), 'questions')})",
                        difficulty_label(q.difficulty) or "—",
                        " · ".join(escape_cell(n) for _k, n in _question_companies(q, api_labels)[:5]) or "—",
                    ]
                    for q in rows
                ],
            ),
            "",
        ]
    lines.append(footer(path))
    return "\n".join(lines)


# ── the whole content layer ──────────────────────────────────────────────────


def render_content(
    *,
    questions: Sequence[Question],
    guides: Sequence[Guide],
    by_company: dict[str, tuple[str, list[Question]]],
    content: Content | None,
    registry: dict[str, str],
    api_labels: dict[str, str],
    today: date,
) -> tuple[dict[str, str], dict[str, str], dict[str, int]]:
    """Every content page. Returns ``(files, registry, stats)``."""
    homes = frozenset(by_company)
    bodies = content.questions if content else {}

    publishable = sorted(
        (q for q in questions if q.slug in bodies and q.access_tier == "free"),
        key=lambda q: (q.number, q.slug),
    )
    registry = assign_paths(
        registry,
        [(f"question:{q.slug}", f"questions/{q.type}", _readable(q.slug, q.title)) for q in publishable],
    )
    plans, registry = plan_guides(guides, homes, content, registry, api_labels)

    local: dict[str, str] = {}
    for q in publishable:
        local[f"/questions/{q.slug}"] = question_path(registry, q)
    for plan in plans:
        if plan.path:
            # Both spellings: the site serves an article at /study/<slug>, and its
            # own guides link to one another as /questions/<slug>, which redirects.
            local[f"/study/{plan.guide.slug}"] = plan.path
            local[f"/questions/{plan.guide.slug}"] = plan.path

    files: dict[str, str] = {}
    for q in publishable:
        body = bodies[q.slug]
        path = question_path(registry, q)
        files[path] = question_page(q, body.content_md, body.hints, path, local, homes, api_labels)

    plans_by_company: dict[str | None, list[GuidePlan]] = {}
    for plan in plans:
        plans_by_company.setdefault(plan.company, []).append(plan)
    for plan in plans:
        if plan.path and content:
            key = plan.company
            name = by_company[key][0] if key else ""
            files[plan.path] = guide_page(
                plan, content.articles[plan.guide.slug], name, key, local, plans_by_company.get(key, [])
            )

    for key, (name, rows) in by_company.items():
        files[f"companies/{key}/README.md"] = company_home(
            key=key,
            name=name,
            questions=rows,
            plans=plans_by_company.get(key, []),
            content=content,
            local=local,
            registry=registry,
            today=today,
        )

    files["questions/README.md"] = questions_index(publishable, registry, api_labels)

    stats = {
        "question_pages": len(publishable),
        "guide_pages": sum(1 for p in plans if p.path),
        "dropped": content.dropped if content else 0,
    }
    return files, registry, stats
