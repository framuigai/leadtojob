"""
THE SCORING ENGINE. This module is the product.

Pure Python. No Flask import, no app context, no module-level import of
app.models — the DB loaders at the bottom take a SQLAlchemy session as an
argument and import the models lazily. That discipline is what lets the N/A
weighting and the suppression arithmetic be pinned by hand-computed fixtures
rather than by rendering a page and reading numbers back out of it.

────────────────────────────────────────────────────────────────────────────────
THE FOUR RULES THAT ARE NOT PREFERENCES
────────────────────────────────────────────────────────────────────────────────

1. N/A IS EXCLUDED FROM BOTH NUMERATOR AND DENOMINATOR, AT EVERY LEVEL.
   Spec §9.1: "Such responses should not be treated as neutral scores." Never
   encode it as 0, 3 or 6. A sentinel reaches an AVG() eventually and corrupts
   a result silently, weeks before anyone notices the number is wrong.

2. A GROUP SCORE IS THE MEAN OF PER-RESPONDENT SCORES, NOT OF RAW ANSWERS.
   Otherwise a respondent who answered all 12 items of a domain outweighs one
   who answered 6 and used N/A for the rest — silently, plausibly, and in the
   direction that systematically under-represents whoever had least basis to
   answer. On the Reward domain, where N/A is most common, those are precisely
   the people whose view is most at issue.

3. THE DISTRIBUTION IS THE PRIMARY DISPLAY, NOT THE MEAN.
   Spec §10.5. A 3.0 made of all-3s and a 3.0 made of half-1s and half-5s are
   different organisations needing different responses, and the mean cannot
   tell them apart.

4. THERE IS NO GEM COMPOSITE.
   Spec §10.3. gem_composite() exists only to raise, with the reason in the
   message — see its docstring.

────────────────────────────────────────────────────────────────────────────────
TEMPORAL DIFFERENCING — A KNOWN GAP, CLOSED IN PHASE 8
────────────────────────────────────────────────────────────────────────────────
Suppression here defends against differencing WITHIN one result set: when one
cell of a breakdown is hidden, enough siblings are hidden with it that the
total cannot give it away by subtraction.

It cannot defend against differencing ACROSS TIME. Two reports of the same
group computed at n=5 and n=6 give up respondent 6's entire profile by
subtraction, and no amount of in-report suppression prevents that.

The mitigation is a policy, not arithmetic: reports are generated from a CLOSED
cycle, and every result carries the `n` it was computed at so a report can state
it. `GroupResult.n` exists for that reason. Phase 8 enforces the closed-cycle
rule at the point of report generation.
"""
import math
import statistics
from dataclasses import dataclass, field

from app import domains

# ── The constants spec §10.1-§10.2 require to be documented ────────────────
#
# Mirrored in docs/scoring_rule.md and printed in every report's methodology
# section. §10.2 does not mandate a method; it mandates documenting WHICH.

MEAN_OF_SUBDOMAINS = 'mean_of_subdomains'
MEAN_OF_ITEMS = 'mean_of_items'
SCORING_METHODS = (MEAN_OF_SUBDOMAINS, MEAN_OF_ITEMS)

DOMAIN_SCORING_METHOD = MEAN_OF_SUBDOMAINS

# A subdomain has four items. Below two answered, the cell is Insufficient for
# that respondent and excluded upward rather than computed from a single item.
# The spec asks only that missing responses be "handled according to a
# documented scoring rule" (§10.1); this is that handling.
SUBDOMAIN_MIN_ITEMS = 2

# Favourable / unfavourable splits for the distribution (§10.5). 4-5 and 1-2;
# 3 is neither, and is reported as its own share rather than folded either way
# — "Neither agree nor disagree" is an answer, not a near-miss.
FAVOURABLE_SCORES = (4, 5)
UNFAVOURABLE_SCORES = (1, 2)
NEUTRAL_SCORE = 3


