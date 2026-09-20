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

## Adding a company to the registry

Every company page prints a line like *🔬 Semiconductors & chips · 10,000+ people · Big Tech*, and
[`company-types/`](company-types/README.md) cuts the whole bank by it — what quant firms ask, what
Big Tech asks, what a two-hundred-person startup asks. Sector and size are facts about the
**employer**, which no question carries, so they live in one hand-written file:
[`scripts/company_registry.py`](scripts/company_registry.py).

If a company here is under *Not classified*, that is the two-line fix:

```python
    "ramp": {"sector": "fintech", "size": "large"},
```

* **`sector`** is one of the ids in [`scripts/segments.py`](scripts/segments.py) — `ai`, `fintech`,
  `quant-trading`, `semiconductors`, `dev-infra`, `enterprise-saas`, `consumer-internet` and the
  rest. That file lists what each one covers.
* **`size`** is a headcount band: `mega` (10,000+), `large` (1,000–9,999), `mid` (200–999),
  `startup` (under 200).
* **The key** is the slug the company's page is filed under — `company_key("Capital One")` →
  `capital-one`. A test fails if a key is not in that form, because a key that is not canonical is
  an entry nothing will ever look up.

Three rules, and the first matters more than the other two:

1. **If you are not sure, leave it out.** `None` on either field is a legitimate answer and so is no
   entry at all. An absent company appears on every page exactly as before — it is simply in no
   company-type cut. A *wrong* one files an employer under a loop it does not run, on a page
   somebody chose deliberately. Never infer a sector from the name.
2. **Size is the company, not the office.** Alphabet's headcount for Google; a household-brand
   subsidiary (LinkedIn, Waymo) is judged on the subsidiary.
3. **"Big Tech" is not a value you can set.** It is derived — a technology-sector employer with
   10,000+ people — so if a company belongs there, the fix is its sector and its size.

The same registry, in the same shape and with the same keys, lives in the two job-list
repositories ([New-Grad-Opportunities](https://github.com/kevin-2023-code/New-Grad-Opportunities),
[Internship-Opportunities](https://github.com/kevin-2023-code/Internship-Opportunities)). A
company's sector must not depend on which repository you read it in, so a correction is worth
making in all three.

## Pull requests

PRs are welcome for the **tooling**, not the tables:

- `scripts/` — the sync engine, the renderers, the tests.
- `README.md` prose, `CONTRIBUTING.md`, `DESIGN.md`.
- `.github/` — workflows and issue forms.

Everything inside `companies/`, `company-types/`, `formats/`, `by-month/`, `guides/`, `insights/`,
`free/`, `experiences/` and `data/` is generated. The sync overwrites those directories and deletes
anything in them it did not produce.

Before opening a PR that touches `scripts/`:

```bash
python3 -m unittest discover scripts -v   # offline, runs against the fixture
python3 scripts/sync.py --check           # needs network; verifies nothing is stale
```

CI runs the first of those on every PR. Read [DESIGN.md](DESIGN.md) first — it explains the
byte budgets, why they are enforced rather than advisory, and the rules the renderers hold
(an unknown date is never printed as a date; rendering is pure and deterministic).
