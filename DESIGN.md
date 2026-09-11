# How this repository works, and why it is shaped this way

This is a public index over TrueInterview's question bank. It exists to be found — someone
searching "Google OA questions github" should land on a page that answers them and then
takes them somewhere they can actually solve the thing.

The format was arrived at by studying the repository that owns that search result today,
[`perixtar/Tech-OA-Interview-Questions`](https://github.com/perixtar/Tech-OA-Interview-Questions),
taking the parts that work, and fixing the one that does not.

---

## 1. What the prior art actually does

Measured against the repository at commit `92d0a83` (196 commits, ~2,260 rows).

**The content model.** One markdown table in `README.md`, five columns: Company, Question,
Format, Practice, Updated. Every row links **twice** to the same URL — once as the question
title, once as a "Practice" button rendered from a local SVG. Rows are sorted newest-first.
`formats/*.md` carry the same rows filtered by format.

**The freshness mechanic — the smartest thing in it.** A date alone is inert. They put a 🔥
on anything reported in the last 14 days and a 🆕 on the last 45, and then re-run the
markers *hourly*. The table therefore keeps changing even in a week when nothing new is
added: markers expire, rows re-sort, GitHub shows "updated 40 minutes ago". Freshness is the
whole product for an OA tracker, and they made it visible on the row and in the repo
metadata at the same time. Worth copying outright.

**The sync.** An hourly GitHub Action hits four public JSON endpoints on their own site,
upserts rows, regenerates the format pages, runs `--check` plus a unit test, and commits only
when something changed. The site is the source of truth; the repo is a projection. Also
worth copying outright.

**The contribution funnel.** An issue form for new sightings, a Discord link, and a
CONTRIBUTING that draws a careful line: summaries yes, screenshots and copied problem
statements no. That line is doing real work — it is what makes the repository publishable
at all — and it is copied here nearly verbatim in substance.

**The reference-style link trick.** `[p]: assets/practice-button.svg` is declared once and
used 2,260 times, instead of repeating the path on every row. A small, correct instinct.

**What it is for.** The repo is a funnel. Two links per row into `fastprep.io`, a Practice
button as the visual call to action, and the question titles themselves as the SEO surface.
That is a legitimate design and this repository has the same goal.

## 2. The one thing that is broken

**`README.md` is 500,381 bytes. GitHub stops rendering a markdown file at 512,000 bytes.**

They are 11,619 bytes — roughly fifty more rows, about two weeks of their own commit rate —
from the table silently truncating on the landing page. Their own script knows it:

```python
# GitHub truncates rendered README input at 512,000 bytes. Keep enough margin
# for routine table additions between maintenance passes.
README_MAX_BYTES = 500_000
```

…and the file is *already over that self-imposed line*. The guard now fails the sync rather
than preventing the problem. There is no room left to keep.

This is not only a cliff at the end; it is a tax on every visit up to it:

| | Measured |
| :-- | --: |
| `README.md` | 500,381 B |
| Table rows on the landing page | 2,260 |
| External links resolved on one page load | ~4,520 |
| `formats/coding.md` | 377,472 B |
| `git clone` size | 1.9 MB |

Every visitor downloads half a megabyte of markdown and waits for GitHub to lay out a
2,260-row table, on a phone as well as a laptop, before they can find the one company they
came for. And the git cost compounds: the hourly job re-sorts the table, so a single new row
rewrites *every line below it*, and each of the 196 commits stores a fresh ~500 KB blob.
The repository grows whether or not anyone adds anything.

The root cause is a structural choice, not a bug: **one file holds the whole catalog, and it
is also the landing page.** Those two jobs have opposite requirements.

## 3. What this repository does instead

**The README is an index; rows live in shards.**

- `README.md` — navigation, plus the newest 100 sightings. Capped at 96,000 bytes, which is
  under a fifth of GitHub's limit.
- `companies/<slug>.md` — the page most visitors actually want. Drops the Company column,
  because the file *is* the company.
- `formats/<slug>.md` — drops the Format column, same reason.
- `by-month/YYYY-MM.md` — what was being asked in a given month.
- Every page is capped at 192,000 bytes and paginates into `-2.md`, `-3.md` before it gets
  close, so no single file can ever reach the truncation limit however large the bank grows.

The caps are enforced. `scripts/sync.py` renders the whole repository in memory, checks every
budget, and **writes nothing at all if one is exceeded** — it fails the run instead. A
scheduled job that quietly published a truncated landing page at 3am is exactly the failure
this design exists to prevent, so it is the one the tooling refuses to have.

**Shards also fix the git cost.** A new question touches its company page, its format page
and its month page — three small files — instead of rewriting one 500 KB table. The data
exports are sorted **by slug and written one record per line**, so a changed question is a
one-line diff that git can delta-compress, rather than a re-emitted blob.

**No image buttons on the row.** A per-row `<img>` goes through GitHub's `camo` proxy, and
the reference repo's landing page carries 2,260 of them. The question title is already a
link; a second link to the same URL is a second request and no extra information. One small
local asset is used in the header and nowhere else.

**Machine-readable output.** `data/questions.jsonl`, `data/questions.csv` and
`data/companies.csv` mean nobody has to parse markdown to use this. The reference repo has
no export at all, which is odd for a dataset whose whole value is being a dataset.

**The freshness mechanic, kept.** 🔥 ≤14 days, 🆕 ≤45 days, hourly. It is the best idea in
the prior art.

**But an unknown date is never printed as a date.** This is the one place the two designs
disagree on substance rather than performance. The reference repo gives every row a date,
falling back to a sync timestamp when it has no sighting. Here, a question with no recorded
sighting renders `—`, sorts last, is never marked fresh, and is **absent from `by-month/`** —
with the count of excluded rows stated on that index, so the omission is visible rather than
quiet. A month page is a claim that these questions were asked in that month. A row we have
no sighting for cannot stand behind that claim, and the date a question was *imported* is a
fact about us, not about anybody's interview.

## 4. The pipeline

```
trueinterview.io/api/v1/questions   ──┐
trueinterview.io/api/v1/companies   ──┤
                                      ├─► scripts/catalog.py   fetch + validate → records
                                      ├─► scripts/labels.py    display names, slugs
                                      ├─► scripts/render.py    cells, tables, shards, exports
                                      ├─► scripts/build.py     assemble every file (pure)
                                      └─► scripts/sync.py      budgets, diff, write, prune
```

`catalog.py` is the only module that touches the network. `build.py` and `render.py` are
pure functions of `(records, date)`, which is what lets the test suite render the entire
repository from a fixture and compare bytes, with no network and no clock.

**Metadata, never bodies.** The API this reads publishes titles, companies, formats,
difficulties, sighting months and URLs — and deliberately no problem statements, no
solutions and no test cases. This repository carries exactly what that API carries. The
distinction is the whole reason an index like this can be public: it is a pointer into the
site, not a copy of it.

### Properties worth keeping when you change something

- **A read that failed is never an empty catalog.** Every failure raises; the script exits
  non-zero and writes nothing. An hourly job that rendered zero questions after a blip would
  commit an empty repository over a good one, at an hour nobody is watching.
- **Rendering is deterministic.** Sorting is a total order, so two rows cannot swap places
  between runs — every swap would otherwise be a committed diff on a file nobody changed.
- **Only changed bytes are written, and orphans are pruned.** A company that loses its last
  question leaves a file nothing links to and nothing updates; pruning is scoped to the four
  generated directories by construction, so a bad run cannot reach the prose.
- **Company counts sum to more than the bank.** A question reported at three employers is on
  three company pages. Every surface that prints a company count says what it is counting.

## 5. Known gaps

- **`/api/v1/companies` cannot page.** It reports `total: 99` but takes no offset and caps at
  50, so only the busiest 50 display names come from the API. `scripts/labels.py` transcribes
  the site's own `formatCompany` rules as a fallback for the tail — correct today, and a
  transcription that will drift. Adding a `page` parameter to that operation on the site
  would remove the need for the fallback entirely.
- **Contribution is one-way.** Reported sightings arrive as issues and a maintainer enters
  them on the site, which is then synced back here. That is deliberate for now: an automatic
  path from a GitHub issue into the live question bank is a write path into candidate-facing
  data, and it needs its own review.
