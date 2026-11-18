#!/usr/bin/env python3
"""What KIND of employer a company is: the sector it trades in, and its size.

── Why this exists here ─────────────────────────────────────────────────────

The bank answers "what does Stripe ask". It could not answer "what do FINTECH
companies ask", or "I am preparing for quant — which nine of these ninety-nine
companies are the quant firms", and that is the question a candidate actually
arrives with: they are preparing for a KIND of loop, not for one employer.

The pair below is the same taxonomy the job-list repositories file employers
under (`lib/segments.mjs` in `Internship-Opportunities` and
`New-Grad-Opportunities`), deliberately transcribed rather than invented twice.
That is what lets the three repositories agree: the company whose interview
loop you read about here is filed under the same sector on the list that told
you the job was open.

── The rules ────────────────────────────────────────────────────────────────

  * **An employer nobody could identify is unclassified, never guessed.** The
    registry carries an entry only where the company was actually recognised.
    Everything else answers ``(None, None)``, appears on every other page
    exactly as before, and is counted as coverage on the index. Guessing a
    sector from a company's NAME is the failure that matters: a reader who
    filters to Fintech and finds a staffing agency stops trusting the rest.
  * **Size is a band, not a number.** Headcount moves; "1,000–9,999 people"
    survives a year of it and "4,300 people" does not.
  * **"Big Tech" is DERIVED, not declared.** A technology-sector employer with
    10,000+ people — a rule a page can print, rather than a list of opinions.
    It is why Cisco is in it and a 40,000-person engineering consultancy is not.
"""

from __future__ import annotations

from dataclasses import dataclass

from company_registry import COMPANY_SEGMENTS
from labels import company_key


@dataclass(frozen=True)
class Sector:
    id: str
    emoji: str
    label: str
    #: Whether the size cuts are taken over this sector — see ``BIG_TECH_SIZES``.
    tech: bool


SECTORS: tuple[Sector, ...] = (
    Sector("ai", "🧠", "AI labs & AI infrastructure", True),
    Sector("consumer-internet", "📱", "Consumer internet & media", True),
    Sector("ecommerce-marketplace", "🛒", "E-commerce & marketplaces", True),
    Sector("dev-infra", "☁️", "Developer tools, cloud & data infrastructure", True),
    Sector("enterprise-saas", "🏢", "Enterprise & business software", True),
    Sector("security", "🔒", "Cybersecurity", True),
    Sector("fintech", "💳", "Fintech, payments & crypto", True),
    Sector("quant-trading", "📈", "Quant trading & hedge funds", False),
    Sector("banking-finance", "🏦", "Banks, insurers & asset managers", False),
    Sector("semiconductors", "🔬", "Semiconductors & chips", True),
    Sector("hardware-devices", "🖥️", "Hardware, devices & networking", True),
    Sector("aerospace-defense", "🚀", "Aerospace & defence", False),
    Sector("autonomy-mobility", "🚗", "Autonomy, automotive & mobility", False),
    Sector("gaming", "🎮", "Gaming & interactive", True),
    Sector("health-bio", "🧬", "Health, biotech & medical devices", False),
    Sector("energy-industrial", "⚡", "Energy, climate & industrial", False),
    Sector("engineering-services", "📐", "Engineering & architecture firms", False),
    Sector("it-consulting", "🧾", "IT services & consulting", False),
    # Named for what it actually holds: the old label read as *research* over a
    # homelessness charity, while a non-profit school chain sat in Other
    # industries — two charities, two sectors, and a reader sees that as
    # arbitrary.
    Sector("public-research", "🏛️", "Government, research & non-profits", False),
    Sector("other-industry", "💼", "Other industries", False),
)

SECTOR_BY_ID: dict[str, Sector] = {sector.id: sector for sector in SECTORS}

#: Headcount bands, largest first, with the words a page prints.
SIZES: tuple[tuple[str, str], ...] = (
    ("mega", "10,000+ people"),
    ("large", "1,000–9,999 people"),
    ("mid", "200–999 people"),
    ("startup", "Under 200 people"),
)

SIZE_LABELS: dict[str, str] = dict(SIZES)


def segment_of(company: str, api_labels: dict[str, str] | None = None) -> tuple[str | None, str | None]:
    """``(sector, size)`` for a stored company value, or ``(None, None)``.

    Total: a company the registry has never heard of answers with the empty
    pair rather than raising, and an entry naming a sector this file does not
    define is read as unclassified rather than trusted.
    """
    key = company_key(company, api_labels)
    entry = COMPANY_SEGMENTS.get(key)
    if not entry:
        return None, None
    sector = entry.get("sector")
    size = entry.get("size")
    return (
        sector if sector in SECTOR_BY_ID else None,
        size if size in SIZE_LABELS else None,
    )


def is_big_tech(sector: str | None, size: str | None) -> bool:
    """The derived cut: a technology-sector employer with 10,000+ people."""
    return bool(sector) and SECTOR_BY_ID[sector].tech and size == "mega"


def sector_chip(sector: str | None, size: str | None) -> str:
    """The one line a company page prints about the employer itself.

    Two facts or nothing: a page that says "🔬 Semiconductors & chips" and
    stays silent about the size is saying everything it knows, and a page that
    says neither prints no chip at all rather than an empty one.
    """
    parts = []
    if sector:
        entry = SECTOR_BY_ID[sector]
        parts.append(f"{entry.emoji} {entry.label}")
    if size:
        parts.append(SIZE_LABELS[size])
    if sector and is_big_tech(sector, size):
        parts.append("Big Tech")
    return " · ".join(parts)
