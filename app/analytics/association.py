"""
THE DIAGNOSTIC LAYER — spec §10.4, answered in the spec's own terms.

§10.4 is the only section that specifies how associations are handled, and it
specifies QUESTIONS, not a method:

    TID, BIT and PIE should be examined in relation to GEM.

    The analysis should explore questions such as:
      - Do employees reporting lower overall satisfaction also report lower
        autonomy or purpose?
      - Are relationship experiences associated with differences in overall
        satisfaction?
      - Does financial compensation satisfaction vary alongside overall
        satisfaction?
      - Do patterns differ across teams, roles, locations or other appropriate
        groups?

    Such patterns indicate associations and areas for investigation. They do
    not, by themselves, establish that one factor caused another.

Three of those four bullets are the same comparison applied to each diagnostic
domain: contrast the employees reporting LOWER overall satisfaction against
those reporting HIGHER. The fourth is a comparison between organisational
groups. So the engine computes exactly two things, and they map one-to-one onto
the specification's own list.

────────────────────────────────────────────────────────────────────────────────
WHY THERE IS NO CORRELATION COEFFICIENT HERE
────────────────────────────────────────────────────────────────────────────────
The word "correlation" does not appear in the specification. Neither does
Pearson, coefficient, regression, confidence interval or significance. §10.5 is
explicit about what to show at this stage instead:

    "During early implementation, it is preferable to show the scores,
     response distributions, comparisons and limitations clearly."

Comparisons. And a comparison answers §10.4's questions AS ASKED — "employees
in the lowest third on overall satisfaction score 2.1 on autonomy; those in the
highest third score 4.3" is the bullet point, in words, with its own n on the
face of it.

A coefficient would also be the easier thing to misuse. `r = 0.72` survives
into a board paper stripped of its caveat and its sample size; the sentence
above does not, because it carries both. On an instrument that is not yet
validated (§18's product note), publishing coefficients invites readers to
treat artefacts of unvalidated items as findings about their workforce.

Correlation DOES belong in one place — the internal Validation Report, where
§15 asks "whether the three GEM subdomains are sufficiently distinguishable"
and "whether the questions within each subdomain measure a coherent construct".
That is evidence about the INSTRUMENT, computed on pooled pilot data, L1-only,
and it is phase 10. It is not a client-facing finding.

────────────────────────────────────────────────────────────────────────────────
AND THERE IS STILL NO GEM COMPOSITE
────────────────────────────────────────────────────────────────────────────────
§10.4 says "employees reporting lower overall satisfaction", and §10.3 forbids
combining the three Overall Satisfaction subdomains into one number. Both hold:
respondents are ranked by ONE NAMED outcome subdomain at a time, and every
output states which. Rank by General Job Satisfaction and you learn whether
autonomy tracks people's considered judgement of the job; rank by Emotional
Experience and you learn whether it tracks how work feels. §5 says those are
different questions, and averaging them away would be the composite by the back
door.
"""
from dataclasses import dataclass, field

from app import domains
from app.analytics import scoring as sc

# ── Floors ─────────────────────────────────────────────────────────────────
#
# A contrast splits a group into thirds, so each third is itself a reported
# group and is subject to the reporting threshold like any other. That gives
# two floors, and the binding one is whichever is larger.

# Below this, thirds of the group are too small to be a comparison at all —
# a contrast between three people and three people is an anecdote with a
# decimal point. Independent of the privacy threshold.
ASSOCIATION_MIN_N = 12


def contrast_floor(min_reporting_n):
    """
    The smallest group that can produce a contrast.

    max(ASSOCIATION_MIN_N, 3 x min_reporting_n), because each tertile is a
    reported group: at a threshold of 5 a group of 12 would publish two
    four-person figures, which is precisely what the threshold forbids. The
    tertiles are where that is easiest to overlook, since the GROUP passes.
    """
    return max(ASSOCIATION_MIN_N, 3 * max(1, min_reporting_n))


