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

### 2. No band labels, ever (§10.5)

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

## Six deliberate departures from the spec

Both were decided by the product owner after seeing the screens. They are
recorded here so they are not "fixed" back by someone reading only the PDF.

### The landing page carries no §9.2 disclosures (2026-10-19)

`app/templates/survey/landing.html` used to carry a "What we can and cannot
promise" section: the delivery-mode anonymity claim, the reporting threshold as
a number, who sees what, non-retaliation, and the technical-logs caveat. **It
was removed on request.** The respondent is now told only what the org admin
wrote in `purpose_statement`.

`reporting_promise()` in `app/survey/routes.py` still computes `promise` and the
route still passes it; the template no longer renders it. **Leave it there.** It
is the only code that keeps the wording honest if the section is restored, and
at `MIN_REPORTING_N = 1` a restored section that claims group-only reporting is
a false statement to someone answering candidly about their employer.

The deleted assertions are listed in full in the comment block at the top of
the "landing page no longer makes any promise" section of
`tests/test_survey_form.py`. Restoring the section means restoring those tests,
not reconstructing the requirement from memory.

What still holds: the page must not **overclaim** ("completely anonymous" and
friends are still asserted absent), and an open-link deployment must still not
claim one response per person.

### The purpose statement is written once per org, not once per cycle

`orgs.default_purpose_statement` (migration `0003`) holds the organisation's
standing statement. A new cycle is **created with a copy of it**, and
`measurement_cycles.purpose_statement` stays `NOT NULL` — so the landing page
still renders the cycle's own text and §9.2 is still a property of the schema.
Only the place it is *written* moved.

The gate moved with it: an org with no default **cannot create a cycle at all**.
L2 writes it under `/org/settings` (`MANAGE_ORG_SETTINGS`, L2-only); L3 inherits
it and may edit its own cycle's copy, which touches nothing else.

The creation form asks for **three fields — name, domains, previous cycle**.
The edit form adds one: the cycle's own purpose statement. The cycle reference
is generated by `_next_cycle_id()` rather than typed.

**Scoring method, reporting threshold, retention and the pilot flag are on
neither form.** Every cycle takes them from `_cycle_defaults()` and keeps them
for life; both routes **ignore** them in a posted body rather than validating
one. What that costs, so it is not rediscovered as a bug:

- `min_reporting_n` is always the platform floor. Raising it for a client who
  wants stricter suppression is a config or code change. Safe in the direction
  that matters — it can no longer be lowered either, and `LOWER_MIN_N` was
  already held by no role.
- `retention_months` is always 24. Still bounded and defined, so §13.8 holds.
- `scoring_method` is always `mean_of_subdomains`. §10.2 asks that the method
  be *documented*, and every report still prints it.
- A pilot cycle (§15) can no longer be created through a screen.

Restoring any of them means restoring **both** the field in `cycle_form.html`
and the branch in `_cycle_form_values` that reads it.

---

### Every deployment is an open link (2026-10-19)

The Delivery choice was removed from the deploy screen on request. `deploy()`
sets `delivery_mode = 'open_link'` unconditionally and **ignores** a posted
`delivery_mode` rather than validating it — a form that no longer offers a mode
must not be able to select it through a hand-crafted POST.

**Left in place deliberately:** the `delivery_mode` column, `ck_delivery_mode`,
the `AccessTicket` table, `issue_tickets()`, `ISSUE_TICKETS`, and the survey's
ticket-entry path. They still serve any deployment that predates the change,
and the honest-claim wording that distinguishes the two modes is not something
to reconstruct from memory if the choice comes back. Nothing here needs a
migration, which is what makes the change reversible.

Note the interaction with the removed landing-page section: the open-link
deployment used to tell respondents that duplicate submissions cannot be
strictly prevented. It no longer tells them anything, so **the only remaining
statement about duplicates is whatever the org admin wrote in the purpose
statement.**

---

### `MIN_REPORTING_N` is 1 everywhere, production included (2026-10-19)

Set on request in all four config classes, in `MeasurementCycle.min_reporting_n`'s
default and in the `app/__init__.py` boot check's fallback.

**At 1 there is no suppression.** A group of one reports, so a segment
breakdown containing a single person shows that person's own answers to whoever
reads the report — their manager included. The **differencing** defence never
fires either, because no cell is ever suppressed for it to protect.

It used to be 5 in production and 1 in development and testing, split so a
threshold convenient for a local walkthrough could not reach a real workforce by
inheritance. That split is gone; 1 is the intended production value, not a
leftover. `test_the_reporting_threshold_is_1_IN_EVERY_ENVIRONMENT` pins it,
inverted from the test that used to pin the split.

Compounding it: the landing page no longer warns respondents either. The removed
section said, in those words, that a result might rest on their answers alone
whenever this value was below 2.

