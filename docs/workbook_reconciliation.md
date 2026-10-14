# Supplied workbook — reconciliation against the spec

Source: `docs/LeadTO➡️ Job Satisfaction Analysis System.xlsx`
Checked: 2026-10-14, against the Master Product Concept v3.0 PDF §5–§8 and §11.

The build plan (§3.6) said to treat the PDF items as authoritative and the
workbook as a draft to reconcile item by item, and budgeted a data-prep step
because *"every leadTo workbook so far has arrived with Excel-mangled codes and
at least one bad row."*

**That budget was mostly not needed. The wording is clean.** What differs is
the coding scheme and the shape of the topic sheet.

---

## 1. All 48 question wordings match the PDF verbatim

Four sheets × 12 rows. Every one of the 48 found in the PDF after whitespace
and trailing-stop normalisation. **0 mismatches.**

This is unusual and worth saying plainly: the instrument content needs no
cleaning. Seed from either source and reconcile on code, not on text.

## 2. The workbook uses a different domain coding scheme

| Workbook | Ours (`app/domains.py`) | Spec reference |
|---|---|---|
| `OS` Overall Satisfaction | `GEM` | §4, §5 |
| `TS` Task Satisfaction | `TID` | §4, §6 |
| `RL` Relationships Satisfaction | `BIT` | §4, §7 |
| `RW` Reward Satisfaction | `PIE` | §4, §8 |

Subdomain letters are **identical** in both (`G E M` / `T I D` / `B I T` /
`P I E`), so the mapping is 1:1 and total.

Item codes also differ in shape: workbook `OS-G01`, ours `GEM-G-01`.

**Decision: keep `GEM`/`TID`/`BIT`/`PIE`.** §4 and §18 make the framework names
the product's client-facing vocabulary ("Through its GEM, TID, BIT and PIE
frameworks…"), and the workbook's `OS`/`TS`/`RL`/`RW` are domain-name
abbreviations, not framework names. The seeder maps workbook codes to ours on
ingestion; the alias table is the four rows above, and it belongs in
`seed_templates.py`, not scattered.

## 3. The workbook's subdomain labels carry the mnemonic — ours must not

The workbook writes:

- `Thrilling: Autonomy and Control`
- `Important: Purpose and Impact`
- **`Daunting: Appropriate Challenge and Development`**

Spec §6.4 is explicit that "Daunting" is a mnemonic for challenge and *"must
not be interpreted as excessive pressure, unmanageable workload or persistent
stress."* A client-facing label containing the word invites exactly that
reading, and the reader has no way to know it was a mnemonic.

`domains.SUBDOMAIN_LABELS` therefore carries `Appropriate Challenge and
Development`, and `tests/test_domains.py` asserts the label contains no
stress vocabulary.

**The seeder must ingest the question column only** — never the workbook's
`Subdomain Name` into anything rendered.

## 4. The topic sheet is a draft intervention catalogue

`Job Satisfaction Topic`: 29 rows, covering **all 12 subdomains**.

| | |
|---|---|
| Rows carrying a real topic code | 26 |
| Unique topic codes | 25 |
| Rows that are system notes, not topics | 3 (all Reward) |
| Source programmes | `PO` 9 · `CO` 9 · `LD` 6 · `WWO` 2 |

### 4a. The curriculum author independently reached §8.5's conclusion

The three Reward subdomains carry **no topic at all**. In place of a code, each
row carries a note:

> `RW-P` — *[System Note: Address via internal pay-equity review, not LMS training]*
> `RW-I` — *[System Note: Address via market benchmarking, not LMS training]*
> `RW-E` — *[System Note: Address via compensation review and communication, not LMS training]*

This matches, independently, what §8.5 requires and what
`domains.INTERVENTION_MAP` already enforces structurally: the three PIE rows
carry no `training` type, so `app/catalogue.py` cannot offer a course against a
compensation finding.

**These three rows are not catalogue entries and must not be seeded as such.**
A topic row with a null code and prose in the name column, ingested naively,
becomes a "course" whose title is a bracketed system note — printed to a client
as a recommendation. They are already represented by the §11 map; the seeder
drops them and `tests/test_catalogue.py` pins that the three PIE cells return
no training row.

### 4b. One topic serves two cells — and it is correct

`PO-EPS-T01` "Goal Clarity and Alignment" is mapped to both:

- `OS-M` → (GEM, 'M') Match with Overall Expectations
- `TS-I` → (TID, 'I') Purpose and Impact

The same intervention legitimately answers two different findings. This is
**not** the Excel-mangling defect the plan predicted.

It did, however, expose a real defect in the phase-1 schema: migration 0001 put
`domain_code`/`subdomain_code` on `intervention_catalogue` next to a unique
`intervention_code`, a shape that cannot represent it. Fixed in migration
**0002** by splitting the cells into `intervention_cells`. The code names the
TOPIC; the mapping names the CELL IT SERVES. Promo hit the same shape and
resolved it the same way.

---

## What the seeder must do (phase 3 and phase 10)

1. Map workbook domain codes to ours (§2 above); **abort** on an unknown one.
2. Ingest the **question column only**; never a `Subdomain Name`.
3. Reject a malformed cell — **never truncate**. A subdomain delivered with 3
   or 5 questions, an unknown domain or an unmapped subdomain aborts the seed
   naming the file and the line. A silently short subdomain rests a reported
   score on three items and is visible only as a wrong number, weeks later.
4. Drop the three Reward system-note rows from the catalogue; they are §11 map
   content, not topics.
5. Treat a repeated topic code as **one topic in several cells**, not as a
   duplicate to deduplicate away.
6. Assign `intervention_type` from the signed-off `docs/intervention_map.json`,
   never by a regex over prose (plan §3.8).

## Still outstanding

- The **final** survey workbook and curriculum, if they differ from these
  drafts. Nothing in phases 3–9 blocks on them: the PDF items are
  authoritative and identical, and the catalogue lands in phase 10.
- Sign-off on `intervention_type` per §11 row (plan §3.8), before phase 10.
