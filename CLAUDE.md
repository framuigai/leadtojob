# leadTo ➡️ Job Satisfaction Analysis System — Project Context

Read this before changing anything. It records decisions that are already
settled, so they are not re-litigated every session.

**Source of truth:** `docs/LEADTO ➡️ JOB SATISFACTION ANALYSIS SYSTEM.pdf`
(Master Product Concept v3.0, §1–§18).
**Build plan:** `docs/jobsat build plan.md` (v2).
**Product:** `jobs.leadto.co.ke` · **Service:** `leadtojobsat` on Render.

---

## The leadTo ecosystem — three products, three subdomains

| Product | Subdomain | Unit of analysis | Respondent |
|---|---|---|---|
| TNA (trainiq) | `tna.leadto.co.ke` | (org\|branch, domain, cycle) | anonymous, pooled |
| Promotion Readiness (promo) | `promote.leadto.co.ke` | (position, candidate, instrument) | **identified** |
| **Job Satisfaction (this)** | `jobs.leadto.co.ke` | (cycle, group) over **anonymous sessions** | anonymous, **grouped per respondent** |

They share a visual language and a chassis. They do not share a database, a
schema or an Alembic chain, and no data crosses between them.

---

## When in doubt

**Precedence: the spec PDF > `docs/jobsat build plan.md` > trainiq/promo
precedent.** This inverts promo's order deliberately — promo had 21 build
guidelines that post-dated its PDF, and this product has no such layer. The PDF
is the only authority; the siblings are a chassis, not a spec.

If a trainiq or promo pattern seems to fit, check whether it assumes an
**identified respondent**. If it does, it is probably wrong here. This product
pools anonymous sessions, and that difference is where the bugs will be.
(Promo's own CLAUDE.md says the opposite, for the opposite reason. Both are
correct about their own product; that is the point.)

---

## The architecture in one paragraph

Four domains (**GEM** Overall Satisfaction, **TID** Task, **BIT**
Relationships, **PIE** Reward), twelve subdomains, four questions each — 48
items, one anonymous link, one five-point agreement scale plus N/A. GEM is the
**outcome**; TID, BIT and PIE are **diagnostic** and are reported *in relation
to* GEM. The platform shows scores, distributions and associations at group
level under minimum reporting thresholds, and routes findings to an
intervention category — without declaring causation, without a validated band
label, and without ever exposing an individual respondent.

---

## The five rules that are not preferences

Each is a specification requirement. Breaking one is a bug, not a style
disagreement.

### 1. The respondent is never identified (§9.2, §13)

`response_sessions` holds **no identity and no FK to any identity** — no email,
no employee number, no name, **no IP address**, no user-agent, no link to
`users`. There is no screen, export or API that can return "who said this",
because the information was never collected.

That guarantee is a property of the **schema**, not of a permission check,
which is the only form of it worth making.

But `survey_responses` **does** carry `session_id`, so one respondent's answers
stay grouped. That is what makes §10.4 possible. **Anonymous but not
aggregate-only** — an aggregate-only design makes §10.4 impossible, an
identified design makes §13 impossible, and rows keyed to an anonymous session
is the only shape that satisfies both.

Do not resurrect trainiq's dropped `email_hash`. It is a re-identification
vector with a small, enumerable input space, and it is tempting because it
looks like anonymisation.

### 2. No band labels while `BANDS_VALIDATED` is False (§10.5)

> "Universal score bands should not be treated as validated merely because the
> scale ranges from 1 to 5."

No `Low` / `Moderate` / `High` / `Critical` / `Gap` anywhere — which means
promo's four-band palette does **not** transfer. Colour encodes the **response
category** (the 1–5 anchors), never a judgement of the score. Reports show
score + distribution + comparison; relative statements are defensible before
validation, absolute ones are not.

### 3. No GEM composite (§10.3)

The three Overall Satisfaction subdomains are reported **separately**. There is
no single "Organisational Satisfaction 3.4/5" headline, on any screen or in any
report. `gem_composite()` does not exist, and if a caller wants one the
function raises with the §10.3 reason in the message rather than returning a
number.

This is the single thing clients ask for most. Saying no is a sales
conversation, not a technical one.

### 4. Association is never causation (§10.4, §17.8)

Every association output carries `domains.ASSOCIATION_CAVEAT`, imported from
one place so a new chart cannot omit it by forgetting it. No generated
narrative uses `causes` / `drives` / `because of` / `leads to` next to an
association figure. The "no causal claim" test is **negation-aware** — the
reports deliberately print the negated forms — and the guard is itself tested
against real violations so it cannot quietly become vacuous.

### 5. 'Daunting' is a mnemonic, not a meaning (§6.4)

`(TID, 'D')` measures **appropriate challenge and development**. It must never
be rendered, narrated or summarised as pressure, overload or stress. A low
score can mean *under-stretched* as easily as *overwhelmed*, and nothing in the
data distinguishes them — the report says which items drove it and stops. The
client-facing label is "Appropriate Challenge and Development"; the word
"Daunting" never reaches a client.