# The three outcome subdomains a contrast may be ranked by. Never averaged —
# see the module docstring.
OUTCOME_SUBDOMAINS = [(domains.GEM, s) for s in domains.SUBDOMAINS[domains.GEM]]

# The default ranking variable: the employee's considered evaluation of the job
# as a whole (§5.2), which is the closest single construct to §10.4's phrase
# "employees reporting lower overall satisfaction". A choice, not a composite,
# and every output names it.
DEFAULT_OUTCOME = (domains.GEM, 'G')

# Every diagnostic subdomain, in presentation order.
DIAGNOSTIC_SUBDOMAINS = [
    (d, s) for d in domains.DIAGNOSTIC_DOMAINS for s in domains.SUBDOMAINS[d]
]


# ── Results ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class NotComputed:
    """
    An association that could not be produced, with the reason in words.

    A distinct type rather than a None or a zero: "we did not have enough
    people to look at this" and "we looked and found nothing" are different
    findings, and a report that renders them identically is lying about one of
    them.
    """
    reason: str
    n: int = 0

    def __bool__(self):
        return False


@dataclass(frozen=True)
class Contrast:
    """
    One tertile contrast: §10.4's first three bullets, for one diagnostic
    measure against one named outcome subdomain.

    `caveat` is populated from domains.ASSOCIATION_CAVEAT at construction and
    is not optional. Carrying it ON THE RESULT rather than leaving it to the
    template means a new chart, export or screen cannot omit it by forgetting
    it exists — which is how a caveat gets lost, every time.
    """
    outcome: tuple
    diagnostic: tuple
    lower_mean: float
    upper_mean: float
    lower_n: int
    upper_n: int
    group_n: int
    outcome_lower_mean: float
    outcome_upper_mean: float
    caveat: str = field(default=domains.ASSOCIATION_CAVEAT)

    @property
    def difference(self):
        """
        Upper third minus lower third, on the diagnostic measure.

        POSITIVE means the more satisfied employees also report more of this.
        That is an association and nothing more — it does not say which way
        round anything runs, and the caveat beside it says so.
        """
        return self.upper_mean - self.lower_mean

    @property
    def size(self):
        """Magnitude, for ordering the priority view."""
        return abs(self.difference)

    @property
    def outcome_label(self):
        return domains.subdomain_label(*self.outcome)

    @property
    def diagnostic_label(self):
        if isinstance(self.diagnostic, tuple):
            return domains.subdomain_label(*self.diagnostic)
        return domains.DOMAIN_LABELS.get(self.diagnostic, self.diagnostic)

    def sentence(self):
        """
        §10.4's bullet, answered in words.

        This is the primary rendering. It carries its own sample sizes, which
        is most of why it is preferred to a coefficient: a reader seeing
        "lowest third, 4 people" calibrates instantly, where `r = 0.72` hides
        its n inside a decimal.
        """
        return (
            f'Employees in the lowest third on {self.outcome_label} '
            f'score {self.lower_mean:.1f} on {self.diagnostic_label}. '
            f'Those in the highest third score {self.upper_mean:.1f}. '
            f'(lowest third n={self.lower_n}, highest third n={self.upper_n})'
        )


@dataclass(frozen=True)
class PriorityEntry:
    """One row of the priority view."""
    diagnostic: tuple
    score: float
    contrast: Contrast
    quadrant: str
    quadrant_label: str

    @property
    def label(self):
        return domains.subdomain_label(*self.diagnostic)


