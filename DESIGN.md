# Design

This repository is generated. `scripts/sync.py` reads the public metadata API at
<https://trueinterview.io/api/v1> and writes every file under `companies/`, `data/`, `formats/`,
`latest/` and `questions/`, plus the marked blocks of `README.md`.

## Layout

```
README.md                         landing page: season title, stats line, new this week, recently
                                  reported, AI coding rounds, format links, company table
latest/README.md, page-N.md       every question reported in the past year, newest first
formats/<format>.md               the same table for one format
companies/<key>/README.md         the company's questions as one table, most recently reported first
companies/<key>/thirty-days.csv   questions last reported in the past 30 days
companies/<key>/three-months.csv  ... in the past 90 days
companies/<key>/six-months.csv    ... in the past 180 days
companies/<key>/all.csv           every question reported at the company
questions/README.md               index of the questions published with a statement
questions/<format>/<name>/README.md   one such question: statement and hints
data/questions.csv, .jsonl        the whole bank, one row per question, sorted by slug
data/paths.json                   the folder each published question was given
scripts/                          the generator and its tests
```

Two ways in. By company: a question reported at several companies is in each of their folders, so
per-company counts sum to more than the size of the bank. By date: `README.md`, `latest/` and
`formats/` list each question once, newest first, whichever companies reported it.

## Rules the generator holds

- **Titles, metadata and links only.** No statement, hint, solution or guide text is written
  under `companies/`, `data/`, `formats/` or `latest/`, or into `README.md`. Every row of the date
  index links to the question's page on the site.
- **Statements are published for an allowlist only.** `scripts/common_questions.txt` lists the
  well-known questions. Their statement and hints are read from a separate export that needs the
  `CONTENT_EXPORT_SECRET` secret; `catalog.common_only` discards everything else in that export,
  every article included, before anything else sees it. With the secret unset no question page
  is rendered at all. Solutions are never published.
- **A published path never moves.** A question's folder is recorded in `data/paths.json` the
  first time it is rendered and kept from then on, and a retired path is never reused.
- **An unknown date is never printed as a date.** A question with no reported date is in
  `all.csv` only. So is one dated after the snapshot, which is a mistyped date upstream.
  A month-only date counts from the first of that month.
- **The last reported date is the site's.** The API sends `reported` (the question's own
  sighting, to the day when known) and `lastReported` (a month: the later of that and the newest
  interview write-up naming the question). The later month wins and is printed as a month; inside
  the same month the day is kept (`catalog.latest_sighting`).
- **Reported and added are different dates.** *New this week* and the "+N added" figure count rows
  by the day they entered the bank and say so; every other date is a reported date.
- **The count columns are ones the data supports.** `Companies Reporting` is the number of distinct
  companies the question was reported at. `Reports` is the API's `reportCount`: interview write-ups
  from the past 12 months that name the question, the counter the site's own lists rank on. A
  snapshot with no counts renders no Reports column in any table, and an unknown count is never
  printed as 0.
- **Site links from tables carry a source tag.** `render.site_link` appends
  `utm_source=github&utm_medium=repo&utm_campaign=tech-interview-questions`. The CSV and JSONL
  exports keep the bare canonical URL.
- **A window with nothing in it says so.** *New this week* prints a sentence, not older rows.
- **Rendering is pure and deterministic.** `build.build` takes records and a date and returns a
  path-to-text map. The same snapshot renders to the same bytes, so a second pass changes nothing.

## Safety checks in `sync.py`

Everything is rendered and checked in memory before the filesystem is touched.

- **Paths.** Every rendered path must match a strict pattern and sit inside `GENERATED_DIRS`
  (`companies`, `data`, `questions`). Pruning is limited to the same directories.
- **Size budgets.** `README.md` must stay under `README_MAX_BYTES` and every other page under
  `PAGE_MAX_BYTES`, both far below the 512,000 bytes GitHub will render. A company table is
  capped at `TABLE_MAX_ROWS` rows; `all.csv` has no cap. The README tables, the `latest/` pages
  and the format pages are capped in rows in `season.py`.
- **Links.** `scripts/check_links.py` sends one `HEAD` per distinct site link in `README.md`
  once a day and fails the run on a dead one. A refusal (401, 403, 429) is reported as
  unverified, neither dead nor ok.
- **Question pages.** A run without the secret refuses to start if question pages are already
  published, rather than deleting them. A run that would remove more than a fifth of them stops.
- **Failed reads.** Any API failure aborts the run with nothing written.

## Modules

| Module | Role |
| :-- | :-- |
| `catalog.py` | The only network code in the sync. Reads the API into plain records and validates them. |
| `build.py` | Assembles every generated file from one snapshot. |
| `company.py` | The company folder: the table and the four CSV files. |
| `season.py` | The date index: the README blocks, `latest/` and `formats/`. |
| `check_links.py` | The daily link check. The only other network code. |
| `content.py` | Question pages, their index, stable paths, link rewriting. |
| `render.py` | Shared helpers: escaping, ordering, tables, the `data/` exports. |
| `labels.py` | Display names and folder keys for companies, formats, difficulties, rounds. |
| `sync.py` | Command line: fetch, render, check, write, prune. |

## Running it

```bash
python3 -m unittest discover scripts            # offline, against scripts/fixtures
python3 scripts/sync.py                         # fetch live and write
python3 scripts/sync.py --snapshot-out s.json   # also save what was fetched
python3 scripts/sync.py --catalog-file s.json --check   # verify nothing is stale
```

`.github/workflows/sync.yml` runs the tests, the sync and the `--check` pass on a schedule and
commits the result. `.github/workflows/verify.yml` runs the tests and a fixture render on pull
requests.
