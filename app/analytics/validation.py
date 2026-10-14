"""
THE VALIDATION REPORT — spec §15 and §18. L1 only.

§15 lists what pilot testing must examine, and three of its bullets are
psychometric:

    - Whether the three GEM subdomains are sufficiently distinguishable.
    - Whether the questions within each subdomain measure a coherent construct.
    - Whether the proposed scoring methods are reliable and interpretable.

§18 singles one of them out:

    "Particular attention should be paid to the distinction between General Job
     Satisfaction and Emotional Experience. ... Although the two may be
     related, the survey should not simply measure the same overall sentiment
     using slightly different words."

────────────────────────────────────────────────────────────────────────────────
THIS IS THE ONE PLACE CORRELATION BELONGS
────────────────────────────────────────────────────────────────────────────────
app/analytics/association.py refuses to compute a coefficient, because §10.4
asks for comparisons and §10.5 asks for "scores, response distributions,
comparisons and limitations" in CLIENT reporting.

Here the question is different. "Are these two subdomains measuring the same
thing?" is a question about the INSTRUMENT, and a correlation is the direct
answer to it. The output never reaches a client: it is evidence for Technolysi
about whether the instrument works, not a finding about any organisation's
workforce.

That separation is why VIEW_VALIDATION_REPORT is L1-only. Handing a client
these figures invites the instrument's weaknesses to be read as their
organisation's.

────────────────────────────────────────────────────────────────────────────────
WHAT A NUMBER HERE MAY AND MAY NOT BE CALLED
────────────────────────────────────────────────────────────────────────────────
§10.5 forbids labelling a SCORE as low, moderate or high while bands are
unvalidated. It says nothing about reliability coefficients, which have an
established interpretive literature of their own — and reporting α = 0.84 with
no indication of whether that is adequate would make the report useless for the
one decision it exists to inform.

So alpha and the discriminant correlations carry conventional readings, labelled
as conventions rather than as facts about this instrument. A satisfaction score
never does.

Pure Python. numpy is available but not used: everything here is a handful of
sums, and keeping it dependency-free means the whole module is testable against
hand-computed fixtures.
"""
import math
import statistics
from dataclasses import dataclass, field

from app import domains

# Below this many complete responses a coefficient is not reported at all.
# α and r are both unstable at small n, and a validation report that published
# α = 0.91 from eleven people would license exactly the claim §18's product
# note withholds — that the instrument is validated.
VALIDATION_MIN_N = 30

# Conventional readings for Cronbach's alpha. LABELLED AS CONVENTIONS, because
# they are: they come from the measurement literature, not from this
# instrument's own pilot evidence.
ALPHA_CONVENTIONS = [
    (0.90, 'at or above the level usually described as excellent, and high '
           'enough to raise the opposite question of whether some items are '
           'near-duplicates'),
    (0.80, 'within the range usually described as good'),
    (0.70, 'at or above the level conventionally treated as acceptable'),
    (0.60, 'below the conventional threshold of 0.70 and usually described as '
           'questionable'),
    (0.00, 'well below the conventional threshold of 0.70'),
]

# Above this, two subdomains are close enough that §18's warning applies: they
# may be measuring the same sentiment in different words.
DISCRIMINANT_CONCERN = 0.85


@dataclass(frozen=True)
class NotComputable:
    reason: str
    n: int = 0

    def __bool__(self):
        return False


@dataclass(frozen=True)
class Reliability:
    """Internal consistency for one subdomain."""
    measure: tuple
    alpha: float
    n: int
    items: int
    item_total: dict = field(default_factory=dict)

    @property
    def label(self):
        return domains.subdomain_label(*self.measure)

    @property
    def convention(self):
        for floor, text in ALPHA_CONVENTIONS:
            if self.alpha >= floor:
                return text
        return ALPHA_CONVENTIONS[-1][1]

    @property
    def weak_items(self):
        """
        Items that barely move with their own subdomain.

        A corrected item-total correlation below 0.30 is the usual flag: the
        item is not measuring what its three neighbours measure, and it is
        dragging the subdomain score toward something else.
        """
        return sorted([code for code, r in self.item_total.items()
                       if r is not None and r < 0.30])