@dataclass(frozen=True)
class PriorityView:
    """
    §10.4 turned into an order of investigation.

    Each diagnostic subdomain placed on its CURRENT SCORE and on the SIZE OF
    ITS CONTRAST with the named outcome. Lower score plus larger contrast =
    look here first.

    THE SPLIT IS RELATIVE, NOT ABSOLUTE. §10.5 forbids calling a score "low"
    while bands are unvalidated, so the axes are divided at THIS GROUP'S OWN
    MEDIAN — "below this group's median" is a statement about the data in front
    of the reader, which is defensible now; "low" is a claim about the scale,
    which is not.

    THE LABELS ARE INVESTIGATIVE, NEVER VERDICTS. "Investigate first", not
    "Critical". The output is a reading order, not a judgement.
    """
    outcome: tuple
    entries: list
    score_median: float
    contrast_median: float
    group_n: int
    caveat: str = field(default=domains.ASSOCIATION_CAVEAT)

    @property
    def outcome_label(self):
        return domains.subdomain_label(*self.outcome)

    def investigate_first(self):
        return [e for e in self.entries if e.quadrant == 'investigate_first']


@dataclass(frozen=True)
class SegmentComparison:
    """
    §10.4's fourth bullet: "Do patterns differ across teams, roles, locations
    or other appropriate groups?"

    DELIBERATELY NOT A LEAGUE TABLE. Cells are returned in the segment's own
    declared order, never sorted by score, and each carries its DIFFERENCE FROM
    THE WHOLE GROUP rather than a rank. A sorted list of departments by
    satisfaction is read as a performance ranking of their managers within
    about four seconds, and §13's closing line is explicit that this instrument
    is not for ranking.

    `spread` — the distance between the highest and lowest reportable cell — is
    the finding worth having: a wide spread means the organisation is not one
    place, which is a prompt to investigate rather than a scoreboard.
    """
    measure: object
    segment_key: str
    segment_label: str
    overall: object
    cells: list          # [(option_code, label, result, difference_or_None)]
    caveat: str = field(default=domains.ASSOCIATION_CAVEAT)

    @property
    def reportable(self):
        return [c for c in self.cells if isinstance(c[2], sc.GroupResult)]

    @property
    def spread(self):
        means = [c[2].mean for c in self.reportable]
        return (max(means) - min(means)) if len(means) >= 2 else None

    @property
    def suppressed_count(self):
        return sum(1 for c in self.cells if isinstance(c[2], sc.Suppressed))


# ── Tertile contrast ───────────────────────────────────────────────────────

def _measure_score(profile, measure):
    if isinstance(measure, tuple):
        return profile.subdomain_scores.get(measure)
    return profile.domain_scores.get(measure)


def _usable(profiles, measure):
    out = []
    for profile in profiles:
        score = _measure_score(profile, measure)
        if score is not None and not isinstance(score, sc.Insufficient):
            out.append((profile, score))
    return out


def tertile_contrast(profiles, diagnostic, outcome=DEFAULT_OUTCOME,
                     min_reporting_n=1):
    """
    Compare the lowest and highest thirds on `outcome`, measured on
    `diagnostic`.

    `diagnostic` may be a domain code ('TID') or a (domain, subdomain) pair.
    `outcome` must be one of the three Overall Satisfaction subdomains — never
    a composite of them (§10.3).

    Respondents are included only if they have BOTH an outcome score and a
    diagnostic score. Ranking people on an outcome they could not answer is
    not possible, and silently treating a missing outcome as low would invent
    the very pattern being looked for.
    """
    if outcome not in OUTCOME_SUBDOMAINS:
        raise sc.ScoringError(
            f'{outcome!r} is not an Overall Satisfaction subdomain. A contrast '
            'ranks respondents by one named outcome subdomain at a time; '
            'combining the three into a single ranking variable would be the '
            'composite §10.3 forbids, arrived at sideways.'
        )

    paired = []
    for profile, outcome_score in _usable(profiles, outcome):
        diagnostic_score = _measure_score(profile, diagnostic)
        if diagnostic_score is None or isinstance(diagnostic_score, sc.Insufficient):
            continue
        paired.append((outcome_score, diagnostic_score))

    floor = contrast_floor(min_reporting_n)
    n = len(paired)
    if n < floor:
        return NotComputed(
            reason=(
                f'{n} respondents answered both measures; {floor} are needed. '
                'A contrast splits the group into thirds, and each third is '
                'reported like any other group.'
            ),
            n=n,
        )

    paired.sort(key=lambda pair: pair[0])
    third = n // 3
    lower = paired[:third]
    upper = paired[-third:]

    outcome_lower = sum(p[0] for p in lower) / len(lower)
    outcome_upper = sum(p[0] for p in upper) / len(upper)

    if outcome_upper == outcome_lower:
        # Everybody gave the same outcome score, so there is no "more
        # satisfied" and "less satisfied" to compare. Reported as its own
        # finding rather than as a contrast of zero, which a reader would take
        # to mean "no association" when it means "no variation to associate
        # with".
        return NotComputed(
            reason=('every respondent reported the same level of '
                    f'{domains.subdomain_label(*outcome)}, so there is no '
                    'difference between the thirds to compare against.'),
            n=n,
        )

    return Contrast(
        outcome=outcome,
        diagnostic=diagnostic,
        lower_mean=sum(p[1] for p in lower) / len(lower),
        upper_mean=sum(p[1] for p in upper) / len(upper),
        lower_n=len(lower),
        upper_n=len(upper),
        group_n=n,
        outcome_lower_mean=outcome_lower,
        outcome_upper_mean=outcome_upper,
    )


