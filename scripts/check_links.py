#!/usr/bin/env python3
"""Check that the site links this repository prints actually resolve.

    python3 scripts/check_links.py                 # every site link in README.md
    python3 scripts/check_links.py latest formats  # ... and in these files or folders
    python3 scripts/check_links.py --limit 200     # stop after this many distinct links

The renderers are tested offline: they prove a row links at the URL the catalog
gave for that question, and nothing else. They cannot prove the page is there.
This does, with one ``HEAD`` per distinct link, paced, and it is the only code
besides ``catalog.py`` that touches the network.

Three outcomes per link, and only one of them fails the run:

  * **ok**: the page answered 200 (redirects are followed).
  * **dead**: 404, 410, any other 4xx this script was not refused with, a 5xx,
    or no answer at all. One dead link exits 1.
  * **unverified**: 401, 403 or 429. The site refused the checker, which says
    nothing about the page. Counted and printed, never reported as dead and
    never reported as ok.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://trueinterview.io"
USER_AGENT = "trueinterview-question-bank-sync/1 (+https://github.com/kevin-2023-code/Tech-Interview-Questions)"
PAUSE_SECONDS = 0.25
REFUSED = frozenset({401, 403, 429})

#: A markdown link target on the site: everything up to the closing parenthesis.
SITE_LINK = re.compile(r"\]\((" + re.escape(SITE) + r"[^)\s]*)\)")


def site_links(text: str) -> list[str]:
    """Every distinct site link in a markdown document, in first-seen order."""
    return list(dict.fromkeys(SITE_LINK.findall(text)))


def markdown_files(targets: Iterable[str]) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        path = (ROOT / target).resolve()
        if path.is_dir():
            files.extend(sorted(path.rglob("*.md")))
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(target)
    return files


def classify(status: int | None) -> str:
    """``ok``, ``dead`` or ``unverified`` for one response status (``None`` = no answer)."""
    if status == 200:
        return "ok"
    if status in REFUSED:
        return "unverified"
    return "dead"


def head(url: str, timeout: int = 30) -> int | None:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def check(urls: Iterable[str], fetch=head, pause: float = PAUSE_SECONDS) -> dict[str, list[tuple[str, int | None]]]:
    results: dict[str, list[tuple[str, int | None]]] = {"ok": [], "dead": [], "unverified": []}
    for url in urls:
        status = fetch(url)
        if status is None or status >= 500:
            # One retry for a blip, so a single timeout does not fail the run.
            time.sleep(pause * 4)
            status = fetch(url)
        results[classify(status)].append((url, status))
        time.sleep(pause)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="*", default=["README.md"], help="Files or folders to read links from.")
    parser.add_argument("--limit", type=int, default=0, help="Check at most this many distinct links (0 = all).")
    args = parser.parse_args()

    urls: list[str] = []
    for path in markdown_files(args.targets):
        urls.extend(site_links(path.read_text(encoding="utf-8")))
    urls = list(dict.fromkeys(urls))
    if args.limit:
        urls = urls[: args.limit]
    if not urls:
        print("link check failed: no site links found, which means the wrong files were read", file=sys.stderr)
        return 2

    results = check(urls)
    for url, status in results["dead"]:
        print(f"dead: {status or 'no answer'} {url}", file=sys.stderr)
    for url, status in results["unverified"][:10]:
        print(f"unverified: {status} {url}", file=sys.stderr)
    print(
        f"{len(urls)} links checked · {len(results['ok'])} ok · {len(results['dead'])} dead · "
        f"{len(results['unverified'])} unverified (the site refused the checker)"
    )
    return 1 if results["dead"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
