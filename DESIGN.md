# Design

This repository is generated. `scripts/sync.py` reads the public metadata API at
<https://trueinterview.io/api/v1> and writes every file under `companies/`, `data/` and
`questions/`, plus two marked blocks of `README.md`.

## Layout

```
README.md                         landing page: snapshot line, how to use, column legend, A-Z index
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

One axis only: the company. A question reported at several companies is in each of their
folders, so per-company counts sum to more than the size of the bank.

## Rules the generator holds

- **Titles, metadata and links only.** No statement, hint, solution or guide text is written
  under `companies/` or `data/`.
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
- **The count column is one the data supports.** The API has no per-question report count, so
  the CSV column is `Companies Reporting`: the number of distinct companies the question was
  reported at.
- **Rendering is pure and deterministic.** `build.build` takes records and a date and returns a
  path-to-text map. The same snapshot renders to the same bytes, so a second pass changes nothing.

## Safety checks in `sync.py`

Everything is rendered and checked in memory before the filesystem is touched.

- **Paths.** Every rendered path must match a strict pattern and sit inside `GENERATED_DIRS`
  (`companies`, `data`, `questions`). Pruning is limited to the same directories.
- **Size budgets.** `README.md` must stay under `README_MAX_BYTES` and every other page under
  `PAGE_MAX_BYTES`, both far below the 512,000 bytes GitHub will render. A company table is
  capped at `TABLE_MAX_ROWS` rows; `all.csv` has no cap.
- **Question pages.** A run without the secret refuses to start if question pages are already
  published, rather than deleting them. A run that would remove more than a fifth of them stops.
- **Failed reads.** Any API failure aborts the run with nothing written.

## Modules

| Module | Role |
| :-- | :-- |
| `catalog.py` | The only network code. Reads the API into plain records and validates them. |
| `build.py` | Assembles every generated file from one snapshot. |
| `company.py` | The company folder: the table and the four CSV files. |
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