def domain_contrasts(profiles, outcome=DEFAULT_OUTCOME, min_reporting_n=1):
    """
    §10.4's first three bullets: one contrast per diagnostic DOMAIN.

    Returns them in specification order — Task, Relationships, Reward — which
    is the order the bullets appear in and the order the report renders.
    """
    return [
        (domain, tertile_contrast(profiles, domain, outcome, min_reporting_n))
        for domain in domains.DIAGNOSTIC_DOMAINS
    ]


# ── Priority view ──────────────────────────────────────────────────────────

_QUADRANT_LABELS = {
    # Investigative language only. Never "Critical", "Failing", "At risk" —
    # §10.5 forbids the band these would imply, and the output is a reading
    # order rather than a verdict.
    'investigate_first': 'Investigate first',
    'monitor': 'Monitor',
    'maintain': 'Maintain',
    'lower_priority': 'Lower priority',
}


def priority_view(profiles, outcome=DEFAULT_OUTCOME, min_reporting_n=1):
    """
    Order the nine diagnostic subdomains by where to look first.

    Lower score + larger contrast with the outcome = investigate first.
    Both axes split at THIS GROUP'S median, never at an absolute threshold —
    see PriorityView's docstring.

    Returns NotComputed when no subdomain produced a contrast, rather than an
    empty list: "too few people" and "nothing stood out" are different
    findings.
    """
    rows = []
    for diagnostic in DIAGNOSTIC_SUBDOMAINS:
        contrast = tertile_contrast(profiles, diagnostic, outcome,
                                    min_reporting_n)
        if not isinstance(contrast, Contrast):
            continue
        group = sc.group_result(profiles, diagnostic, min_n=max(1, min_reporting_n))
        if not isinstance(group, sc.GroupResult):
            continue
        rows.append((diagnostic, group.mean, contrast))

    if not rows:
        return NotComputed(
            reason=('no diagnostic subdomain had enough respondents to compare '
                    'the lowest and highest thirds.'),
            n=len(profiles),
        )

    score_median = _median([r[1] for r in rows])
    contrast_median = _median([r[2].size for r in rows])

    entries = []
    for diagnostic, score, contrast in rows:
        below_median = score <= score_median

        # STRICTLY above the median, and never zero.
        #
        # `>=` looks equivalent and is not. When most subdomains are flat — the
        # ordinary case in a healthy organisation, and the case in any cycle
        # where one issue dominates — the median contrast is 0, and `size >= 0`
        # is true for every one of them. The result was all nine subdomains
        # flagged "Investigate first", including those with a contrast of
        # exactly 0.00. A priority list containing everything is not a priority
        # list, and it points an organisation at eight things that showed no
        # association at all.
        #
        # Found by running 18 real responses through the form, not by the unit
        # tests, which used populations varied enough that the median was never
        # degenerate.
        strong = contrast.size > 0 and contrast.size > contrast_median
        if below_median and strong:
            quadrant = 'investigate_first'
        elif below_median:
            quadrant = 'monitor'
        elif strong:
            quadrant = 'maintain'
        else:
            quadrant = 'lower_priority'
        entries.append(PriorityEntry(
            diagnostic=diagnostic, score=score, contrast=contrast,
            quadrant=quadrant, quadrant_label=_QUADRANT_LABELS[quadrant],
        ))

    # Ordered by contrast size, largest first — the explicit instruction, and
    # the thing the view is for.
    entries.sort(key=lambda e: e.contrast.size, reverse=True)

    return PriorityView(
        outcome=outcome, entries=entries, score_median=score_median,
        contrast_median=contrast_median, group_n=len(profiles),
    )


