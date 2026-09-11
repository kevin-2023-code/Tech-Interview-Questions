# 2026 & 2027 Tech Interview & OA Questions

Real Online Assessment and interview questions, tracked by company, format and the month
they were reported — and every one of them opens a **runnable, server-judged workspace** on
[TrueInterview](https://trueinterview.io), not a dead link to a screenshot.

<!-- gen:stats:start -->
Run `python3 scripts/sync.py` to populate.
<!-- gen:stats:end -->

[**Practice these questions →**](https://trueinterview.io/problems) ·
[Report a question](../../issues/new?template=question-report.yml) ·
[Fix a row](../../issues/new?template=correction.yml) ·
[Contributing](CONTRIBUTING.md) ·
[How this repo works](DESIGN.md)

---

## Browse

<!-- gen:formats:start -->
Run `python3 scripts/sync.py` to populate.
<!-- gen:formats:end -->

<!-- gen:months:start -->
Run `python3 scripts/sync.py` to populate.
<!-- gen:months:end -->

<!-- gen:companies:start -->
Run `python3 scripts/sync.py` to populate.
<!-- gen:companies:end -->

**Raw data:** [`data/questions.jsonl`](data/questions.jsonl) ·
[`data/questions.csv`](data/questions.csv) ·
[`data/companies.csv`](data/companies.csv) ·
[live API](https://trueinterview.io/developers/api)

---

## Latest sightings

<sub>🔥 reported in the last 14 days · 🆕 in the last 45 · a dash means no sighting date was
recorded, which is not the same as old.</sub>

<!-- gen:latest:start -->
Run `python3 scripts/sync.py` to populate.
<!-- gen:latest:end -->

<sub>This is the newest slice, not the bank. Use the navigation above for the rest — every
question is on a company page, a format page, and (if it carries a sighting date) a month
page.</sub>

---

## Why this repo loads fast

Most question-bank repositories put the whole catalog in one markdown table in the README.
That has a ceiling nobody sees coming: **GitHub stops rendering a markdown file at 512,000
bytes** and prints a truncation notice instead of the rest — and the page gets slower the
whole way up to it, because every visitor downloads and lays out thousands of table rows
before they have found anything.

So here the README is an **index**, and the rows live in shards:

| | This repo | One-giant-README |
| :-- | :-- | :-- |
| Landing page | an index, hard-capped well under GitHub's limit | grows until it is truncated |
| Rows | sharded by company, format and month, paginated | one table, every row |
| Row cost | 3 small files touched per new question | the whole table rewritten |
| Machine-readable | JSONL + CSV, one record per line | parse the markdown |
| Undated rows | shown as `—` | given a date they do not have |

The budgets are enforced, not aspirational: `scripts/sync.py` refuses to write anything if
the README or any generated page would exceed its cap. Details and the measurements are in
[DESIGN.md](DESIGN.md).

## Where the data comes from

Every row is synced hourly from TrueInterview's public, documented, read-only catalog API
([spec](https://trueinterview.io/openapi.json) ·
[reference](https://trueinterview.io/developers/api)) by
[`scripts/sync.py`](scripts/sync.py). That API publishes **metadata only** — a title, a
company, a format, a difficulty, a sighting month, a URL. It carries no problem statements,
no solutions and no test cases, and neither does this repository: what you get here is the
index, and the questions themselves live on the site.

```bash
python3 scripts/sync.py            # refresh everything from the live catalog
python3 scripts/sync.py --check    # verify nothing is stale (CI runs this)
python3 -m unittest discover scripts -v
```

## Contributing

Sightings are what keep this current. Open an issue — [report a
question](../../issues/new?template=question-report.yml) or [fix a
row](../../issues/new?template=correction.yml) — and read
[CONTRIBUTING.md](CONTRIBUTING.md) first for what is and is not safe to send. Short version:
**a summary in your own words, never a screenshot or a copied problem statement.**

## Licence

The tracker — the scripts and the generated index — is MIT (see [LICENSE](LICENSE)). The
questions, statements and solutions it points at belong to TrueInterview and are published
on the site under its own terms.