class ScoringError(Exception):
    pass


# ── Result types ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Insufficient:
    """
    Not computable for this respondent, with the reason.

    A distinct type rather than None, because None invites `or 0` somewhere
    downstream and a zero on a 1-5 scale is not "missing", it is worse than
    the worst possible answer.
    """
    reason: str

    def __bool__(self):
        # Falsy, so `if score:` does not silently treat it as a real value.
        return False


@dataclass(frozen=True)
class Suppressed:
    """
    A group too small to report (§9.2, §13.5-§13.6).

    Carries both numbers so a renderer can say "Suppressed (n < 5)" rather than
    printing a blank — a blank is read as zero, which on a satisfaction scale
    is a catastrophic finding rather than an absent one.

    `complementary` marks a cell suppressed not because it was itself too small
    but because hiding it alone would have let a reader recover a smaller
    sibling by subtraction.
    """
    n: int
    threshold: int
    complementary: bool = False

    def __bool__(self):
        return False

    @property
    def label(self):
        return f'Suppressed (n < {self.threshold})'


@dataclass(frozen=True)
class Distribution:
    """
    The full 1-5 frequency count, plus the favourable/neutral/unfavourable
    split. THE PRIMARY DISPLAY (§10.5), not a decoration beneath the mean.

    `counts` is over ANSWERED responses only; `not_applicable` is counted
    separately and never folded in.
    """
    counts: dict
    not_applicable: int
    total_responses: int

    @property
    def answered(self):
        return sum(self.counts.values())

    def _pct(self, scores):
        if not self.answered:
            return None
        return sum(self.counts.get(s, 0) for s in scores) / self.answered * 100

    @property
    def favourable_pct(self):
        return self._pct(FAVOURABLE_SCORES)

    @property
    def unfavourable_pct(self):
        return self._pct(UNFAVOURABLE_SCORES)

    @property
    def neutral_pct(self):
        return self._pct((NEUTRAL_SCORE,))

    @property
    def na_pct(self):
        """
        Coverage. A high rate is A FINDING, not noise: 30% N/A on a Peer
        Comparison item means people do not know what their peers earn, which
        is a fact about pay transparency worth naming in its own right.
        """
        if not self.total_responses:
            return None
        return self.not_applicable / self.total_responses * 100


@dataclass(frozen=True)
class Dispersion:
    """
    Spread within a group.

    HIGH DISPERSION IS A FINDING, NOT A DATA-QUALITY WARNING. A department
    split between people who are content and people who are not is a real
    organisational fact — and it is invisible in the mean, which is exactly
    why the mean is not the primary display.

    `sd` is the POPULATION standard deviation (ddof=0). We are describing the
    respondents we actually have, not inferring to a wider population from a
    sample, and population sd is defined at n=1 where the sample version is
    not.
    """
    sd: float
    iqr: float
    minimum: float
    maximum: float


@dataclass(frozen=True)
class GroupResult:
    """
    One group, one measure.

    `n` IS PART OF THE RESULT, not metadata. Every report footer carries it,
    because a number without its n is how a four-person finding becomes a
    board slide. It is also what lets a report state the n it was computed at,
    which is the temporal-differencing mitigation described in the module
    docstring.
    """
    n: int
    mean: float
    distribution: Distribution
    dispersion: Dispersion


@dataclass
class RespondentProfile:
    """
    One anonymous session's scores. No identity — see models.ResponseSession.

    This is the unit the group aggregates over, and the reason the product can
    answer §10.4 at all: the subdomain and domain scores belong to ONE person,
    so they can be correlated against each other across respondents without
    anyone knowing who that person is.
    """
    session_id: str
    segment_values: dict = field(default_factory=dict)
    subdomain_scores: dict = field(default_factory=dict)   # (domain, sub) -> float|Insufficient
    domain_scores: dict = field(default_factory=dict)      # domain -> float|Insufficient
    answers: list = field(default_factory=list)