def _median(values):
    values = sorted(values)
    n = len(values)
    if not n:
        return None
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2


# ── Segment comparison ─────────────────────────────────────────────────────

def segment_comparison(profiles, measure, segment, min_reporting_n=1):
    """
    §10.4's fourth bullet. `segment` is a models.Segment.

    ONE SEGMENT AT A TIME. Crossing two of them — department by tenure band —
    collapses cell sizes fast and is the single easiest way to re-identify
    someone in an otherwise safe dataset. There is deliberately no function
    here that takes two segment keys.

    Cells come back in the segment's DECLARED order and carry their difference
    from the whole group. Not sorted by score: see SegmentComparison.
    """
    overall = sc.group_result(profiles, measure, min_n=max(1, min_reporting_n))
    breakdown = sc.segment_breakdown(profiles, measure, segment.segment_key,
                                     min_n=max(1, min_reporting_n))

    labels = {o.option_code: o.option_label
              for o in sorted(segment.options, key=lambda o: o.sort_order)}

    cells = []
    for code, label in labels.items():
        if code not in breakdown:
            continue
        result = breakdown[code]
        difference = (result.mean - overall.mean
                      if isinstance(result, sc.GroupResult)
                      and isinstance(overall, sc.GroupResult) else None)
        cells.append((code, label, result, difference))

    # Respondents who declined to declare are reported as their own group,
    # last. Folding them into another group would attribute their answers to a
    # department they did not claim; dropping them would quietly shrink the
    # total and change the organisation-wide figure.
    if None in breakdown:
        result = breakdown[None]
        difference = (result.mean - overall.mean
                      if isinstance(result, sc.GroupResult)
                      and isinstance(overall, sc.GroupResult) else None)
        cells.append((None, 'Not specified', result, difference))

    return SegmentComparison(
        measure=measure, segment_key=segment.segment_key,
        segment_label=segment.label, overall=overall, cells=cells,
    )


# ── The thing that is not built ────────────────────────────────────────────

def correlation(*_args, **_kwargs):
    """
    ALWAYS RAISES. There is no correlation coefficient in client reporting.

    Kept as a raising stub for the same reason gem_composite() is: computing
    one is a dozen lines of numpy, it looks like rigour, and it is the obvious
    thing for a later contributor to add. The reason it is absent is a product
    decision grounded in the specification, not an omission.
    """
    raise sc.ScoringError(
        'Correlation coefficients are not part of client reporting. The '
        'specification never asks for one: §10.4 specifies the QUESTIONS to '
        'explore and §10.5 asks for "scores, response distributions, '
        'comparisons and limitations" during early implementation. Use '
        'tertile_contrast(), which answers §10.4 in its own words and carries '
        'its sample sizes where a reader can see them. Correlation belongs to '
        'the internal Validation Report (§15), computed on pooled pilot data '
        'as evidence about the instrument.'
    )
