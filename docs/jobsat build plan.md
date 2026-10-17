# leadTo ➡️ Job Satisfaction Analysis System — Build Plan (v2)

**Product:** `jobs.leadto.co.ke` · **Repo:** new (`leadto-jobsat`) · **Status:** scaffolding not yet started
**Source of truth:** `LEADTO ➡️ JOB SATISFACTION ANALYSIS SYSTEM.pdf` (Master Spec v3.0, §1–§18).
**Chassis:** `../trainiq` (live TNA) and `../promo` (Promotion Readiness) — inherit, do not reinvent.
**Content outstanding:** survey workbook and intervention/course curriculum. Everything below is built to receive them.

---

## 0. The one-paragraph summary

Four domains (GEM / TID / BIT / PIE), twelve subdomains, four questions each — **48 items, one anonymous
link, one five-point agreement scale plus N/A**. GEM is the *outcome*; TID, BIT and PIE are *diagnostic*
and are reported **in relation to** GEM. The platform's job is to show scores, distributions and
associations at group level, under minimum reporting thresholds, and to route findings to an
intervention category — **without** declaring causation, without a validated band label, and without
ever exposing an individual respondent.

---

## 1. What this product is NOT (read before writing code)

Three inherited instincts will actively damage this product. Each is a spec requirement, not a preference.