---

## Two more that cost money if broken

**Training is one intervention category among several (§11, §8.5).** The three
PIE subdomains carry **no `training` type** in `domains.INTERVENTION_MAP`, and
`app/catalogue.py` filters candidate rows against those type lists *before*
anything reaches a report or the model. So a compensation finding cannot
produce a training recommendation however the narrative is phrased. It is
prevented by the data, not by asking a model nicely.

**Suppression is enforced in `app/analytics/`, never in a template.** A
template-level check is one `{% if %}` away from being forgotten in the next
report. `MIN_REPORTING_N` defaults to 5, may be raised per cycle and never
lowered. `permissions.LOWER_MIN_N` exists and **is held by no role, not even
L1** — weakening suppression must be a code change and a review, not an
afternoon's decision under client pressure. Suppression also defends against
**differencing**: when one cell in a breakdown is suppressed, the next-smallest
is suppressed too.

---

## Tech stack

Mirrors the siblings deliberately, so one developer moves between all three.

- **Language**: Python 3.13.3, pinned in `.python-version` **and**
  `render.yaml`. They must match — a mismatch pins wheels for the wrong
  interpreter and the deploy fails at install while the last good build keeps
  serving.
- **Framework**: Flask 3.x · **DB**: PostgreSQL 16 + SQLAlchemy 2.x + Alembic
- **Background**: none. Every job runs synchronously inside the Flask request.
  **Do not add Celery or redis** — they were deliberately removed from trainiq.
  This is why `--timeout 120` in `render.yaml` is load-bearing.
- **Charts**: Plotly for in-page (`to_html`); **matplotlib** for PDF PNGs. Do
  not add `kaleido`.
- **PDF**: WeasyPrint · **Excel**: openpyxl · **PPTX**: not available
- **Auth**: Flask-Login + bcrypt · **AI**: Anthropic SDK, manual-trigger only
- `scipy` is **not** a dependency. Pearson *r* and a Fisher-z interval are a
  dozen lines of numpy, and scipy is heavy on a 512 MB free tier.
- Add a dependency only when something imports it, then
  `pip freeze > requirements.txt`.

**Never SQLite.** The schema uses ARRAY, JSONB, a persisted computed column and
a partial unique index. A suite that passes on SQLite proves nothing about any
of them. `TestingConfig` carries an in-memory SQLite URL **as a placeholder**
so the app can be constructed for template tests; the DB-backed fixtures gate
on `TEST_DATABASE_URL` and skip when it is unset.

---

## ONE FILE RULE (non-negotiable)

- ALL SQLAlchemy models live in `app/models.py` — never split across files.
- ALL scoring logic lives in `app/analytics/scoring.py`.
- ALL chart generators live in `app/analytics/charts.py`.
- ALL report generators (PDF / XLSX) live in `app/analytics/reports.py`.
- ALL role/capability rules live in `app/permissions.py` — never inline a role
  comparison in a route or template; ask `can(user, CAPABILITY)`.
- ALL domain / subdomain / anchor / caveat / §11-map constants live in
  `app/domains.py`.
- ALL list filtering/sorting UI lives in `app/static/js/filters.js` + the
  `.filter-bar` block in `main.css`.

Edit files in place. No `models_v2.py`, no `scoring_new.py`.

---

## Folder layout

```
app/
├── __init__.py          App factory; blueprints, db, login_manager
├── models.py            ALL SQLAlchemy models (18 tables)
├── domains.py           Domains, subdomains, anchors, caveats, the §11 map
├── permissions.py       Role → capability table (pure Python, no Flask import)
├── catalogue.py         Intervention helpers (pure; type-filters against §11)
├── provisioning.py      Master-template cloning into an org
├── ai_service.py        Anthropic call + narrative orchestration (manual-trigger)
├── audit.py             Append-only admin action log
├── auth/                Login / logout + `/` → login redirect + route gates
├── admin/               L1 (cross-org, templates, catalogue, usage, audit, validation)
├── org/                 L2/L3 (dashboard, cycles, segments, deployments, reports)
├── survey/              THE ANONYMOUS SURVEY — no login, no leadTo chrome
├── analytics/{scoring,charts,reports}.py
├── prompts/system_prompt.txt
├── templates/{auth,admin,org,survey,reports,errors}/
└── static/{css,js,img}/
migrations/   tests/   docs/   config.py   run.py   wsgi.py   render.yaml
```

`app/survey/` is the one blueprint whose templates are covered by
`tests/test_privacy_and_wording.py` on every push. It never extends
`base.html`.

---

## Scoring (§10.1–§10.2)

Every level excludes `not_applicable` from **both numerator and denominator**.

1. **Item** — raw 1–5 (`6 − score` if `is_reverse_scored`).
2. **Subdomain** — mean of that session's ≤4 answered items. Requires
   `SUBDOMAIN_MIN_ITEMS = 2`, else `Insufficient` for that session.