@dataclass(frozen=True)
class Discriminant:
    """Correlation between two subdomains of the outcome domain."""
    left: tuple
    right: tuple
    r: float
    n: int

    @property
    def left_label(self):
        return domains.subdomain_label(*self.left)

    @property
    def right_label(self):
        return domains.subdomain_label(*self.right)

    @property
    def concerning(self):
        return self.r >= DISCRIMINANT_CONCERN

    def sentence(self):
        if self.concerning:
            return (
                f'{self.left_label} and {self.right_label} correlate at '
                f'{self.r:.2f} (n={self.n}). At this level the two may be '
                'measuring the same underlying sentiment in different words, '
                'which is the possibility §18 asks to be examined directly.')
        return (
            f'{self.left_label} and {self.right_label} correlate at '
            f'{self.r:.2f} (n={self.n}), which is consistent with two related '
            'but distinguishable constructs.')


@dataclass(frozen=True)
class ItemQuality:
    item_code: str
    measure: tuple
    na_rate: float
    responses: int

    @property
    def subdomain_label(self):
        return domains.subdomain_label(*self.measure)

    @property
    def high_na(self):
        """
        A high rate often means a CONFUSING item rather than an inapplicable
        one — which is §15's "whether respondents find any questions
        repetitive, confusing or difficult to answer", visible in the data.
        """
        return self.na_rate >= 25.0


@dataclass(frozen=True)
class StraightLining:
    """
    Respondents who gave the same answer to everything.

    Not proof of inattention — a genuinely uniform experience exists — but a
    rate that climbs across waves is a signal the instrument is too long or too
    repetitive, which §15 asks about directly.
    """
    sessions: int
    identical_whole: int
    identical_within_domain: int

    @property
    def whole_pct(self):
        return (self.identical_whole / self.sessions * 100) if self.sessions else None

    @property
    def domain_pct(self):
        return (self.identical_within_domain / self.sessions * 100) if self.sessions else None


@dataclass(frozen=True)
class ValidationReport:
    n: int
    reliability: list
    discriminants: list
    items: list
    straight_lining: object
    recommendation: dict


# ── Statistics ─────────────────────────────────────────────────────────────

def pearson(xs, ys):
    """
    Pearson's r. Returns None when it is undefined.

    Undefined, not zero, when either series has no variance: everyone giving
    the same answer means there is nothing to correlate, which is a different
    fact from "no relationship" and must not be rendered as 0.00.
    """
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    xs2 = [p[0] for p in pairs]
    ys2 = [p[1] for p in pairs]
    mx, my = statistics.fmean(xs2), statistics.fmean(ys2)
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    sxx = sum((x - mx) ** 2 for x in xs2)
    syy = sum((y - my) ** 2 for y in ys2)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def cronbach_alpha(rows):
    """
    Cronbach's alpha over `rows`, one list of item scores per respondent.

    LISTWISE: only respondents who answered every item in the subdomain are
    used, and the n is reported alongside. Pairwise deletion would compute each
    variance from a different subset, and the resulting alpha describes no
    actual group of people.

    Returns None when it is undefined — fewer than two items, no variance in
    the totals, or too few complete rows.
    """
    rows = [r for r in rows if all(v is not None for v in r)]
    k = len(rows[0]) if rows else 0
    if k < 2 or len(rows) < 3:
        return None

    item_variances = []
    for index in range(k):
        column = [row[index] for row in rows]
        item_variances.append(statistics.pvariance(column))

    totals = [sum(row) for row in rows]
    total_variance = statistics.pvariance(totals)
    if total_variance == 0:
        return None

    return (k / (k - 1)) * (1 - sum(item_variances) / total_variance)