@dataclass(frozen=True)
class Answer:
    """One response, flattened. `score` is None exactly when not_applicable."""
    domain_code: str
    subdomain_code: str
    item_code: str
    score: int = None
    not_applicable: bool = False
    is_reverse_scored: bool = False

    @property
    def cell(self):
        # ALWAYS the pair. Subdomain letters repeat across domains, so a bare
        # letter would join three different constructs together.
        return (self.domain_code, self.subdomain_code)


# ── Level 1: the item ───────────────────────────────────────────────────────

def item_value(answer):
    """
    The scored value of one answer, or None if it does not contribute.

    Reverse scoring is `6 - score`, honoured FROM DAY ONE even though none of
    the 48 supplied items is currently reverse-worded. Pilot validation (§15)
    is the likeliest reason to introduce one, and retrofitting a reverse flag
    through the ladder after real data exists silently flips a subdomain for
    every historical cycle.
    """
    if answer.not_applicable or answer.score is None:
        return None
    if not 1 <= answer.score <= 5:
        raise ScoringError(
            f'{answer.item_code}: score {answer.score!r} is outside 1-5. The '
            'database CHECK constraint should have refused this row; reaching '
            'here means it was bypassed.'
        )
    return (6 - answer.score) if answer.is_reverse_scored else float(answer.score)


# ── Level 2: the subdomain ──────────────────────────────────────────────────

def subdomain_score(answers):
    """
    Mean of one respondent's answered items in a (domain, subdomain) cell.

    Requires SUBDOMAIN_MIN_ITEMS answered. N/A rows are excluded from both the
    numerator and the count — they are not a low score and not a middling one.
    """
    values = [v for v in (item_value(a) for a in answers) if v is not None]
    if len(values) < SUBDOMAIN_MIN_ITEMS:
        return Insufficient(
            f'{len(values)} of {len(answers)} items answered; '
            f'{SUBDOMAIN_MIN_ITEMS} required'
        )
    return statistics.fmean(values)


# ── Level 3: the domain ─────────────────────────────────────────────────────

def domain_score(answers, method=DOMAIN_SCORING_METHOD, subdomain_scores=None):
    """
    One respondent's score for one domain, by the documented method.

    THE TWO METHODS DIVERGE WHEN COMPLETENESS VARIES — which is the entire
    reason §10.2 requires the choice to be documented:

        Autonomy  4/4 answered, all 5s  -> 5.0
        Purpose   4/4 answered, all 5s  -> 5.0
        Challenge 2/4 answered, both 1s -> 1.0

        mean_of_subdomains = (5.0 + 5.0 + 1.0) / 3      = 3.67
        mean_of_items      = (5x8 + 1x2) / 10           = 4.20

    Half a point apart on the same answers. mean_of_items lets the two
    well-covered subdomains outvote the third eight answers to two, diluting
    the thing this respondent is unhappiest about. mean_of_subdomains gives
    each construct an equal voice, matching how §4 defines a domain — three
    subdomains, not twelve interchangeable questions.

    Both are implemented because a client who has already documented the other
    rule in their own HR policy must be accommodated without a code fork. The
    choice is frozen per cycle (measurement_cycles.scoring_method).
    """
    if method not in SCORING_METHODS:
        raise ScoringError(
            f'unknown scoring method {method!r}; expected one of '
            f'{SCORING_METHODS}. §10.2 requires the method in force to be '
            'documented, so an unrecognised one is refused rather than '
            'defaulted.'
        )

    if method == MEAN_OF_ITEMS:
        values = [v for v in (item_value(a) for a in answers) if v is not None]
        if not values:
            return Insufficient('no items answered in this domain')
        return statistics.fmean(values)

    if subdomain_scores is None:
        subdomain_scores = {}
        for sub in {a.subdomain_code for a in answers}:
            cell = [a for a in answers if a.subdomain_code == sub]
            subdomain_scores[sub] = subdomain_score(cell)

    usable = [s for s in subdomain_scores.values() if not isinstance(s, Insufficient)]
    if not usable:
        return Insufficient('no subdomain in this domain had enough answers')
    return statistics.fmean(usable)