| Instinct from trainiq/promo | Why it is wrong here | Spec |
|---|---|---|
| Attribute every response to a named person (promo's `assessment_invitations`) | This is an **anonymous employee survey**. Attribution destroys the candour the whole instrument depends on, and exposes employees to retaliation. The respondent is never identified. | §9.2, §13 |
| Slap the four-band palette (`Strength / Minor / Moderate / Critical Gap`) on every score | §10.5 forbids it outright: bands are **not validated** merely because the scale is 1–5. Early implementation shows **scores, distributions, comparisons and limitations** — not labels. | §10.5 |
| Average the three GEM subdomains into one headline "satisfaction score" | §10.3: report the three **separately**. A GEM composite only exists if validation supports it. | §10.3 |
| Call a correlation a driver and recommend training | §10.4, §11, §17.8: association ≠ causation; perception ≠ objective evidence; training is **one** intervention category among several. | §10.4, §11 |

Trainiq already carries the respondent-privacy rule and it has held because it is **checkable**. Do the
same here — see §2.4.

---

## 2. Framework, taxonomy and the non-negotiables

### 2.1 Domains (§4)

| Code | Domain | Framework | Role |
|---|---|---|---|
| `GEM` | Overall Satisfaction | GEM | **Outcome domain** |
| `TID` | Task Satisfaction | TID | Diagnostic |
| `BIT` | Relationships Satisfaction | BIT | Diagnostic |
| `PIE` | Reward Satisfaction | PIE | Diagnostic |

### 2.2 Subdomains (§5–§8) — twelve, 4 items each, 48 total

| Domain | Code | Subdomain | Definition (abbrev. — hold verbatim in `app/domains.py`) |
|---|---|---|---|
| GEM | `G` | General Job Satisfaction | Considered evaluation of the job as a whole |
| GEM | `E` | Emotional Experience | Feelings and affective reactions associated with work |
| GEM | `M` | Match with Overall Expectations | Alignment between expectations and actual experience |
| TID | `T` | Thrilling — Autonomy & Control | Discretion and control over how work is done |
| TID | `I` | Important — Purpose & Impact | Significance and contribution of the work |
| TID | `D` | Daunting — Appropriate Challenge | Suitable challenge, capability fit, development |
| BIT | `B` | Belonging | Acceptance, inclusion, recognition as a member |
| BIT | `I` | Positive Interactions | Quality of day-to-day interpersonal interaction |
| BIT | `T` | Trust | Honesty, reliability, psychological safety |
| PIE | `P` | Peer Comparison | Compensation vs comparable people |
| PIE | `I` | Industry Comparison | Compensation vs perceived industry norm |
| PIE | `E` | Expectations | Compensation vs personal financial expectation |

**Subdomain codes repeat across domains** (`I` is in TID, BIT and PIE; `T` in TID and BIT; `E` in GEM and
PIE). The key is therefore always the **pair** `(domain_code, subdomain_code)`, never the letter alone,
and `item_code` is fully qualified: `GEM-G-01` … `PIE-E-04`. Grouping and every join happen on these
codes, **never on a human-readable label** — trainiq's `sub_module` is simultaneously display string,
grouping key and catalogue join key, and a rename there silently kills recommendations. Do not repeat it.

> **48 items with 4 per subdomain is a materially better instrument than promote's 2-per-cell.** A
> subdomain score rests on 4 items and is a legitimate reporting unit. There is no "thin cell" caveat to
> carry over here. Report at **subdomain** level freely; the domain is the summary, not the floor.

### 2.3 "Daunting" is a mnemonic, not a meaning (§6.4)

`D` measures **appropriate challenge and development**. It must never be rendered, narrated or
AI-summarised as pressure, overload or stress. Low `D` can mean *under-stretched* as easily as
*overwhelmed* — the report says which items drove it and stops. Same class of hazard as promote's MESS
leak; same remedy: a test.

### 2.4 What is client-facing, and the leak test

Unlike promote, the framework here **is** client-facing — GEM/TID/BIT/PIE are the product's vocabulary
and appear in reports. What must *not* leak is different:

`tests/test_privacy_and_wording.py` renders every client-facing template and report fixture and asserts:

- no respondent identifier, email, IP, token or session id appears in any report, export or chart;
- any group result rendered has `n >= MIN_REPORTING_N` (§2.6) — a suppressed cell renders as
  `Suppressed (n < 5)`, never as a number, never as a blank that a reader will mistake for zero;
- the `Daunting` subdomain never co-occurs with stress/overload vocabulary;
- no band label (`Low`/`Moderate`/`High`/`Critical`) appears anywhere (§10.5);
- no causal verb (`causes`, `drives`, `because of`, `due to`, `leads to`) appears in a generated
  narrative paragraph adjacent to an association figure. The guard is **negation-aware** — the reports
  deliberately print "this does **not** show that X causes Y" — reuse promote's `verdict_hits()` pattern
  from `tests/`, which is itself tested against real violations so it cannot quietly become vacuous.

Run it in CI.

### 2.5 The response scale (§9.1)

`ANCHOR_LABELS['agreement']`, in `app/domains.py`, **single source of truth** — the form and the report
charts both import it. Trainiq's `confidence` scale drifted between two private copies and reports
mislabelled answers by one step.

```python
ANCHOR_LABELS['agreement'] = [
    'Strongly disagree', 'Disagree', 'Neither agree nor disagree', 'Agree', 'Strongly agree',
]   # plus 'Not applicable / Unable to assess' — stored as not_applicable,
    # NEVER as a 3 and never as a 6th score
```

**N/A is not a neutral score** (§9.1, explicit). It is a first-class boolean, excluded from both
numerator and denominator at every level, and counted and reported as coverage. Do not encode it as `0`,
`3` or `6`; a sentinel reaches an `AVG()` eventually and corrupts a result silently.

Unlike promote, N/A is available on **every** item (the spec offers it wherever a respondent "lacks
sufficient information"), but it is deliberately a second click, not a peer option in the radio row —
otherwise it becomes the lazy answer.

### 2.6 Minimum reporting threshold (§9.2, §13)

`MIN_REPORTING_N` — one constant, default **5**, org-configurable upward only, never downward. Enforced
in `app/analytics/` at the point a group aggregate is produced, **not** in the template. A template-level
check is one `{% if %}` away from being forgotten in the next report.

Suppression must also defend against **differencing**: if a department of 12 has three teams of 5, 4 and 3,
suppressing only the 3 lets a reader subtract it out of the total. Implement complementary suppression —
when one cell in a breakdown is suppressed, suppress the next-smallest too. `tests/test_suppression.py`
pins the arithmetic with a worked fixture.

---

## 3. Data model

Postgres. **Local Postgres 16 first** (§10 phases), Supabase project later. Own Alembic chain, own
database — deliberately **not** shared with trainiq or promo. Trainiq already pays for a shared schema
with an `include_object` filter in `migrations/env.py` to stop autogenerate emitting `DROP TABLE`; do not
create a second instance of that.

### 3.1 Inherited essentially unchanged

`orgs` · `branches` · `users` · `audit_events` · `report_events`

Keep every design note in `promo/app/models.py` — soft-delete/archive semantics, `created_by` for
accountability, the denormalised `actor_email` on audit rows, `Branch.active_domains` intersected at read
time by `effective_domains`.

### 3.2 `measurement_cycles` — the wave

The cycle is a **measurement wave**, not a person and not a position. "Employee Satisfaction Survey 2026
H1". This is what §3.2.6 / §3.2.7 ("establish a baseline", "track changes over time") and §16 rest on.

```
id (String PK, e.g. 'JS-2026-H1')     org_id
branch_id (nullable — NULL = org-wide)
cycle_name            purpose_statement (text — what §9.2 requires be told to respondents)
assessed_domains (ARRAY — defaults to all 4; see §4.3)
previous_cycle_id (nullable, self-FK)  -- baseline linkage; see below
status (draft|open|closed)             opened_at / closes_at
min_reporting_n (int, default 5)       retention_months (NOT NULL, default 24, bounded 3–120)
created_by     archived_at / archived_by
```

`previous_cycle_id` carries from day one even if trend reporting ships in a later phase. Promote learned
this: retrofitting the link after the fact is far harder than carrying an unused column.

Carry over the rule: a cycle may be **hard** deleted only while it holds no deployments; once it has held
any, it is archived so its reports and cost events keep their parent.

### 3.3 `segments` and `segment_options` — the reporting dimensions

§3.2.4 ("patterns across departments, functions, locations and other appropriate organizational groups")
is a core objective, so segmentation is a first-class table, not a hardcoded enum.

```
segments:         id  org_id  segment_key ('department'|'function'|'location'|'tenure_band'|custom)
                  label  is_active  sort_order  collect_at_survey (bool)
segment_options:  id  segment_id  option_code  option_label  sort_order  is_active
```

Two sharp rules, both privacy:

- **A segment with few options is fine; a *combination* of segments is not.** Cross-tabs
  (department × tenure) re-identify fast. v1 reports **one segment at a time**; a cross-tab is an
  L1-gated, min-N-enforced, explicitly-logged action, or it is phase 11.
- **Free text is never a segment.** Options are a controlled vocabulary set by the org admin before the
  cycle opens and frozen on the first response — otherwise two spellings of "Finance" become two groups.

### 3.4 `survey_deployments`

**One deployment = one cycle for one audience, covering all four domains, one link** (§4).

```
id   org_id   branch_id (nullable)   cycle_id
assessed_domains (ARRAY — inherited from the cycle, overridable)
instrument_version (int)
public_slug          -- secrets.token_urlsafe(32), unique; the ONE anonymous link
delivery_mode ('open_link' | 'ticketed')
expected_respondents (int)   -- headcount, for the response-rate denominator
responses_received (int)
response_rate_pct (Computed, as trainiq)
deployed_at   closes_at   closed
archived_at / archived_by   created_by
```

Reuse trainiq's `uq_open_deployment` partial unique index, re-keyed: unique on `(org_id, cycle_id)` where
`closed = false`, wrapping `branch_id` in `COALESCE(branch_id, nil-uuid)` — Postgres treats NULLs as
distinct, so a bare nullable column silently stops blocking duplicate org-wide rows. The comment in
`trainiq/app/models.py` explains it; copy the comment too.

### 3.5 `response_sessions` — anonymity with integrity

This is the table that makes the product correct. **It holds no identity and no FK to any identity.**

```
id (uuid)   deployment_id   org_id   branch_id (nullable)
segment_values (JSONB — {'department': 'FIN', 'tenure_band': '1_3'})
started_at   submitted_at (nullable)   last_seen_at
resume_secret_hash                     is_void
```

- A session is created on first `GET /survey/<slug>` and carries the partial answers, so save-and-resume
  works **without knowing who the person is**. The browser holds an opaque `resume_secret`; the server
  stores only its hash. Lose the browser, lose the draft — that is the correct trade.
- **`segment_values` is self-declared at the start of the form**, not looked up from an HR file. This is
  §13.2 data minimisation: the platform never ingests a staff list.
- A segment combination so rare it identifies someone is handled by suppression at report time (§2.6),
  not by refusing the response.
- **No IP address, no user-agent, no email, no employee number is stored on this table or any other.**
  Note that the web server and Render's edge will log IPs; that is outside the app, and the privacy
  notice must say so rather than promising absolute anonymity (§9.2, explicit: *"avoid promising absolute
  anonymity if the administration method cannot guarantee it"*).

**Single-submission control without identity.** Two supported modes, per deployment:

| Mode | Mechanism | Honest claim |
|---|---|---|
| `open_link` (default) | one shared multi-use slug; a `jsat_submitted_*` cookie is a nudge, not a control — trainiq documents this exactly | "We cannot link your response to you. We also cannot strictly prevent duplicate submissions." |
| `ticketed` | HR generates N opaque tickets, hands them out, app stores only `sha256(ticket)` + `used_at` in `access_tickets` — **never who received which** | "One response per ticket. We do not know which ticket is yours." |

Do **not** resurrect trainiq's dropped `respondent_tokens` columns (`email_hash`, `used`, `expires_at`).
An `email_hash` is a re-identification vector with a small, enumerable input space — it is exactly the
thing this design refuses. Design `access_tickets` fresh and keep it identity-free.

### 3.6 `survey_items`

Org-scoped, versioned, clonable from a master org — trainiq's `SurveyQuestion` pattern, which already
does what is needed: seed later, clone per client, freeze on first clone.

```
id   org_id   version   domain_code   subdomain_code   item_code   ordinal (1..4)
item_text   anchor_type ('agreement')   allows_na (bool)   is_reverse_scored (bool)   is_active
```

`is_reverse_scored` exists and is honoured in scoring from day one even though none of the 48 supplied
items is currently reverse-worded. Pilot validation (§15) is the most likely reason to introduce one, and
retrofitting a reverse flag through a scoring ladder after real data exists is the kind of change that
silently flips a subdomain.

**Ingestion, when the workbook arrives** (mirrors promote's §3.6.1, which worked):

- Per-domain CSVs under `docs/surveys/{gem,tid,bit,pie}.csv`, transcribed **verbatim**, 12 rows each.
  The supplied files are the source; **there is no generated intermediate** — `seed_templates.py`
  translates them at seed time. A second transcription drifts from what the client signed off.
- **Only the question column is ingested.** A `Subdomain Name` column that names the construct openly
  ("General Job Satisfaction") must never reach a stored `item_text`.
- **A malformed cell is rejected, never truncated.** A subdomain delivered with 3 or 5 questions, an
  unknown domain or an unmapped subdomain code aborts the seed naming the file and line. A silently short
  subdomain rests a reported score on three items and is visible only as a wrong number weeks later.
- `--force` replaces the **master** only. Orgs that have cloned keep their copy, by design.
  `--reclone-org <org_id>` re-clones an org still holding `[DRAFT]` placeholders, and refuses the moment
  any item carries real wording or any response points at one — at that point the frozen copy is evidence.

> **The workbook already in this folder** (`LeadTO➡️ Job Satisfaction Analysis System.xlsx`): treat the 48
> items printed in the PDF §5–§8 as authoritative and the workbook as a draft to be reconciled against
> them, item by item, at seed time. Budget a short data-prep step; every leadTo workbook so far has
> arrived with Excel-mangled codes and at least one bad row. Do not import a spreadsheet directly.

### 3.7 `survey_responses`

```
id   session_id (NOT NULL)   deployment_id   org_id   item_id
score (SmallInteger, NULLABLE)
not_applicable (Boolean NOT NULL default false)
answered_at

CHECK ( (not_applicable AND score IS NULL)
     OR (NOT not_applicable AND score BETWEEN 1 AND 5) )
```

`session_id` is what makes **within-respondent analysis** possible — the correlation of a respondent's
TID score with their own GEM score (§10.4) — while `response_sessions` holds no identity. That is the
whole architectural trick of this product: *anonymous but not aggregate-only*. An aggregate-only design
(storing per-item means and discarding rows) would make §10.4 impossible; a named design would make §13
impossible. Rows keyed to an anonymous session is the only shape that satisfies both.

### 3.8 `intervention_map` and `intervention_catalogue`

§11's table is **reference data, not prose in a template**. `intervention_map` is a seeded constant:
`(domain_code, subdomain_code) → areas_to_investigate[], response_categories[]`, transcribed verbatim
from §11 and rendered by every report that needs it.

`intervention_catalogue` (when the curriculum arrives) joins on **`(domain_code, subdomain_code)`** —
structured, immune to label renames, explicitly *not* trainiq's free-text `sub_module` join. No `org_id`:
one catalogue shared by every tenant, L1-only to edit, exactly as trainiq's `courses`.

Each catalogue row carries an `intervention_type` (`training` | `work_design` | `management_practice` |
`policy_or_structural` | `further_diagnostic` | `communication`). **Reports surface the type**, and a
subdomain whose §11 response categories contain no training entry must not produce a training
recommendation — §11 and §17.4 are explicit that training is one category among several, and §8.5 is
explicit that training is **not** a substitute for a compensation fix. Make that structural, not a prompt
instruction: `app/catalogue.py` filters by type against the §11 map before the AI ever sees a row.

> **`intervention_type` is a derived classification, and must be labelled as one.** §11's table gives
> response categories as **prose** ("Delegation; process redesign; manager development"); the six-value
> vocabulary above is *our reading* of that prose, not text the spec supplies. So the mapping lives as
> explicit data in `docs/intervention_map.json` with the §11 prose quoted verbatim beside each assigned
> type, and it is **signed off by the product owner before phase 10**, not inferred in code. A type
> assigned by a regex over prose is a clinical-sounding filter resting on a guess — and it is the filter
> that decides whether a compensation finding can produce a training recommendation (§8.5).

Seed from a reviewable `docs/intervention_catalogue.json` via `seed_catalogue.py`, mirroring trainiq's
`seed_courses.py`. A JSON file diffs in git; a spreadsheet does not.

---

### 3.9 `improvement_actions` — the half of §16 that has no table yet

§16 asks the system to measure **organisational value**, and §12.3 ends the Management Report with
*"follow-up commitments and progress monitoring"*. Neither is a report section that can be rendered from
survey rows: both need state that persists **between** waves. Promote and trainiq have no analogue to
inherit — this is new, and leaving it out reduces the product to a score generator, which §16's last
sentence explicitly says it is not.

```
id   org_id   branch_id (nullable)   cycle_id
domain_code   subdomain_code (nullable)   -- what the action responds to; NULL = org-wide
title   description
intervention_type        -- same controlled vocabulary as the catalogue (§3.8)
owner_label (text)       -- a ROLE or team, e.g. 'Head of Finance'. NEVER a respondent.
status (proposed|agreed|in_progress|completed|dropped)
agreed_at   due_at   completed_at
evidence_note (text)     -- what changed, in the owner's words
created_by   created_at  -- audited like everything else
```

Three rules:

- **An action is owned by a role, never by a respondent.** `owner_label` is free text by design, but the
  privacy test (§2.4) scans it like any other rendered string: an action reading "follow up with the
  person who scored Trust a 1" is the exact re-identification §13.5–§13.6 exists to prevent.
- **Actions are entered by an admin, not generated.** §11 and §17.1 are *diagnose before prescribing*;
  the system proposes **response categories**, a human commits to an action. A row may be *seeded from*
  a catalogue entry, and then carries `source_catalogue_id` so the provenance survives.
- **The next wave's trend section reports actions alongside the change** (§6.3), under §16's caveat:
  completion plus improvement is not proof of cause. It prints both and refuses to join them into a claim.

### 3.10 `ai_cost_events` — because the nav already has the tab

§7.2 gives L1 a *Usage & Costs* tab and §6.4 inherits a metered Anthropic call, but v1 named no table.
Inherit trainiq's shape unchanged: `(org_id, cycle_id, report_type, model, input_tokens, output_tokens,
cost_usd, created_at)`, written on every narrative generation, never on a deterministic render. `report_events`
records *that a report was produced* for audit; this records *what it cost*. Keep them separate — one is a
compliance record and must never be purged with the other.

---

## 4. One link, four domains, and survey fatigue

### 4.1 Domain is an attribute of the question, not of the deployment

In trainiq a deployment *is* `(domain, assessment_type)` because its domains are independently licensed
modules bought à la carte. Here the four domains are **one instrument** — a satisfaction diagnostic
without GEM is not a diagnostic. So: one deployment, 48 items, **one link**. This removes the problem
rather than mitigating it, and it is why §3.4 restructures the deployment table rather than inheriting it.

### 4.2 Fatigue mitigation inside the one form

1. **Sectioned single-page form, four steps plus a short segment step.** One domain per step, 12 items
   visible, the domain's purpose as the section intro. A `1 of 4` progress bar — trainiq's
   `survey/form.html` already has the pattern; extend it.
2. **Server-side save-and-resume** against the anonymous session (§3.5). Only final submit sets
   `submitted_at` and increments the count.
3. **Honest time estimate on the landing page.** 48 Likert items ≈ 8–11 minutes. Saying so measurably
   reduces abandonment; hiding it does not.
4. **The landing page carries the §9.2 disclosures before the first question**: purpose, how results will
   be used, who sees what, the minimum reporting threshold, the anonymity claim stated honestly (§3.5),
   the non-retaliation commitment, and what the organisation intends to do next. This is a spec
   requirement, not a nicety — it is the thing that makes the responses worth having.
5. **Group by domain, do not randomise.** Within a domain, interleave the three subdomains rather than
   blocking them — it reduces straight-lining, and nothing on the form names a subdomain.
6. **No leadTo chrome, no "tracked/monitored" language** on the form, per the inherited privacy rule.

### 4.3 `assessed_domains` as a product lever

A pulse wave may legitimately run GEM plus one diagnostic domain (24 items ≈ 5 minutes). Set at cycle
creation, **frozen on the first response** — otherwise two waves become incomparable and the trend report
in §6.3 breaks. The trend section explicitly refuses to trend a domain absent from either wave rather
than plotting a gap as a drop.

---

## 5. Scoring and analysis engine (`app/analytics/scoring.py`)

Pure Python, session-injected, **no Flask import** — keep trainiq's discipline so it stays unit-testable
without an app context.

### 5.1 The aggregation ladder

Every level excludes `not_applicable` from **both** numerator and denominator.

1. **Item score** — one session's raw 1–5 (reverse-scored as `6 − score` if flagged).
2. **Subdomain score** — mean of that session's ≤4 answered items in `(domain, subdomain)`. Require
   **≥2 answered items**, else the cell is `Insufficient` for that session and excluded upward.
3. **Domain score** — **mean of that session's three subdomain scores** (`DOMAIN_SCORING_METHOD =
   'mean_of_subdomains'`). §10.2 does not mandate a method; it mandates that the organisation
   **document which one it uses**, because the two diverge when completeness varies. So:

   - `SUBDOMAIN_MIN_ITEMS = 2` and `DOMAIN_SCORING_METHOD` are **named constants in `app/analytics/scoring.py`**,
     not implicit consequences of how the SQL happened to group.
   - **Both methods are implemented** — `mean_of_subdomains` and `mean_of_items` — behind that one
     constant, because a client who has already documented the other rule in their own HR policy must be
     accommodated without a code fork. The constant is **frozen per cycle** (store it on
     `measurement_cycles.scoring_method`), otherwise two waves are silently incomparable — the same
     hazard as §4.3's `assessed_domains`.
   - The method in force is **printed in the methodology section of every report**, and mirrored in
     `docs/scoring_rule.md`. A score whose rule is not on the page is not an auditable score.
   - `tests/test_scoring_methods.py` pins a fixture where the two methods **disagree**, so a refactor
     that collapses them into one is a failing test rather than a quiet change of everyone's numbers.
4. **Group score** — **mean of the per-session domain scores**, not the mean of all raw responses.
   Otherwise a respondent who answered everything outweighs one who used N/A. Write this in the comment;
   it is exactly the kind of thing that gets "simplified" back.
5. **Distribution** — the full 1–5 frequency count per item and per subdomain, plus % favourable
   (4–5), % unfavourable (1–2), % neutral. §10.5 makes the **distribution the primary display**, not the
   mean. A 3.0 made of all-3s and a 3.0 made of half-1s and half-5s are different organisations.
6. **Dispersion** — σ and IQR per group. High dispersion inside a department is a finding (a split team),
   and the report says so as a prompt to investigate, never as a data-quality warning.
7. **Coverage** — N/A count and % per item, subdomain and group, feeding a Coverage section.
   A PIE item with 30% N/A means people do not know what peers earn — itself a finding about pay
   transparency, and worth naming.
8. **No GEM composite.** `gem_composite()` does not exist until §15 validation supports it. If a caller
   wants one, the function raises with the §10.3 reason in the message rather than returning a number.

### 5.2 Association analysis (§10.4) — the diagnostic layer

This is the product. Computed **deterministically in Python**, never by the model.

- **Per-respondent correlation** between each diagnostic subdomain (9 of them) and each GEM subdomain
  (3), across sessions within the group — Pearson `r`, with `n` and a 95% CI. Requires
  `n >= ASSOCIATION_MIN_N` (default **30**; below it, see §11.2).
- **Group contrast** — mean diagnostic score among the lowest-GEM tertile vs the highest, which answers
  §10.4's questions in the literal words it asks them ("Do employees reporting lower overall satisfaction
  also report lower autonomy?") and is far easier for a non-statistical reader than `r`.
- **Priority view** — each subdomain plotted on *current score* (x) against *strength of association with
  GEM* (y). Low score plus strong association = investigate first. **Label the quadrants in investigative
  language** ("Investigate first", "Monitor", "Maintain", "Lower priority"), never in verdict language,
  and print the §10.4 caveat beneath it.
- **Every association output carries the §10.4 sentence** — *"Such patterns indicate associations and
  areas for investigation. They do not, by themselves, establish that one factor caused another."* — as
  a rendered constant imported from one place, so a new chart cannot omit it by forgetting it.
- **Segment comparison** — group means and distributions by one segment at a time, min-N enforced,
  reporting the spread across groups rather than a league table. No ranking of departments.

### 5.3 Bands and colours — deliberately deferred

`scoring.band()`, which always raises.

While it is `False` (v1, and until pilot evidence says otherwise, §10.5):

- Reports show **score + distribution bar + comparison to the org mean and to the previous wave**.
  No word like "Low", "Moderate", "High", "Critical", "Gap".
- Colour encodes **the response category** (the 1–5 anchors, a diverging unfavourable→favourable ramp),
  **not a judgement of the score**. This keeps the visual language of trainiq and promote — same type,
  same chart furniture, same footer — while refusing the one thing the spec forbids.
- The relative comparison is where the eye goes: *this group vs org, this wave vs last*. Relative
  statements are defensible before validation; absolute ones are not.

When the pilot produces evidence, flipping the constant to `True` turns on a band table seeded from that
evidence. Build the switch now; do not guess the thresholds.

> Reuse the palette module from promote so the three products read as one system — but bind the colours
> to anchors, not to bands, and derive everything from one table so two copies of a hex can never disagree.

### 5.4 GEM subdomain discriminant check (§15, §18)

§15 and §18 both single this out: **General Job Satisfaction and Emotional Experience must not turn out
to be the same thing in slightly different words.** A standing analysis, available from phase 6 and
surfaced in the HR report and the validation report:

- inter-item correlation within each subdomain (internal consistency, Cronbach's α);
- correlation *between* the three GEM subdomains — a near-unity correlation is a finding to act on, not
  a nice result;
- item-total correlations, flagging any item that does not move with its own subdomain;
- N/A rate and straight-lining rate per item (a high N/A rate often means a confusing item, not an
  inapplicable one).

This is not an extra; §15 makes it a precondition for calling the instrument validated, and §18's product
note repeats it. It is also cheap — the data is already per-respondent.

---

## 6. The three reports (§12)

The spec defines **three** audiences, not four. Plus one internal.

| # | Report | Audience | Scope | trainiq/promo analogue |
|---|---|---|---|---|
| 1 | **Executive Report** | Senior leadership | whole cycle, org (or a branch) | Gap Report, trimmed |
| 2 | **HR & OD Report** | HR / OD / diagnostic teams | whole cycle, all detail | Gap Report + full analytics |
| 3 | **Management Report** | A manager, their team | one group within a cycle | Cycle Report, per-group |
| 4 | **Validation Report** (internal, L1) | Technolysi | one cycle, psychometrics | new — §5.4, §15 |

### 6.1 Shared layout (inherit from the TNA PDFs)

Same cover band, numbering, typography and footer, so the three products are visibly one brand.

- **Cover band**: `LEADTO · CONFIDENTIAL`, title, subtitle, metadata strip —
  `ORGANISATION · CYCLE · SCOPE · RESPONSES (n/N) · GENERATED`.
- Numbered sections. Per-item distribution bars with the anchor labels, ranked subdomain bars,
  boxed `ANALYSIS` / `AREAS TO INVESTIGATE` call-outs, grey footer line.
- **Report Output Parity** (inherited rule): the PDF and XLSX of the same thing agree on title, brand and
  wording. Build the title from one function; never a literal string.
- **Reports never mention AI, Claude or Anthropic.** The narrative section is "Interpretation".
- **Every report footer carries `n`, the response rate and `MIN_REPORTING_N`.** A number without its n is
  how a 4-person finding becomes a board slide.

### 6.2 Section maps

**1 · Executive Report** (§12.1) — short, decision-oriented, no statistics lecture.
1. Executive Summary · 2. Method & Participation (n, response rate, thresholds, limitations up front) ·
3. Overall Satisfaction — the three GEM subdomains **reported separately**, with distributions ·
4. Key Findings across Task, Relationships and Reward · 5. Material Patterns and Group Differences
(one segment at a time, suppressed cells shown as suppressed) · 6. Areas Requiring Further Investigation ·
7. Priorities and Possible Response Categories (§11) · 8. **Limitations and What This Does Not Show** —
a required section, not an appendix.

**2 · HR & OD Report** (§12.2) — the full instrument.
1. Executive Summary · 2. Methodology, Scoring Rule and Participation · 3. Domain and Subdomain Scores
with distributions · 4. Item-by-Item Analysis (48) · 5. Group Comparisons (every segment, min-N) ·
6. **GEM ↔ Diagnostic Association Analysis** (§5.2) · 7. Coverage & N/A Analysis ·
8. Dispersion and Split Groups · 9. Intervention Pathways (§11, by type) ·
10. Follow-up Assessment Recommendations · 11. Limitations.

**3 · Management Report** (§12.3) — one team, actionable, and the most privacy-sensitive.
1. Your Team at a Glance (n, response rate, suppression notice) · 2. Strengths · 3. Areas Requiring
Attention · 4. How Your Team Compares with the Organisation (difference, not rank) ·
5. Practical Management Actions (from §11, filtered to what a manager can actually change — a manager
cannot fix a pay structure, and offering it as "your action" is worse than silence) ·
6. Questions for a Constructive Team Discussion · 7. Follow-up Commitments.

> **Managers receive no individual responses, ever** (§12.3, explicit), including free-text verbatims if
> comments are added later. If the team is below `min_reporting_n`, the report **does not generate** and
> says why, with the aggregate rolled up to the parent group instead. Gate it in `app/analytics/`, and
> pin it: `tests/test_manager_report_suppression.py`.

**Removed.** A fourth, L1-only Validation Report was planned here. It is not specified anywhere in the
PDF — §12 defines three reports — and it has been removed from the product. Instrument validation
(§15, §18) is carried out by other means, outside this system.

### 6.3 Trend, once a second wave exists (§3.2.7, §16)

A trend section appears in reports 1–3 when `previous_cycle_id` is set and both waves share the domain.
It reports change in score **and in distribution**, with n for both waves, and prints the §16 caveat:
improvement after an intervention does not by itself prove the intervention caused it. Consider
composition change — a wave with a different respondent mix is not the same population.

### 6.4 AI usage

Inherit trainiq's settled posture exactly: **manual trigger per report**, never auto-dispatch; system
prompt loaded from `app/prompts/system_prompt.txt` as a file, never inlined; retry 3× with exponential
backoff on HTTP 529; log and raise on JSON parse failure, never fall back to `{}`. Every quantity is
computed deterministically in Python and merely **narrated**.

Four additions to the system prompt, all load-bearing:
- never assert causation, and never use `causes / drives / because of / leads to` about an association;
- never apply a band label or a verdict;
- never interpret low `D` (Daunting) as stress or overload (§2.3);
- never recommend training for a subdomain whose §11 categories do not include it, and never present
  training as a response to a compensation finding (§8.5).

---

### 6.5 Value measurement and the follow-up loop (§16)

§16 lists five value measures. Three are in the product, two are deliberately out, and the plan should say
which is which rather than implying all five:

| §16 measure | v1 |
|---|---|
| Changes in job satisfaction scores over time | **In** — trend section (§6.3) |
| Changes in the subdomains identified as priorities | **In** — priority view (§5.2) re-run per wave |
| Completion of agreed improvement actions | **In** — `improvement_actions` (§3.9) |
| Employee perceptions of changes implemented | **In, as a pulse** — a GEM-plus-one-domain wave (§4.3), not a new instrument |
| Retention, absence, engagement or operational indicators | **Out of v1.** Ingesting HR operational data contradicts §13.2 data minimisation and makes the platform a holder of identifiable workforce records. The HR report **names the measures the client should look at on their own systems** and stops. |

That last row is a decision, not an omission — record it as one.

### 6.6 Ecosystem routing (§14) — output, not integration

§14 is a real requirement and v1 ignored it. It is also the one place the three leadTo products touch, so
it needs a stated boundary: **no data crosses between products.** The HR and Executive reports close with a
*Where this points next* section that names the appropriate follow-on — a focused diagnostic (trainiq TNA),
a work-design or operational review, leadership development (promo), culture work, capability development
**only where §11's categories support it**, or a compensation/policy review. It is a rendered list derived
from the §11 map and the priority view, and it never pre-fills a trainiq cycle, never exports a respondent,
never shares a database. The sales motion is a human reading a sentence.

### 6.7 Pilot instrument feedback (§15)

§15 asks whether respondents *"find any questions repetitive, confusing or difficult to answer"* and whether
they *"understand the questions consistently"*. §5.4's psychometrics are a **proxy** for that — a high N/A
rate tells you an item is a problem, not why. Add a **pilot mode** on the deployment
(`is_pilot`, default false): when set, the form appends one optional free-text block at the very end —
*"Were any questions unclear, repetitive or difficult to answer?"* — written to `pilot_feedback`, a table
**L1-only**, never in a client report, purged with the cycle.

This is the one place free text exists in v1 (§11.3 keeps it out everywhere else) and it is confined to a
pilot wave, read by Technolysi, about the instrument rather than about the employer. That is a different risk
profile from employee comment boxes, and it is what §15 requires to call the instrument validated.

---

## 7. Roles, permissions and UI

### 7.1 Tiers

Inherit `permissions.py` **verbatim** — pure Python, no Flask import, `can(user, CAPABILITY)` only,
**never an inline role comparison**. Roles cumulative.

| | L1 `master_admin` | L2 `org_admin` | L3 `branch_admin` |
|---|---|---|---|
| Creates | L2 org admins | L3 branch admins | — |
| Scope | all orgs | whole org | one branch |
| Templates | **edit** master + per-client | view only | view only |
| Deploy | anywhere | org-wide | **branch only** |
| Reports | all, read-only | whole org | own branch only |
| Segments | — | **define & freeze** | view |
| Catalogue, Usage & Costs, Audit, Validation Report | ✓ | — | — |

New capabilities: `MANAGE_CYCLES`, `MANAGE_SEGMENTS`, `VIEW_VALIDATION_REPORT` (L1), `MANAGE_CATALOGUE`
(L1), and `LOWER_MIN_N` — **which exists and is never granted**, so that an attempt to weaken suppression
is a code change and an audit event rather than a setting.

Keep trainiq's one deliberate non-cumulative rule: **L2 deploys org-wide, L3 deploys to its own branch,
and L2 does not originate a branch-targeted deployment** — otherwise two tiers create duplicate waves
with no way to tell which is authoritative. The reasoning is in `permissions.py`; copy the comment.

**Respondents are not `users`.** They reach the system only through the anonymous link.

### 7.2 Screens

Same dark navy bar, orange `LeadTO>>` wordmark, orange active-tab pill, card-and-rail layout.

**L1:** `All Orgs · Templates · Catalogue · Users · Usage & Costs · Audit · Validation · + New Org`
KPI strip and Organisation Status Board as trainiq, with traffic lights on the same thresholds
(green < 60 days since the last wave, amber 60–90, red > 90 or never).

**L2:** `Dashboard · Cycles · Segments · Deployments · Branches · Reports`
- *Dashboard* — participation card (responses / expected / rate, by branch); the four domain cards
  showing **score + distribution bar**, GEM's three subdomains shown separately and labelled as the
  outcome; a "Where to look first" card driven by §5.2's priority view, worded investigatively.
- **Do not put a single "Organisational Satisfaction 3.4/5" headline at the top.** §10.3 forbids the
  composite and §10.5 forbids the implied band. The visual slot and styling are preserved; the metric
  becomes participation plus the three GEM subdomains side by side.
- *Segments* — define and freeze the reporting dimensions before a cycle opens.
- *Reports* — grouped by cycle, with Branch / Segment / Status / Search filters, per-scope rows with
  response-rate bar and PDF/Excel buttons, and a "needs: …" line when a report is not yet generatable —
  trainiq's `compute_impact_missing` pattern, which deliberately separates *not deployed* from *awaiting
  responses* because they need different actions. Add a third state here: *suppressed — n below threshold*.

**L3:** same minus *Branches* and *Segments*, templates read-only, `Showing results for <Branch> only.`
subtitle, every query branch-scoped.

> Branch isolation comes from **route-level scoped queries** (`_dep_query()` / `_apply_branch_scope()`),
> not from RLS. Trainiq's `CLAUDE.md` is explicit that the app connects as the table owner and bypasses
> RLS entirely — the policies shield the Supabase anon key and nothing more. Never write branch-aware RLS
> predicates or treat a policy as a substitute for a scoped query.

---

## 8. Privacy, consent and retention (§13)

Stronger than either sibling product, because the whole instrument depends on candour.

- **Kenya Data Protection Act 2019.** Even de-identified, this is employee feedback: the client org is
  controller, Technolysi processor. The engagement contract needs a processor clause. Write
  `docs/privacy_notice_template.md` and `docs/dpa_processor_clause.md` **before the first real client
  wave**, not after.
- **The anonymity claim is stated honestly**, per mode (§3.5), and the app's own words are checked by a
  test — a template that says "completely anonymous" on an `open_link` deployment is a bug.
- **Minimum reporting thresholds and complementary suppression** (§2.6) enforced in analytics.
- **Role-based access**, audit on every report generation and every export (`report_events`).
- **Retention per cycle, clock starts when the cycle CLOSES.** `retention_months` NOT NULL, default 24,
  bounded 3–120: a nullable column means "not set", which in practice is indefinite — the state §13.8
  forbids, and an unbounded field is how "indefinitely" gets entered as 9999. **Nothing deletes on a
  timer** — there is no background worker in this product, and a silent purge is worse than a prompt.
  An overdue cycle surfaces as a warning on the L1 board with a one-click, confirmed, audited purge.
- **No use in individual employment decisions** (§13.9, §17.5). There is no screen, export or API that
  returns an individual response. That is a property of the schema (§3.5), not of a permission check —
  which is the only form of this guarantee worth making.
- **Non-retaliation** is stated on the landing page and in the purpose statement the org admin writes at
  cycle creation (`measurement_cycles.purpose_statement` is NOT NULL for a reason).
- RLS enabled on every table in `public` once on Supabase; `org_isolation` policies on every org-scoped
  table including `response_sessions` and `survey_responses`.
- The survey form must never reveal `subdomain_code`, `item_code`, any `domain_code` beyond the four
  public domain names, or any leadTo chrome. After touching the template, view the rendered HTML source
  and confirm.

---

## 9. Infrastructure — GitHub, local, Supabase, Render

Deliberately sequenced: **everything works on localhost against local Postgres before anything is
migrated.** That is the right constraint — promote's Render service had to be deleted and recreated
because it was created against an empty repository and Render fixed its language at creation.

### 9.1 GitHub (phase 1, day one)

- New repo `leadto-jobsat`, **private**, default branch `main`.
- `.gitignore` from promote: `venv/`, `__pycache__/`, `.env`, `logs/`, local DB dumps, generated report
  artefacts.
- **`.env` is never committed.** `.env.example` is, with every key present and no value.
- Branch protection on `main` once CI exists: require the test suite green.
- GitHub Actions `ci.yml` from phase 1: `pip install -r requirements.txt`, `pytest`, against a
  `postgres:16` service container. The privacy and suppression tests (§2.4, §2.6) are the point of
  having CI at all.
- Commit this plan and the spec PDF to `docs/` so the reasoning travels with the code.

### 9.2 Local development (phases 1–9)

- Python 3.13.3, pinned in `.python-version` **and** `render.yaml` — they must match.
- Local Postgres 16, database `jobsat_dev`, plus `jobsat_test` which the suite creates and drops.
  **Never SQLite** — the schema uses `ARRAY`, `JSONB`, computed columns and a partial unique index, and a
  suite that passes on SQLite proves nothing about any of them.
- `requirements.txt` copied from promote and pinned (same WeasyPrint / matplotlib / plotly / openpyxl
  stack — the reports are the same shape, so it carries over unchanged). Add `scipy` **only if** §5.2's
  CIs need it; Pearson `r` and a Fisher-z interval are a dozen lines of `numpy`, and `scipy` is a heavy
  dependency on a 512 MB free tier.
- `config.py` with `DevelopmentConfig` / `TestingConfig` / `ProductionConfig`; production **refuses to
  boot** without `DATABASE_URL`, `FLASK_SECRET_KEY` and `SURVEY_BASE_URL`. A web process that starts
  without a database is worse than one that refuses to start.
- `SURVEY_BASE_URL` is load-bearing: `external_url()` builds every survey link from it, **not** from the
  request Host header, so an admin on `*.onrender.com` still copies links that say `jobs.leadto.co.ke`.

### 9.3 Alembic

- Own chain from the first commit — `alembic revision --autogenerate` after every model change, reviewed
  by hand before it is applied. Autogenerate is a draft, not an answer.
- **No `include_object` filter and no shared schema.** If someone later proposes sharing a database with
  trainiq or the hub, the answer is no: trainiq already carries that scar and its `migrations/env.py`
  documents the cost.
- Every migration runs forward **and** backward on local Postgres before it is committed.

### 9.4 Supabase migration (phase 10)

1. New Supabase project, **region `eu-central-1` (Frankfurt)** to match the Render region — cross-region
   latency on a synchronous report render is the difference between a 10s PDF and a timeout.
   A **new project**, not a new database inside an existing one.
2. `DATABASE_URL` must be the **session-mode pooler** URI, not the direct one:
   `db.<ref>.supabase.co` resolves IPv6-only and its username is plain `postgres`, whereas the pooler
   wants `postgres.<project-ref>`. This exact thing cost promote a deploy.
3. Run the Alembic chain against the empty Supabase database — never `pg_dump` the dev database into it.
   The chain is the schema of record.
4. **Then** enable RLS on every table in `public` and apply `org_isolation` policies. The app connects as
   the table owner and bypasses RLS; the policies exist to shield the anon key (trainiq's note).
5. Seed master templates and the catalogue, provision the first org, and run `test_branch_scoping.py` and
   `test_suppression.py` **against the real database**.
6. Keep local Postgres as the dev target afterwards. Do not develop against production.

### 9.5 Render (phase 10)

- **Create the service from `render.yaml` as a Blueprint.** Do not create it by hand in the dashboard —
  a hand-created service keeps its settings in the dashboard, ignores this file completely, and has its
  language fixed at creation. Promote's service had to be destroyed and recreated for exactly this.
- `runtime: python`, `plan: free`, `branch: main`, `region: frankfurt`,
  `buildCommand: pip install -r requirements.txt`,
  `startCommand: python -m gunicorn run:app --timeout 120`.
  `python -m gunicorn`, not bare `gunicorn` — it depends only on gunicorn being importable, never on the
  console script being on PATH. `--timeout 120` is load-bearing: report generation is synchronous inside
  the request by design, and gunicorn's 30s default kills the worker mid-render and returns a 502.
  No `--bind` (gunicorn binds `0.0.0.0:$PORT` already); one worker on the free tier, revisit with the plan.
- `healthCheckPath: /login`.
- Env: `PYTHON_VERSION=3.13.3` (matching `.python-version`), `FLASK_ENV=production`,
  `SURVEY_BASE_URL=https://jobs.leadto.co.ke`, `LOG_TO_FILE=False`, `LOG_TO_STDERR=True`, `LOG_LEVEL=INFO`.
  Secrets `sync: false`, set in the dashboard: `FLASK_SECRET_KEY`, `DATABASE_URL`, `ANTHROPIC_API_KEY`.
- **DNS**: `jobs.leadto.co.ke` CNAME to the Render service, custom domain added in Render first so the
  certificate issues. Verify the app serves over HTTPS and that a copied survey link carries the custom
  host, not the `onrender.com` one.
- Auto-deploy from `main` only once CI gates it.

---

## 10. Repository conventions — inherit these as rules, not as style

Both siblings carry these in a `CLAUDE.md` at the repo root, and that file is the reason a second developer
(or a second session) does not re-litigate settled decisions. **Write `leadto-jobsat/CLAUDE.md` in phase 1**,
before the first route.

**ONE FILE RULE (non-negotiable).** All SQLAlchemy models in `app/models.py`; all scoring in
`app/analytics/scoring.py`; all charts in `app/analytics/charts.py`; all report generators in
`app/analytics/reports.py`; all role rules in `app/permissions.py`; all domain/subdomain/anchor/§11-map
constants in `app/domains.py`; all filter-bar UI in `static/js/filters.js` + the `.filter-bar` block.
Edit in place — no `scoring_new.py`, no `models_v2.py`.

**Tech stack, pinned to the siblings** so one developer can move between all three: Python 3.13.3, Flask 3.x,
Postgres + SQLAlchemy 2.x + Alembic, Plotly in-page and **matplotlib for PDF PNGs** (no `kaleido`),
WeasyPrint, openpyxl, Flask-Login + bcrypt, Anthropic SDK manual-trigger. **No Celery, no redis, no
background worker** — they were deliberately removed from trainiq and every job runs synchronously inside
the request (which is why `--timeout 120` in §9.5 is load-bearing). Add a dependency only when something
imports it, then `pip freeze > requirements.txt`.

**Folder layout** as promo's, with `assess/` renamed `survey/` (no login, the anonymous link) and
`candidates`/`curriculum` replaced by `cycles`, `segments` and `catalogue`.

**Precedence when two sources conflict:** the Master Spec v3.0 PDF > this plan > trainiq/promo precedent.
This inverts promo's order, deliberately: promo had 21 build guidelines that post-dated its PDF, and this
product has no such layer — the PDF is the only authority, and the siblings are a chassis, not a spec.

**Build discipline:** quote the spec section, do not paraphrase it; run the named acceptance test and report
the result *including when it fails*; commit at logical checkpoints.

**When in doubt:** if a trainiq or promo pattern seems to fit, check whether it assumes an **identified
respondent**. If it does, it is probably wrong here — this product pools anonymous sessions, and that
difference is where the bugs will be. (Promo's own note says the opposite, for the opposite reason. Both
are correct about their own product; that is the point.)

### 10.1 Sibling bugs worth paying for once

Promo rediscovered these during its phases 4–10. They cost a day each and all of them apply here:

- **`app.analytics.reports` must be imported as `report_builder`** in the org blueprint — that module
  defines a view called `reports()` which shadows the module at import time.
- **Jinja macro imports need `with context`** — a bare `{% import %}` sees no `current_user`, so `can()`
  raises inside any shared macro.
- **`tests/conftest.login()` must clear Flask-Login's `g._login_user`** — the `db` fixture pushes one app
  context per test and Flask reuses it, so a second login in one test is silently a no-op and an
  authorisation test passes as the wrong user. This is the single worst class of green-but-wrong test.
- **Uniqueness goes in the database, not the route** — two admins pressing Save is a race; §3.4's partial
  unique index is the fix, and the route check is only a nicer error message.
- **L1 has no `org_id`**, so any capability L1 alone holds (`LOWER_MIN_N`, purge, validation report, the
  §3.9 cross-org view) cannot live in a blueprint whose every query is scoped by `current_user.org_id`.
  Put the purge path in the admin blueprint from the start.
- **`app/catalogue.py` returns rows with the lookup key stripped** — the dimension stops at the module
  boundary so no template can render it by accident, rather than relying on a leak test as the only
  guard. Here the analogue is `item_code` and `subdomain_code` on anything the survey form touches.

---

## 11. Phased delivery

Each phase ends with a **runnable system and a named acceptance test**. Dashboards land in phase 2, not at
the end — the shell has to be visible and navigable while the engine is built behind it, otherwise there
is nothing to look at for six phases.

| # | Phase | Deliverable | Acceptance |
|---|---|---|---|
| 1 | **Scaffolding** | GitHub repo, Flask factory, `config.py`, logging, error handlers, TNA theme, `app/domains.py` (4 domains, 12 subdomains, §11 map, anchors), full schema, `permissions.py`, three-tier nav, L1 status board, L2/L3 dashboard shell, Alembic chain, CI | `python run.py` serves login locally; `/` redirects; unauthenticated `/admin/` bounces to login; suite green in CI |
| 2 | **Identity, tenancy & live dashboards** | Local Postgres connected, schema applied, org wizard, branch CRUD, L2/L3 user creation, branch scoping on every query, audit log | `test_branch_scoping.py` green against local Postgres 16 |
| 3 | **Taxonomy & instrument** | `survey_items` seeded (48 from the PDF §5–§8 now; re-seeded from the workbook when it lands), master-template cloning, L1 editor, L2/L3 read-only viewer | 48 items, 4 per subdomain, every `item_code` unique and well-formed; a short subdomain aborts the seed naming the line |
| 4 | **Cycles & segments** | Measurement cycle CRUD, `assessed_domains`, purpose statement, `min_reporting_n`, `retention_months`, segment definition and freeze | two open cycles for one org+branch rejected by the DB; segments frozen after the first response; `assessed_domains` frozen likewise |
| 5 | **Deployment & the anonymous form** | One deployment per cycle, one link, 4-section form plus segment step, save-and-resume on the anonymous session, N/A on every item, `open_link` and `ticketed` modes, the §9.2 landing page | rendered HTML carries no internal key and no identity field; a used ticket is rejected; the anonymity wording matches the mode |
| 6 | **Scoring engine** | Full §5.1 ladder, N/A exclusion, distributions, dispersion, coverage, suppression, no band labels | unit tests with **no Flask context**: a respondent using 12 N/A neither gains nor loses weight; a hand-computed fixture reproduces exactly; `gem_composite()` raises; a group of 4 suppresses, and its sibling suppresses too |
| 7 | **Association engine & Reports screen** | §5.2 correlations, tertile contrast, priority view, segment comparison; cycle-grouped reports screen with the three-state "needs: …" line; on-screen Plotly | an association below `ASSOCIATION_MIN_N` is labelled or withheld; the §10.4 caveat is present on every association output, asserted by test |
| 8 | **Reports 1 & 2** | Executive + HR/OD, PDF and XLSX, charts, limitations sections | PDF/XLSX titles and wording identical; renders when a whole domain is N/A for a third of respondents; `test_privacy_and_wording.py` green on a populated render |
| 9 | **Report 3, trend & follow-up** | Management report with a hard suppression gate; trend section once a second wave exists; `improvement_actions` CRUD and the follow-up section (§3.9, §6.5) | a 4-person team produces no report and says why; a wave missing a domain is refused, not plotted as a drop; an action naming a respondent fails `test_privacy_and_wording.py` |
| 10 | **Validation, catalogue, Supabase & Render** | Validation report (§5.4), `intervention_catalogue` + seeder, interventions in reports 1–3, retention/purge path, Supabase migration, Render Blueprint cutover, `jobs.leadto.co.ke` DNS | α and inter-subdomain correlations computed on a seeded fixture; recommendations deterministic and type-filtered on repeat runs; `FLASK_ENV=production` serves over HTTPS on the custom domain; `intervention_map.json` is signed off and every §11 row's type is traceable to quoted prose |
| 11 | **Pilot wave** | Pilot mode and `pilot_feedback` (§6.7), `ai_cost_events` + Usage & Costs tab | nothing is marketed as validated before instrument validation has been carried out separately (§18 product note); the product ships no band labels and no GEM composite either way |

**Phase 6 is the product — write its tests before its code.** Phases 3 and 10 absorb the survey workbook
and the curriculum when they arrive; nothing earlier blocks on them.

---

## 12. Open decisions

**Settled in v2** (previously implicit or absent):

- **Domain scoring** is `mean_of_subdomains`, both methods implemented behind one constant, frozen per
  cycle and printed in every report's methodology section (§5.1.3). §10.2 requires the documentation, not
  a particular method.
- **`§16` value measurement** gets a table (`improvement_actions`, §3.9) and an explicit scope line: HR
  operational indicators (retention, absence) are **named, not ingested** (§6.5).
- **`§14` ecosystem routing** is a rendered *Where this points next* section. No data crosses products (§6.6).
- **`§15` respondent feedback** exists, confined to a pilot wave, L1-only, never in a client report (§6.7).
- **`intervention_type`** is a derived classification requiring product-owner sign-off against quoted §11
  prose, not a regex (§3.8).
- **Repo conventions and the precedence order** are written down in phase 1, PDF-first (§10).

**Still needing a decision:**

1. **Does a GEM composite ever ship?** §10.3 says only after validation. Proposal: no composite in v1;
   the validation report recommends, and a later flag enables it. *Needs confirmation — it is the single
   thing clients will ask for most ("just give me one number"), and saying no is a sales conversation,
   not a technical one.*
2. **`ASSOCIATION_MIN_N` — 30, and what happens below it.** Withhold entirely, or show with an
   *Indicative only* flag? Recommend: show the tertile contrast (robust, readable) and withhold `r`.
3. **Free-text comments.** §12/§13 do not mention them, and they are the single largest re-identification
   risk in any employee survey. Recommend: **not in v1.** If wanted later they need their own design —
   redaction, no manager access, L1-only review.
4. **Cross-segment reporting** (department × tenure). Phase 11, or never without a privacy review?
5. **`min_reporting_n` default** — 5 is the common standard; some firms use 7 or 10. Confirm 5, and
   confirm it can only be raised.
6. **Pilot client and sample size for §15 validation.** The instrument cannot be marketed as validated
   until this runs (§18 product note). Who, and when?
7. **`open_link` vs `ticketed` default** for the first real wave.
8. **Does an org admin get to see `improvement_actions` across branches?** The table is org-scoped, but a
   branch admin's actions rolling up to an org view is a management-visibility decision, not a privacy one.
   *Recommend: yes, L2 sees all branches; L3 sees its own. Confirm.*
9. **Is `mean_of_items` ever offered to a client, or is the second implementation purely a hedge?**
   *Recommend: built, tested, not exposed in the UI in v1 — changed by L1 only, audited, and refused once a
   cycle has responses.*
10. **Pilot free text (§6.7) — does the client org ever see it?** *Recommend: no. It is instrument feedback
   for Technolysi. A client who sees it will read it as employee comment, which is the thing §11.3 excludes.*

---

*v2, written against the Master Spec v3.0 (§1–§18) read in full, the trainiq codebase, `promo/CLAUDE.md`
and `promo/docs/PROMOTE_BUILD_PLAN.md` (phases 1–10 complete) as of 2026-10-14.
Survey workbook and intervention curriculum outstanding — phases 3, 10 and 11 are built to receive them.*

**v2 changelog:** §3.8 type provenance · §3.9 `improvement_actions` · §3.10 `ai_cost_events` ·
§5.1.3 scoring-method constants and both implementations · §6.5 §16 value scope · §6.6 §14 routing ·
§6.7 §15 pilot feedback · §10 repo conventions, precedence and inherited sibling bugs ·
phase 9 extended, phase 11 added · §2.4 band citation corrected to §10.5.