**Still true, and still worth keeping:** the floor is one-directional (a cycle
may raise its own `min_reporting_n`, never lower it) and `LOWER_MIN_N` is held by
no role — though at a floor of 1 neither guarantee protects anything, since
there is nothing below 1. The production boot warning in `app/__init__.py` now
fires on **every** start; it was left in deliberately so the choice is visible in
the logs rather than invisible for having become the default. Do not silence it
by lowering the comparison. **Association outputs are unaffected** —
`contrast_floor()` takes `max(ASSOCIATION_MIN_N, 3 × min_reporting_n)`, so
`ASSOCIATION_MIN_N` still binds there.

A cycle created before this change keeps its stored value and no screen can
lower it.

---

### The deployment page was merged into the cycle page (2026-10-19)

`org/deployment_detail.html` is deleted. The survey link, invited count, response
rate, per-deployment close button and the legacy ticket-issuing form now render
as one block per deployment inside the Deployments panel of
`org/cycle_detail.html`; `cycle_detail()` builds a `rows` list carrying each
deployment's link and ticket counts.

**`deployment_detail()` is kept as a redirect**, not deleted — eight `url_for`
call sites and any bookmark still resolve. `_deployment_or_404()` still runs
*before* the redirect, and must keep doing so: a redirect that skipped it would
turn the route into a cross-tenant probe, because a redirect carrying another
org's cycle id confirms that cycle exists.
`test_the_redirect_still_404s_another_tenants_deployment` pins that.

---

### Segments were removed from the product (2026-10-19)

The reporting dimensions (department, function, location, tenure band) are
gone: the `/org/segments*` screens, the respondent's `/about-you`
self-declaration step, `create_default_segments()` at org creation, the
cycle-open gate that required them to be configured, and the Segments nav tab.

**Why**, recorded because §3.2.4 asks for "patterns across departments,
functions, locations": branch breakdowns and cycle-over-cycle comparison cover
most of it, and the vocabulary was a setup step a novice admin had to get right
*before* a cycle could open — an empty dropdown being unfixable once responses
arrived.

**What is left in place, unused:** the `segments` and `segment_options` tables
and models, `response_sessions.segment_values` (still written as `{}`), and the
`provisioning` helpers. `association.segment_comparison()` and the
`comparison.segment_label` blocks in `analysis.html`, `executive.html` and
`hr_od.html` survive too — they iterate a list that is now always `[]`, so they
render nothing. Dropping any of it would be a migration that destroys what
earlier respondents declared and would make restoring the feature a rewrite
rather than a revert.

`MANAGE_SEGMENTS` still exists in `permissions.py` and **is held by no role** —
the same shape as `LOWER_MIN_N`, for a different reason: this table should
record a feature that is *off*, not a feature nobody thought about.

**The cost:** there is no longer any breakdown *within* a branch. A department's
results inside Nairobi cannot be produced, because the data is no longer
collected.

---

### The deployments list was merged into the cycles list (2026-10-19)

`org/deployments.html` is deleted and `deployments_list()` redirects to
`cycles_list()`. The cycles table now carries scope, responses, rate and
deployed-date, summed from the cycle's deployments in `cycles_list()`.

**Summed, not taken from one row.** A cycle is 1:1 with its deployment only
because no role can currently create a second: L2 holds `DEPLOY_ORG_WIDE` but
not `DEPLOY_TO_BRANCH`, L3 the reverse, and `_scoped()` stops L3 seeing
org-wide cycles. The schema still permits one org-wide deployment alongside one
per branch (`uq_open_deployment` keys on scope, not on cycle), so the page must
not start lying if that policy changes.

Note `cycle_detail.html` still gates its Deploy button on `{% if not
deployments %}` — stricter than both the schema and the capability table, and it
blocks the legitimate remake-after-archive case.

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
report. `MIN_REPORTING_N` is **1 in every environment including production**
(see the departures section below), may be raised per cycle and never
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
- ALL expand/collapse lives in `app/static/js/collapse.js`, and ALL copy-to-clipboard in `app/static/js/copy.js`. Both are data-attribute contracts (`data-collapse-*`, `data-copy`) with no id plumbing, so a page can render N of them — one per deployment — without colliding.

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
  band label; never read low `(TID, 'D')` as stress or overload; never present
  training as a response to a compensation finding.
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
   **Run only the tests that cover the change — never the full suite unless
   asked.** A full run costs minutes that belong to building; a targeted
   `pytest tests/test_x.py::test_y` proves the same thing. The full suite is for
   release checkpoints and for when the user asks for it.
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
  pilot feedback, the retention purge — cannot live in a
  blueprint whose every query is scoped by `current_user.org_id`. Put it in
  `app/admin/`.
- **`ProductionConfig` reads `os.environ` at class-body evaluation**, once, at
  import. A test that `monkeypatch.setenv`s a config value changes nothing;
  patch the class attribute instead. Four tests passed for the wrong reason
  before this was noticed.
