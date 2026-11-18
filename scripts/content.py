#!/usr/bin/env python3
"""The well-known questions, rendered as pages you can read here.

Everything else in this repository is an index: titles, metadata and dates,
each row a link. This module renders the one part that is CONTENT, from the
bodies of the allowlisted questions (``catalog.Content``):

  * ``questions/<format>/<name>/README.md``: one page per published question,
    the statement and the hints. Never the solution: that stays on the site,
    next to the editor that runs an attempt against the hidden tests.
  * ``questions/README.md``: the index of those pages.

Three rules, each with a reason:

  * **A path, once published, does not move.** A question's folder is named
    from its slug (or, for a UUID slug, its title) the first time it appears,
    and recorded in ``data/paths.json``. A later title change leaves the file
    where it is, because a moved file is a broken link on every page that
    shared it. See :func:`assign_paths`.
  * **A body is stored once.** A question is reported at up to a dozen
    employers; its page lives under its format and every company links to it,
    rather than a dozen copies drifting apart.
  * **Links in a body land somewhere real.** The site writes site-relative
    links (``/questions/x``). Each one becomes the page here when this
    repository publishes that target, and the absolute site URL when it does
    not. See :func:`rewrite_links`.

Pure, like ``build.py``: records in, a path → text map out.
"""

from __future__ import annotations

import posixpath
import re
from typing import Sequence

from catalog import Content, Question
from labels import ROUND_LABELS, company_key, company_label, difficulty_label, format_label
from render import GENERATED_NOTICE, date_label, escape_cell, table

SITE = "https://trueinterview.io"

LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"

REPO = "https://github.com/kevin-2023-code/Tech-Interview-Questions"

#: Formats whose runs get a server-judged verdict on the site. Object-oriented
#: programming routes into the algorithm workspace, so it is judged too.
JUDGED = frozenset({"algorithm", "object-oriented-programming", "sql"})

MAX_NAME_CHARS = 72

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def slugify(text: str) -> str:
    """A file-name-safe slug, the site's own shape (``[a-z0-9-]``)."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower().replace("&", " and ")).strip("-")
    return slug[:MAX_NAME_CHARS].rstrip("-") or "untitled"


def _readable(slug: str, title: str) -> str:
    return slugify(title) if _UUID.match(slug) else slugify(slug)


# ── paths ────────────────────────────────────────────────────────────────────


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



# ── pieces ───────────────────────────────────────────────────────────────────


def _root(path: str) -> str:
    """The relative prefix from ``path`` back to the repository root."""
    depth = path.count("/")
    return "../" * depth


def footer(path: str) -> str:
    issues = f"{REPO}/issues/new?template=correction.yml"
    return (
        "---\n\n"
        f"<sub>From [TrueInterview]({SITE}): interview questions by company, with the well-known ones "
        f"in full. Text licensed [CC BY 4.0]({LICENSE_URL}): share or adapt it anywhere, "
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


def questions_index(publishable: Sequence[Question], registry: dict[str, str], api_labels: dict[str, str]) -> str:
    """``questions/README.md``: every question published here, by format, easiest first."""
    path = "questions/README.md"
    lines = [
        GENERATED_NOTICE,
        "",
        "# Interview questions with full statements",
        "",
        "The well-known questions in the TrueInterview bank (classics, and questions already public "
        "elsewhere), published here with the statement and the hints. The reference solution, and an "
        "editor that runs your attempt against the hidden tests, are one click away on each page.",
        "",
        "[All companies](../README.md#companies)",
        "",
    ]
    if not publishable:
        lines += [
            "No statement is published in this snapshot. Every question is listed under its company in "
            "[`companies/`](../README.md#companies).",
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
    homes: frozenset[str],
    content: Content | None,
    registry: dict[str, str],
    api_labels: dict[str, str],
) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, int]]:
    """Every content page.

    Returns ``(files, registry, pages, stats)``. ``pages`` maps the slug of each
    published question to its page, which is how a company row knows to link
    here instead of at the site. With no export (``content is None``) nothing
    but the index is rendered, and the registry is handed back untouched: every
    path it has ever held stays reserved.
    """
    bodies = content.questions if content else {}

    publishable = sorted(
        (q for q in questions if q.slug in bodies and q.access_tier == "free"),
        key=lambda q: (q.number, q.slug),
    )
    registry = assign_paths(
        registry,
        [(f"question:{q.slug}", f"questions/{q.type}", _readable(q.slug, q.title)) for q in publishable],
    )
    pages = {q.slug: question_path(registry, q) for q in publishable}
    local = {f"/questions/{slug}": path for slug, path in pages.items()}

    files: dict[str, str] = {}
    for q in publishable:
        body = bodies[q.slug]
        path = pages[q.slug]
        files[path] = question_page(q, body.content_md, body.hints, path, local, homes, api_labels)

    files["questions/README.md"] = questions_index(publishable, registry, api_labels)

    stats = {
        "question_pages": len(publishable),
        "dropped": content.dropped if content else 0,
    }
    return files, registry, pages, stats
