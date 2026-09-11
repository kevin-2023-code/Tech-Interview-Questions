# Contributing

This tracker is worth reading only if the sightings in it are specific, recent and safe to
publish. That is what contributions are for.

## What to send

- **A question you were actually asked** — in an OA, a phone screen or an onsite.
- **A correction** — wrong company, wrong format, wrong date, dead link, duplicate row.
- **A missing sighting date** on a row that has none. These render as `—` and are excluded
  from the monthly pages; a date fixes both.

## What NOT to send

Please do not send, and we will not publish:

- Screenshots of an assessment, or any account-only assessment page.
- A full copied problem statement.
- Private recruiter messages, or anything under an NDA you signed.
- Another site's problem text.

**A short summary in your own words is enough.** Say what the task was — "given a grid of
scores, count how many fall into each category" — and a maintainer can verify it and build a
practice-safe version. That line is not a formality: it is what makes this repository
publishable at all, and a submission that crosses it gets closed rather than edited.

## How to send it

Open an issue:

- [**Report a question**](../../issues/new?template=question-report.yml)
- [**Fix a row**](../../issues/new?template=correction.yml)

One issue per question, so each can be verified and tracked on its own.

Useful to include: company, role and season, the round it was in, the date you saw it, and
where it came from (your own interview, a public post, a friend's report).

## What happens next

A maintainer verifies the report and, once it is confirmed, adds it to the question bank on
[TrueInterview](https://trueinterview.io). **The site is the source of truth** — this
repository is generated from the site's public catalog API, hourly, by
[`scripts/sync.py`](scripts/sync.py). Your sighting appears here on the next sync after it
lands there.

That is why there is no "edit the table" pull request path: a hand-edited row would be
overwritten by the next sync within the hour. Send the sighting, not the diff.

## Pull requests

PRs are welcome for the **tooling**, not the tables:

- `scripts/` — the sync engine, the renderers, the tests.
- `README.md` prose, `CONTRIBUTING.md`, `DESIGN.md`.
- `.github/` — workflows and issue forms.

Everything inside `companies/`, `formats/`, `by-month/`, `guides/` and `data/` is generated. The sync
overwrites those directories and deletes anything in them it did not produce.

Before opening a PR that touches `scripts/`:

```bash
python3 -m unittest discover scripts -v   # offline, runs against the fixture
python3 scripts/sync.py --check           # needs network; verifies nothing is stale
```

CI runs the first of those on every PR. Read [DESIGN.md](DESIGN.md) first — it explains the
byte budgets, why they are enforced rather than advisory, and the rules the renderers hold
(an unknown date is never printed as a date; rendering is pure and deterministic).