def corrected_item_total(rows, index):
    """
    Correlation of one item with the SUM OF THE OTHERS.

    Corrected — the item is excluded from the total it is compared against.
    Correlating an item with a total that contains it guarantees a healthy
    figure and tells you nothing, which is the classic way this check is got
    wrong.
    """
    rows = [r for r in rows if all(v is not None for v in r)]
    if len(rows) < 3 or len(rows[0]) < 2:
        return None
    item = [row[index] for row in rows]
    rest = [sum(v for i, v in enumerate(row) if i != index) for row in rows]
    return pearson(item, rest)


# ── Assembly ───────────────────────────────────────────────────────────────

def _answers_by_cell(profiles):
    """{(domain, sub): {item_code: {session_id: value}}} over scored answers."""
    cells = {}
    for profile in profiles:
        for answer in profile.answers:
            if answer.not_applicable or answer.score is None:
                continue
            value = (6 - answer.score) if answer.is_reverse_scored else float(answer.score)
            cells.setdefault(answer.cell, {}).setdefault(
                answer.item_code, {})[profile.session_id] = value
    return cells


def _matrix(by_item, session_ids):
    """Rows of item scores, one row per session, in a stable item order."""
    codes = sorted(by_item)
    return codes, [[by_item[code].get(sid) for code in codes]
                   for sid in session_ids]


def subdomain_reliability(profiles, min_n=VALIDATION_MIN_N):
    """Cronbach's alpha and corrected item-total correlations, per subdomain."""
    cells = _answers_by_cell(profiles)
    session_ids = [p.session_id for p in profiles]
    out = []

    for measure in domains.subdomain_pairs():
        by_item = cells.get(measure)
        if not by_item:
            out.append((measure, NotComputable('no responses for this subdomain')))
            continue

        codes, rows = _matrix(by_item, session_ids)
        complete = [r for r in rows if all(v is not None for v in r)]
        if len(complete) < min_n:
            out.append((measure, NotComputable(
                f'{len(complete)} respondents answered all {len(codes)} items; '
                f'{min_n} are needed before a reliability coefficient means '
                'anything.', n=len(complete))))
            continue

        alpha = cronbach_alpha(complete)
        if alpha is None:
            out.append((measure, NotComputable(
                'every respondent gave the same total, so there is no '
                'variance to analyse.', n=len(complete))))
            continue

        item_total = {code: corrected_item_total(complete, index)
                      for index, code in enumerate(codes)}
        out.append((measure, Reliability(
            measure=measure, alpha=alpha, n=len(complete),
            items=len(codes), item_total=item_total)))
    return out


def gem_discriminants(profiles, min_n=VALIDATION_MIN_N):
    """
    §18's check: are the three Overall Satisfaction subdomains distinguishable?

    Correlated on the SUBDOMAIN SCORES each respondent produced, which is the
    level at which the question is asked — not on individual items.
    """
    pairs = [(domains.GEM, s) for s in domains.SUBDOMAINS[domains.GEM]]
    out = []
    for i, left in enumerate(pairs):
        for right in pairs[i + 1:]:
            xs, ys = [], []
            for profile in profiles:
                a = profile.subdomain_scores.get(left)
                b = profile.subdomain_scores.get(right)
                if isinstance(a, float) and isinstance(b, float):
                    xs.append(a)
                    ys.append(b)
            if len(xs) < min_n:
                out.append(NotComputable(
                    f'{domains.subdomain_label(*left)} vs '
                    f'{domains.subdomain_label(*right)}: {len(xs)} respondents '
                    f'scored both; {min_n} are needed.', n=len(xs)))
                continue
            r = pearson(xs, ys)
            if r is None:
                out.append(NotComputable(
                    f'{domains.subdomain_label(*left)} vs '
                    f'{domains.subdomain_label(*right)}: no variance to '
                    'correlate.', n=len(xs)))
                continue
            out.append(Discriminant(left=left, right=right, r=r, n=len(xs)))
    return out