3. **Domain** — `DOMAIN_SCORING_METHOD`, default `mean_of_subdomains`.
   **Both methods are implemented**, the constant is **frozen per cycle**
   (`measurement_cycles.scoring_method`) and **printed in every report's
   methodology section**. §10.2 does not mandate a method; it mandates
   documenting which one is used, because the two diverge when completeness
   varies.
4. **Group** — mean of the per-session **domain** scores, *not* the mean of all
   raw responses. Otherwise a respondent who answered everything outweighs one
   who used N/A. This is exactly the kind of thing that gets "simplified" back.
5. **Distribution is the primary display** (§10.5), not the mean. A 3.0 made of
   all-3s and a 3.0 made of half-1s and half-5s are different organisations.
6. **Dispersion** — σ and IQR. High dispersion inside a team is a *finding*
   (a split team), never a data-quality warning.
7. **Coverage** — N/A count and %. 30% N/A on a Peer Comparison item means
   people do not know what their peers earn: a finding about pay transparency,
   worth naming.

**N/A is never 0, 3 or 6.** A sentinel reaches an `AVG()` eventually and
corrupts a result silently.

---

## Anthropic API conventions

- Narrative generation is **manual-trigger only** (a button per report), never
  auto-dispatched at a response threshold.
- Load the system prompt from `app/prompts/system_prompt.txt` as a file
  constant — never inline it as a long Python string.
- **Every quantity is computed deterministically in Python** and merely
  narrated.
- Retry up to 3× with exponential backoff (1s, 2s, 4s) on HTTP 529. Log and
  raise on JSON parse failure — never silently fall back to `{}`.
- API key from `os.environ["ANTHROPIC_API_KEY"]`; fail loudly if missing.
- **Reports never mention AI, Claude or Anthropic.** The section is
  "Interpretation".
- Four prompt rules are load-bearing: never assert causation; never apply a
  band label while `BANDS_VALIDATED` is False; never read low `(TID, 'D')` as
  stress or overload; never present training as a response to a compensation
  finding.
- Confirm the model string in `app/ai_service.py` before assuming it is
  unchanged — do not hardcode from memory.

---

## Security rules

- Secrets live in `.env` only, which is git-ignored. Never in a `.py`, `.html`
  or any tracked file. If one is ever committed, rotate it in the provider
  console immediately.
- `FLASK_SECRET_KEY` must be the **output** of
  `python -c "import secrets; print(secrets.token_hex(32))"`, never the command
  itself. Production refuses to boot on a key that looks like the command —
  this has happened once on the sibling project, and it makes every session
  cookie forgeable for every user including L1.
- `DATABASE_URL` on Supabase must be the **session-mode pooler** URI
  (`postgres.<project-ref>@...pooler...`), not `db.<ref>.supabase.co`, which
  resolves IPv6-only. This cost promo a deploy.
- RLS is enabled on Supabase to shield the **anon key only**. The app connects
  as the table owner and bypasses RLS entirely. **Branch isolation comes from
  route-level scoped queries** (`_apply_branch_scope`), never from a policy.
- `audit_events.payload` carries **administrative identifiers only** — never
  response data. It is the one place respondent data could be smuggled into a
  long-lived table that outlives the retention purge.

---

## Build discipline

1. Read `docs/jobsat build plan.md` and the relevant spec section first.
   **Quote the section; do not paraphrase it.**
2. Edit files in place.
3. Run the phase's named acceptance test and report the result — **including
   when it fails**.
4. If a package was added, `pip freeze > requirements.txt`.
5. Every migration runs forward **and** backward on local Postgres before it is
   committed. Autogenerate is a draft, not an answer.
6. Commit at logical checkpoints with a clear summary.

---

## Inherited bugs — already paid for once, on promo

- **`app.analytics.reports` must be imported as `report_builder`** in the org
  blueprint: that module defines a view called `reports()` which shadows the
  module at import time.
- **Jinja macro imports need `with context`** — a bare `{% import %}` sees no
  `current_user`, so `can()` raises inside a shared macro.
- **`tests/conftest.login()` clears Flask-Login's `g._login_user`.** The `db`
  fixture pushes one app context per test and Flask reuses it, so without this
  a second login is silently a no-op and an authorisation test passes **as the
  wrong user**. The worst class of bug a suite can have.
- **Uniqueness goes in the database, not the route.** Two admins pressing Save
  is a race the route cannot win; `uq_open_deployment` is the fix and the route
  check is only a nicer error message.
- **L1 has no `org_id`**, so any capability L1 alone holds — the catalogue, the
  validation report, pilot feedback, the retention purge — cannot live in a
  blueprint whose every query is scoped by `current_user.org_id`. Put it in
  `app/admin/`.
- **`ProductionConfig` reads `os.environ` at class-body evaluation**, once, at
  import. A test that `monkeypatch.setenv`s a config value changes nothing;
  patch the class attribute instead. Four tests passed for the wrong reason
  before this was noticed.
