# Scoring rule

**Status:** in force from phase 6. Recorded here because spec §10.2 requires the
organisation to *document which method it uses* — not because it mandates a
particular one.

> §10.2: "The organization should document whether it uses the mean of all valid
> domain items or the mean of the three subdomain scores, particularly when
> missing responses occur. The two methods may produce different results if
> response completeness varies."

This file is the documentation that sentence asks for. It is mirrored in the
methodology section of every generated report, so a reader never has to find it.

---

## The ladder

Every level excludes `not_applicable` from **both numerator and denominator**.

| # | Level | Rule |
|---|---|---|
| 1 | Item | the raw 1–5, or `6 − score` where `is_reverse_scored` |
| 2 | Subdomain | mean of that session's answered items in the `(domain, subdomain)` cell |
| 3 | Domain | **mean of that session's three subdomain scores** |
| 4 | Group | **mean of the per-session domain scores** |

## The constants

Both live in `app/analytics/scoring.py` and nowhere else.

```python
SUBDOMAIN_MIN_ITEMS = 2
DOMAIN_SCORING_METHOD = 'mean_of_subdomains'   # or 'mean_of_items'
```

**`SUBDOMAIN_MIN_ITEMS = 2`.** A subdomain has four items. Below two answered,
the cell is `Insufficient` for that session and is excluded upward rather than
being computed from a single item. The spec does not set this number; it asks
only that missing responses be "handled according to a documented scoring
rule" (§10.1). This is that handling.

**`DOMAIN_SCORING_METHOD = 'mean_of_subdomains'`.** The default, and the one in
force unless a client has already documented the other rule in their own HR
policy.

Both methods are implemented. The constant is **frozen per cycle** on
`measurement_cycles.scoring_method`: changing it mid-stream silently changes
everyone's numbers and makes two waves incomparable, which is the one failure
§3.2.7 ("track changes over time") cannot survive.

`tests/test_scoring_methods.py` pins a fixture in which the two methods
**disagree**, so a refactor that collapses them into one is a failing test
rather than a quiet change to every reported figure.

## Why group score is a mean of means

Step 4 is the mean of the per-session **domain** scores, **not** the mean of all
raw responses in the group.

A respondent who answered all 12 items of a domain and one who answered 6 and
used N/A for the rest must carry **equal weight** in the group figure. Pooling
raw responses weights the first twice as heavily — silently, plausibly, and in
the direction that systematically under-represents whoever had least basis to
answer. On the Reward domain, where N/A is most common, those are exactly the
people whose view is most at issue.

## What is NOT computed

**There is no GEM composite.** §10.3: the three Overall Satisfaction subdomains
"should initially be reported separately. An overall GEM composite should only
be introduced if validation supports its interpretation and the intended use."

`gem_composite()` does not exist. A caller that wants one gets a raised
exception carrying the §10.3 reason, rather than a number.

**There are no band labels.** §10.5 — see `domains.BANDS_VALIDATED`.