def item_quality(profiles):
    """N/A rate per item — §15's "difficult to answer", visible in the data."""
    totals, nas, measures = {}, {}, {}
    for profile in profiles:
        for answer in profile.answers:
            totals[answer.item_code] = totals.get(answer.item_code, 0) + 1
            measures[answer.item_code] = answer.cell
            if answer.not_applicable or answer.score is None:
                nas[answer.item_code] = nas.get(answer.item_code, 0) + 1

    return sorted(
        (ItemQuality(item_code=code, measure=measures[code],
                     na_rate=(nas.get(code, 0) / totals[code] * 100),
                     responses=totals[code])
         for code in totals),
        key=lambda i: i.na_rate, reverse=True)


def straight_lining(profiles):
    """How many respondents gave one answer to everything."""
    whole = within = 0
    for profile in profiles:
        scored = [a for a in profile.answers
                  if not a.not_applicable and a.score is not None]
        if len(scored) < 2:
            continue
        if len({a.score for a in scored}) == 1:
            whole += 1
            within += 1
            continue
        by_domain = {}
        for answer in scored:
            by_domain.setdefault(answer.domain_code, set()).add(answer.score)
        if any(len(v) == 1 for v in by_domain.values() if v):
            within += 1
    return StraightLining(sessions=len(profiles), identical_whole=whole,
                          identical_within_domain=within)


def recommendation(reliability, discriminants, n, min_n=VALIDATION_MIN_N):
    """
    The report's conclusion: may bands or a GEM composite be turned on yet?

    DEFAULTS TO NO, and says what would have to be true. §18's product note is
    the standard: the framework, items, scoring approach and interpretation
    rules "should be piloted and validated before the system is marketed as a
    psychometrically validated assessment". An absence of evidence is not
    evidence, and this function will not treat it as such.
    """
    blockers = []

    if n < min_n:
        blockers.append(
            f'Only {n} complete responses are available; at least {min_n} are '
            'needed before any coefficient here is stable.')

    computed = [r for _, r in reliability if isinstance(r, Reliability)]
    if not computed:
        blockers.append('No subdomain has enough complete responses to assess '
                        'internal consistency.')
    else:
        weak = [r for r in computed if r.alpha < 0.70]
        if weak:
            blockers.append(
                'Internal consistency is below the conventional 0.70 threshold '
                'for: ' + ', '.join(r.label for r in weak) + '.')
        flagged = [(r.label, code) for r in computed for code in r.weak_items]
        if flagged:
            blockers.append(
                'These items do not move with their own subdomain and should '
                'be reviewed: '
                + ', '.join(f'{code} ({label})' for label, code in flagged) + '.')

    usable = [d for d in discriminants if isinstance(d, Discriminant)]
    if not usable:
        blockers.append('The Overall Satisfaction subdomains could not be '
                        'compared against each other.')
    else:
        close = [d for d in usable if d.concerning]
        if close:
            blockers.append(
                'These subdomains may be measuring the same sentiment in '
                'different words, which §18 singles out: '
                + '; '.join(f'{d.left_label} and {d.right_label} (r={d.r:.2f})'
                            for d in close) + '.')

    return {
        'bands_may_be_enabled': False,
        'composite_may_be_enabled': False,
        'blockers': blockers,
        'statement': (
            'Score bands and an Overall Satisfaction composite remain '
            'disabled. This report does not recommend enabling either, and it '
            'will not do so on the strength of coefficients alone: §15 also '
            'asks whether employees understand the questions consistently, '
            'whether any are repetitive or hard to answer, and whether the '
            'instrument works across different roles and settings. Those are '
            'answered by a structured pilot with respondent feedback, not by '
            'this page.'
            if blockers or True else ''),
    }


def build(profiles, min_n=VALIDATION_MIN_N):
    """The whole report. `profiles` come from scoring.load_profiles()."""
    reliability = subdomain_reliability(profiles, min_n)
    discriminants = gem_discriminants(profiles, min_n)
    return ValidationReport(
        n=len(profiles),
        reliability=reliability,
        discriminants=discriminants,
        items=item_quality(profiles),
        straight_lining=straight_lining(profiles),
        recommendation=recommendation(reliability, discriminants,
                                      len(profiles), min_n),
    )
