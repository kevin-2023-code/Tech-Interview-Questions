<div align="center">

# 2026 & 2027 Tech Interview & OA Questions

**Real Online Assessment and interview questions — and how each company actually runs its loop.**

<!-- gen:stats:start -->
Run `python3 scripts/sync.py` to populate.
<!-- gen:stats:end -->

[**▶ Practice these questions**](https://trueinterview.io/problems) &nbsp;·&nbsp;
[**Report a question**](../../issues/new?template=question-report.yml) &nbsp;·&nbsp;
[Fix a row](../../issues/new?template=correction.yml) &nbsp;·&nbsp;
[Contributing](CONTRIBUTING.md)

</div>

---

Every title here opens the real problem on [TrueInterview](https://trueinterview.io) — a
runnable workspace and a server-judged verdict, not a screenshot. Synced from the live
catalog every hour.

## 📖 How each company interviews

<!-- gen:guides:start -->
Run `python3 scripts/sync.py` to populate.
<!-- gen:guides:end -->

Round-by-round writeups: the recruiter screen, the hiring-manager round, the culture
interview, the project deep-dive. **Read the loop before you practise for it** — each
company's page carries its guides above its questions.

## 🏢 Browse by company

<!-- gen:companies:start -->
Run `python3 scripts/sync.py` to populate.
<!-- gen:companies:end -->

## 🧩 Browse by format

<!-- gen:formats:start -->
Run `python3 scripts/sync.py` to populate.
<!-- gen:formats:end -->

## 📅 Browse by month reported

<!-- gen:months:start -->
Run `python3 scripts/sync.py` to populate.
<!-- gen:months:end -->

---

## 🔥 Latest sightings

<sub>🔥 reported in the last 14 days &nbsp;·&nbsp; 🆕 in the last 45 &nbsp;·&nbsp; a dash
means no sighting date was recorded, which is not the same as old.</sub>

<!-- gen:latest:start -->
Run `python3 scripts/sync.py` to populate.
<!-- gen:latest:end -->

<sub>The newest slice, not the bank — use the navigation above for the rest. Every question
is on a company page, a format page, and (if it carries a sighting date) a month page.</sub>

---

## 💾 Take the data

| | |
| :-- | :-- |
| [`data/questions.jsonl`](data/questions.jsonl) | one JSON object per line, slug-sorted |
| [`data/questions.csv`](data/questions.csv) | the same rows as a spreadsheet |
| [`data/companies.csv`](data/companies.csv) | every company and its question count |
| [Live API](https://trueinterview.io/developers/api) | what this repo is generated from |

## ⚙️ How it stays current

Everything under `companies/`, `formats/`, `by-month/`, `guides/` and `data/` is generated
hourly by [`scripts/sync.py`](scripts/sync.py) from TrueInterview's public, documented,
read-only catalog API. That API publishes **metadata only** — a title, a company, a format,
a difficulty, a sighting month, a URL. No problem statements, no solutions, no test cases,
and neither does this repository: what you get here is the index, and the questions
themselves live on the site.

```bash
python3 scripts/sync.py          # refresh from the live catalog
python3 scripts/sync.py --check  # verify nothing is stale
python3 -m unittest discover scripts
```

Design notes, the byte budgets and the rules the renderers hold: **[DESIGN.md](DESIGN.md)**.

## 🤝 Contributing

Sightings keep this current. [Report a
question](../../issues/new?template=question-report.yml) or [fix a
row](../../issues/new?template=correction.yml) — read
[CONTRIBUTING.md](CONTRIBUTING.md) first. Short version: **a summary in your own words,
never a screenshot or a copied problem statement.**

## 📄 Licence

The tracker — these scripts and the index they generate — is MIT ([LICENSE](LICENSE)). The
questions and guides it points at belong to TrueInterview and are published on the site
under its own terms.