def build_profile(session_id, answers, method=DOMAIN_SCORING_METHOD,
                  segment_values=None):
    """Turn one session's answers into its profile. Pure; no DB, no Flask."""
    profile = RespondentProfile(
        session_id=str(session_id),
        segment_values=dict(segment_values or {}),
        answers=list(answers),
    )
    for domain_code in domains.DOMAINS:
        in_domain = [a for a in answers if a.domain_code == domain_code]
        if not in_domain:
            continue
        per_sub = {}
        for sub in domains.SUBDOMAINS[domain_code]:
            cell = [a for a in in_domain if a.subdomain_code == sub]
            if cell:
                score = subdomain_score(cell)
                per_sub[sub] = score
                profile.subdomain_scores[(domain_code, sub)] = score
        profile.domain_scores[domain_code] = domain_score(
            in_domain, method=method, subdomain_scores=per_sub)
    return profile


# ── Level 4: the group ──────────────────────────────────────────────────────

def group_result(profiles, measure, min_n, answer_filter=None):
    """
    Aggregate a group for one measure, or suppress it.

    `measure` is a domain code ('GEM') or a (domain, subdomain) pair.

    THE MEAN IS OF PER-RESPONDENT SCORES. See rule 2 in the module docstring:
    pooling raw answers instead would weight a complete respondent above one
    who used N/A, in the direction that silently under-represents whoever had
    least basis to answer.

    A respondent whose score for this measure is Insufficient is excluded from
    the mean AND from `n`. They are still counted in the distribution's
    not_applicable tally, because "people could not answer this" is itself a
    reportable fact.
    """
    if min_n < 1:
        raise ScoringError(
            f'min_n must be at least 1, got {min_n}. A threshold of 0 would '
            'report a group with no respondents at all.'
        )

    if isinstance(measure, tuple):
        scores = [p.subdomain_scores.get(measure) for p in profiles]
        wanted = (lambda a: a.cell == measure)
    else:
        scores = [p.domain_scores.get(measure) for p in profiles]
        wanted = (lambda a: a.domain_code == measure)

    usable = [s for s in scores
              if s is not None and not isinstance(s, Insufficient)]
    n = len(usable)

    if n < min_n:
        return Suppressed(n=n, threshold=min_n)

    relevant = [a for p in profiles for a in p.answers
                if wanted(a) and (answer_filter is None or answer_filter(a))]
    return GroupResult(
        n=n,
        mean=statistics.fmean(usable),
        distribution=distribution(relevant),
        dispersion=dispersion(usable),
    )


def distribution(answers):
    """The 1-5 frequency count over answered rows, plus the N/A tally."""
    counts = {s: 0 for s in range(1, 6)}
    na = 0
    for answer in answers:
        if answer.not_applicable or answer.score is None:
            na += 1
            continue
        value = item_value(answer)
        # Reverse-scored items are counted at their SCORED value, so a
        # distribution bar always reads in the same direction as the mean
        # beside it. Counting the raw keypress would put a reverse item's
        # agreement at the unfavourable end of its own chart.
        counts[int(value)] = counts.get(int(value), 0) + 1
    return Distribution(counts=counts, not_applicable=na,
                        total_responses=len(answers))


def dispersion(values):
    """
    Spread of a group's per-respondent scores.

    statistics.quantiles needs at least two points, so IQR is 0.0 at n=1 —
    a single respondent has no spread, which is true rather than missing.
    """
    values = list(values)
    if not values:
        raise ScoringError('dispersion of an empty group')
    if len(values) == 1:
        return Dispersion(sd=0.0, iqr=0.0, minimum=values[0], maximum=values[0])
    q1, _, q3 = statistics.quantiles(values, n=4, method='inclusive')
    return Dispersion(
        sd=statistics.pstdev(values),
        iqr=q3 - q1,
        minimum=min(values),
        maximum=max(values),
    )


