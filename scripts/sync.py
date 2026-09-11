#!/usr/bin/env python3
"""Sync this repository from TrueInterview's public catalog API.

    python3 scripts/sync.py                       # fetch live, write the repo
    python3 scripts/sync.py --check               # fetch live, fail if anything is stale
    python3 scripts/sync.py --catalog-file f.json # render from a snapshot, no network
    python3 scripts/sync.py --snapshot-out f.json # save what it fetched, for offline replay
    python3 scripts/sync.py --today 2026-09-11    # pin the clock (tests, reproducing a diff)

Three properties make this safe to run unattended on an hourly schedule:

  * **It writes nothing on a failed read.** Everything is rendered and every
    budget is checked in memory first; the filesystem is touched only once the
    whole repository is known to be renderable. A half-written sync is worse
    than no sync, because the next run would treat the damage as the base.
  * **It only writes files whose bytes actually changed.** The hourly schedule
    means most runs have nothing to do, and a run with nothing to do must leave
    no trace — otherwise the repository grows every hour whether or not anybody
    added a question.
  * **It prunes generated files the catalog no longer produces.** A company that
    loses its last question, or a shard that shrinks from three pages to two,
    leaves a file behind that nothing links to and nothing updates. Pruning is
    scoped to the generated directories by construction (see ``GENERATED_DIRS``)
    so a bad run can never reach the prose.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build import build, check_budgets  # noqa: E402
from catalog import CatalogError, DEFAULT_BASE_URL, fetch_catalog, load_catalog, snapshot  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
README_TEMPLATE = ROOT / "README.md"

# Directories whose entire contents this script owns. Anything inside them that
# a render did not produce is deleted; anything outside them is never touched.
GENERATED_DIRS = ("companies", "formats", "by-month", "guides", "data")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"Catalog origin (default: {DEFAULT_BASE_URL}).")
    parser.add_argument("--catalog-file", type=Path, help="Render from a saved snapshot instead of the network.")
    parser.add_argument("--snapshot-out", type=Path, help="Write the fetched snapshot here as well.")
    parser.add_argument("--check", action="store_true", help="Write nothing; exit 1 if any file is out of date.")
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="Pin the date freshness markers are measured against (default: today).",
    )
    parser.add_argument("--quiet", action="store_true", help="Only print the summary line.")
    return parser.parse_args()


def generated_paths() -> set[str]:
    """Every file currently sitting in a generated directory."""
    found: set[str] = set()
    for directory in GENERATED_DIRS:
        base = ROOT / directory
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                found.add(path.relative_to(ROOT).as_posix())
    return found


def main() -> int:
    args = parse_args()
    today = args.today or date.today()

    try:
        if args.catalog_file:
            payload = json.loads(args.catalog_file.read_text(encoding="utf-8"))
        else:
            payload = fetch_catalog(args.base_url)
        catalog = load_catalog(payload)
        template = README_TEMPLATE.read_text(encoding="utf-8")
        result = build(catalog, template, today)
    except (CatalogError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"sync failed: {error}", file=sys.stderr)
        return 2

    problems = check_budgets(result)
    if problems:
        for problem in problems:
            print(f"budget: {problem}", file=sys.stderr)
        return 2

    rendered = set(result.files)
    stale = sorted(generated_paths() - rendered)
    changed = sorted(
        path
        for path, content in result.files.items()
        if not (ROOT / path).exists() or (ROOT / path).read_text(encoding="utf-8") != content
    )

    stats = result.stats
    largest_bytes, largest_path = stats["largest_page"]  # type: ignore[misc]
    summary = (
        f"{stats['questions']:,} questions · "
        f"{stats['guides']:,}{'' if stats['guides_complete'] else '+'} guides "
        f"({stats['guide_companies']} companies) · {stats['companies']} companies · "
        f"{stats['months']} months · {stats['undated']:,} undated · "
        f"{stats['files']} files · README {stats['readme_bytes']:,} B · "
        f"largest page {largest_bytes:,} B ({largest_path})"
    )

    if not stats["guides_complete"]:
        print(
            f"warning: only {stats['guides']} guides were reachable — /api/v1/articles on this "
            "deployment returns one page and takes no offset. The guides index says so on the "
            "page; deploy the paged listArticles to get the rest.",
            file=sys.stderr,
        )

    future_dated = stats["future_dated"]  # type: ignore[index]
    if future_dated:
        shown = ", ".join(future_dated[:10]) + ("…" if len(future_dated) > 10 else "")
        print(
            f"warning: {len(future_dated)} question(s) carry a sighting date after {today.isoformat()} "
            f"and are rendered unmarked: {shown}",
            file=sys.stderr,
        )

    if args.check:
        print(summary)
        if changed or stale:
            for path in changed:
                print(f"out of date: {path}", file=sys.stderr)
            for path in stale:
                print(f"orphaned: {path}", file=sys.stderr)
            return 1
        print("every generated file is current")
        return 0

    for path in changed:
        target = ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result.files[path], encoding="utf-8")
    for path in stale:
        (ROOT / path).unlink()

    if args.snapshot_out:
        args.snapshot_out.parent.mkdir(parents=True, exist_ok=True)
        args.snapshot_out.write_text(
            json.dumps(
                snapshot(catalog, args.base_url),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    if not args.quiet:
        for path in changed[:40]:
            print(f"wrote {path}")
        if len(changed) > 40:
            print(f"… and {len(changed) - 40} more")
        for path in stale:
            print(f"removed {path}")
    print(summary)
    print(f"{len(changed)} file(s) written, {len(stale)} removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
