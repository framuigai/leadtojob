# leadTo ➡️ Job Satisfaction Analysis System

An organizational diagnostic: four domains, twelve subdomains, 48 items, one
anonymous link. Powered by Technolysi Limited.

- **Spec:** `docs/LEADTO ➡️ JOB SATISFACTION ANALYSIS SYSTEM.pdf` (v3.0)
- **Build plan:** `docs/jobsat build plan.md` (v2)
- **Context and the rules that are not preferences:** `CLAUDE.md` — read first
- **Scoring rule (§10.2 documentation requirement):** `docs/scoring_rule.md`

## Local development

```bash
python -m venv venv
venv/Scripts/activate            # Windows
pip install -r requirements.txt
cp .env.example .env             # then fill it in
python run.py                    # http://localhost:5000
```

Requires **local PostgreSQL 16**, databases `jobsat_dev` and `jobsat_test`.
Never SQLite — see `CLAUDE.md`.

```bash
alembic upgrade head             # apply the schema
pytest -q                        # DB-backed tests skip without TEST_DATABASE_URL
```

## Status

| Phase | | |
|---|---|---|
| 1 | Scaffolding | ✅ done |
| 2 | Identity, tenancy & live dashboards | ✅ done |
| 3 | Taxonomy & instrument | ✅ done |
| 4 | Cycles & segments | ✅ done |
| 5 | Deployment & the anonymous form | ✅ done |
| 6 | Scoring engine | ✅ done |
| 7 | Association engine & Reports screen | ✅ done |
| 8 | Reports 1 & 2 (Executive, HR & OD) | next |
| 9–11 | see `docs/jobsat build plan.md` §11 | |

## How associations are reported

§10.4 is the only section specifying this, and it specifies **questions**, not
a statistic. The words "correlation", "Pearson" and "coefficient" appear
nowhere in the specification, and §10.5 asks for *"scores, response
distributions, comparisons and limitations"* during early implementation.

So the engine computes **tertile contrasts** (the least vs most satisfied
third, per diagnostic domain) and **segment comparisons** — which answer
§10.4's four bullets in its own words — and no correlation coefficient.
`association.correlation()` exists only to raise and explain why.

## The reporting threshold

`MIN_REPORTING_N` is **1 in development and testing** (so small fixtures
produce visible numbers) and **5 in production**, set per environment in
`config.py` and overridable by environment variable.

Whatever it is set to, the survey landing page renders a claim that MATCHES it
— at 1 it tells respondents plainly that a result may rest on their answers
alone. See `reporting_promise()` in `app/survey/routes.py`.

## Seeding a fresh database

```bash
alembic upgrade head
python seed_templates.py     # the 48 master items
python seed_admin.py         # the L1 master admin, from SEED_ADMIN_* in .env
```

Both are idempotent: re-running reports what exists rather than duplicating it.
