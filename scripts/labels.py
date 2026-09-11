#!/usr/bin/env python3
"""Display names and URL slugs for companies, formats and difficulties.

The company half is a transcription of the site's own ``formatCompany`` /
``companySlug`` (``web/lib/labels.ts`` in the app repository), and it is a
transcription rather than an import because this repository has no TypeScript
toolchain and must be able to render offline from a fixture.

A transcription drifts, so it is a FALLBACK and not the source: ``company_label``
prefers the name the API itself returned for a slug (see
``catalog.fetch_company_labels``) and only reaches the rules below for a company
the API's single, capped page did not cover. The rules exist so the long tail
reads as "Capital One" rather than "Capitalone", not so this file can be the
authority on what a company is called.

``company_key`` is deliberately the same derivation the site uses for
``/problems/company/<slug>``, which means the file this repository generates for
a company and the page it links to are addressed by the same string. Two
spellings of one employer collapse onto one page here exactly as they do there.
"""

from __future__ import annotations

import re

# Tokens that stay uppercase when title-casing a slug we have no label for.
ACRONYMS = frozenset(
    {
        "ai", "ml", "ui", "ux", "ios", "qa", "sre", "api", "sde", "swe",
        "llm", "nlp", "hr", "it", "pm", "tpm", "em",
    }
)

# Slugs whose real casing or word breaks title-casing cannot recover.
COMPANY_LABELS: dict[str, str] = {
    "openai": "OpenAI",
    "xai": "xAI",
    "x-ai": "xAI",
    "deepmind": "DeepMind",
    "ibm": "IBM",
    "aws": "AWS",
    "sap": "SAP",
    "nvidia": "NVIDIA",
    "tiktok": "TikTok",
    "bytedance": "ByteDance",
    "linkedin": "LinkedIn",
    "github": "GitHub",
    "gitlab": "GitLab",
    "paypal": "PayPal",
    "youtube": "YouTube",
    "doordash": "DoorDash",
    "capitalone": "Capital One",
    "goldmansachs": "Goldman Sachs",
    "jpmorgan": "JPMorgan",
    "twosigma": "Two Sigma",
    "akunacapital": "Akuna Capital",
    "walmartlabs": "Walmart Labs",
    "sofi": "SoFi",
    "scale.ai": "Scale AI",
    "scaleai": "Scale AI",
}

# Practice formats, in the order every navigation block prints them. The order is
# the catalog's own shelf order rather than a count ranking, so the nav does not
# reshuffle itself between syncs as counts move past each other.
FORMAT_ORDER: tuple[str, ...] = ("algorithm", "sql", "system-design", "low-level-design", "ai-coding")

FORMAT_LABELS: dict[str, str] = {
    "algorithm": "Algorithm",
    "sql": "SQL",
    "system-design": "System Design",
    "low-level-design": "Low-Level Design",
    "ai-coding": "AI Coding",
}

DIFFICULTY_ORDER: tuple[str, ...] = ("easy", "medium", "hard")
DIFFICULTY_LABELS: dict[str, str] = {"easy": "Easy", "medium": "Medium", "hard": "Hard"}


def titleize(value: str) -> str:
    parts = [part for part in re.split(r"[-_\s]+", value) if part]
    return " ".join(part.upper() if part in ACRONYMS else part[:1].upper() + part[1:] for part in parts)


def company_label(company: str, api_labels: dict[str, str] | None = None) -> str:
    """The display name for a stored company value.

    Free text the catalog stores as authored (anything with a space or a capital)
    is left exactly as written — the same rule the site applies, and the reason
    "Susquehanna International Group" does not come back mangled.
    """
    raw = company.strip()
    if not raw:
        return ""
    if api_labels:
        exact = api_labels.get(raw) or api_labels.get(raw.lower())
        if exact:
            return exact
    key = raw.lower()
    if key in COMPANY_LABELS:
        return COMPANY_LABELS[key]
    if any(character.isupper() for character in raw) or " " in raw:
        return raw
    return titleize(key)


def company_key(company: str, api_labels: dict[str, str] | None = None) -> str:
    """The slug a company's page is filed under — the site's own derivation."""
    label = company_label(company, api_labels)
    slug = label.lower().replace("&", " and ")
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def format_label(question_type: str) -> str:
    return FORMAT_LABELS.get(question_type, titleize(question_type))


def difficulty_label(difficulty: str | None) -> str | None:
    if not difficulty:
        return None
    return DIFFICULTY_LABELS.get(difficulty, titleize(difficulty))