def coverage(answers):
    """
    N/A count and percentage. Feeds the Coverage section of the HR report.

    Reported rather than hidden: a domain that a third of respondents could not
    answer is a finding about that domain, and a mean computed from the rest is
    not wrong but it is not the whole story either.
    """
    total = len(answers)
    na = sum(1 for a in answers if a.not_applicable or a.score is None)
    return {
        'total': total,
        'not_applicable': na,
        'answered': total - na,
        'na_pct': (na / total * 100) if total else None,
    }


# ── Suppression, including the differencing defence ─────────────────────────

def suppress_breakdown(cells, min_n):
    """
    Apply primary AND COMPLEMENTARY suppression to one breakdown.

    `cells` maps a group key to its GroupResult or Suppressed.

    PRIMARY: any cell with n < min_n is suppressed. Obvious, and not enough.

    COMPLEMENTARY: if exactly one cell is suppressed, a reader who also has the
    total can recover it by subtraction — the suppression achieved nothing. So
    the next-smallest cell is suppressed too.

    And that is still not enough. Two suppressed cells of n=1 each have a
    combined n of 2: subtracting the visible cells from the total then yields a
    two-person aggregate, which at min_n=5 is exactly what the threshold
    forbids. So cells are suppressed until BOTH conditions hold:

        at least two cells are suppressed, AND
        their combined n is at least min_n

    tests/test_suppression.py pins the arithmetic with worked fixtures,
    including the n=1 + n=1 case that a naive "suppress one more" rule gets
    wrong.

    Note what this does NOT defend against: differencing across TIME. See the
    module docstring.
    """
    if min_n <= 1:
        # Nothing is suppressed, so nothing can be recovered by subtraction and
        # complementary suppression has nothing to do. Returned explicitly
        # rather than falling through the loop below, which would otherwise
        # start hiding perfectly reportable cells to satisfy a combined-n rule
        # that cannot be met when the threshold is 1.
        return dict(cells)

    out = {}
    for key, cell in cells.items():
        n = cell.n
        out[key] = (Suppressed(n=n, threshold=min_n) if n < min_n else cell)

    def suppressed_keys():
        return [k for k, v in out.items() if isinstance(v, Suppressed)]

    def combined_n():
        return sum(out[k].n for k in suppressed_keys())

    while True:
        hidden = suppressed_keys()
        visible = [k for k in out if k not in hidden]
        if not visible:
            break
        if len(hidden) >= 2 and combined_n() >= min_n:
            break
        if not hidden:
            break
        # Hide the smallest still-visible cell. Smallest first, because hiding
        # a large group to shield a small one costs the report far more
        # information than it needs to.
        nxt = min(visible, key=lambda k: out[k].n)
        out[nxt] = Suppressed(n=out[nxt].n, threshold=min_n, complementary=True)

    return out


# ── The composite that does not exist ───────────────────────────────────────

def gem_composite(*_args, **_kwargs):
    """
    ALWAYS RAISES. There is no overall satisfaction score.

    Spec §10.3: the three Overall Satisfaction subdomains "should initially be
    reported separately. An overall GEM composite should only be introduced if
    validation supports its interpretation and the intended use."

    This function exists precisely so that a caller who wants one gets an
    explanation rather than a number. Returning a mean of the three would be
    one line, would look entirely reasonable in review, and would be the single
    most likely way this requirement gets lost — it is the thing clients ask
    for most.
    """
    raise ScoringError(
        'There is no overall satisfaction composite. Specification §10.3 '
        'requires the three Overall Satisfaction subdomains - General Job '
        'Satisfaction, Emotional Experience, and Match with Overall '
        'Expectations - to be reported SEPARATELY, because they are related '
        'but not interchangeable: an employee may consider the job a good fit '
        'and still experience frequent frustration. A composite may only be '
        'introduced once pilot validation supports its interpretation (§15).'
    )


def band(*_args, **_kwargs):
    """
    ALWAYS RAISES. This instrument has no score bands.

    Spec §10.5: "Universal score bands should not be treated as validated
    merely because the scale ranges from 1 to 5." Same reasoning as
    gem_composite: a `if score > 4: return 'Strength'` is one line and looks
    harmless, and both sibling products have exactly that function.
    """
    raise ScoringError(
        'Score bands are not available for this instrument. Specification '
        '§10.5 requires labels such as low, moderate or high to be developed '
        'through pilot evidence and psychometric evaluation first. Report the '
        'score, the distribution and the comparison instead; colour encodes '
        'the response anchors, never a judgement of the score.'
    )


# ── DB loaders. Session-injected; no Flask, no app context. ────────────────

def load_answers(db_session, session_ids):
    """
    Flatten stored responses into Answer tuples, grouped by response session.

    `db_session` is passed in rather than imported, and the models are imported
    lazily inside the function, so the pure functions above stay importable
    without an application context.
    """
    from app.models import SurveyItem, SurveyResponse      # noqa: PLC0415

    rows = (db_session.query(SurveyResponse, SurveyItem)
            .join(SurveyItem, SurveyResponse.item_id == SurveyItem.id)
            .filter(SurveyResponse.session_id.in_(list(session_ids)))
            .all())

    grouped = {}
    for response, item in rows:
        grouped.setdefault(str(response.session_id), []).append(Answer(
            domain_code=item.domain_code,
            subdomain_code=item.subdomain_code,
            item_code=item.item_code,
            score=response.score,
            not_applicable=response.not_applicable,
            is_reverse_scored=item.is_reverse_scored,
        ))
    return grouped


def load_profiles(db_session, deployment_ids, method=DOMAIN_SCORING_METHOD):
    """
    Every SUBMITTED, non-void session for these deployments, as profiles.

    Unsubmitted drafts are excluded: a partial response is not a response, and
    counting one would move a reported figure every time somebody opened the
    form and wandered off. Voided sessions are excluded too — that is what
    voiding is for — and the exclusion is visible in the audit log rather than
    silently changing a count.
    """
    from app.models import ResponseSession                 # noqa: PLC0415

    sessions = (db_session.query(ResponseSession)
                .filter(ResponseSession.deployment_id.in_(list(deployment_ids)))
                .filter(ResponseSession.submitted_at.isnot(None))
                .filter(ResponseSession.is_void.is_(False))
                .all())
    if not sessions:
        return []

    answers_by_session = load_answers(db_session, [s.id for s in sessions])
    return [
        build_profile(
            session_id=s.id,
            answers=answers_by_session.get(str(s.id), []),
            method=method,
            segment_values=s.segment_values or {},
        )
        for s in sessions
    ]


def segment_breakdown(profiles, measure, segment_key, min_n):
    """
    One segment at a time, with complementary suppression applied.

    ONE SEGMENT, NEVER A CROSS-TAB. Department x tenure band collapses group
    sizes fast — twelve departments by four bands over 200 staff averages four
    people a cell — and a cross-tab is the single easiest way to re-identify
    someone in an otherwise safe dataset. A cross-tab is an L1-gated,
    explicitly-logged action or it is a later phase.

    Respondents who declined to declare this segment are reported as their own
    group under the key None, rather than being folded into another group or
    dropped. Folding them in would attribute their answers to a department
    they did not claim; dropping them would quietly shrink the total.
    """
    groups = {}
    for profile in profiles:
        groups.setdefault(profile.segment_values.get(segment_key), []).append(profile)

    cells = {}
    for key, members in groups.items():
        result = group_result(members, measure, min_n=1)
        cells[key] = result
    return suppress_breakdown(cells, min_n)
