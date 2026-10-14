"""
PHASE 6'S ACCEPTANCE TEST — the scoring engine.

    unit tests with NO Flask context: a respondent using 12 N/A neither gains
    nor loses weight; a hand-computed fixture reproduces exactly;
    gem_composite() raises.

Not one of these takes the `app` or `db` fixture. That is the point: the
arithmetic is pinned by hand-computed numbers rather than by rendering a page
and reading figures back out of it. If any test in this file ever needs an
application context, the engine has acquired a dependency it should not have.
"""
import math

import pytest

from app import domains
from app.analytics import scoring as sc


# ── Builders ────────────────────────────────────────────────────────────────

def answer(domain, sub, ordinal, score=None, na=False, reverse=False):
    return sc.Answer(
        domain_code=domain, subdomain_code=sub,
        item_code=domains.item_code(domain, sub, ordinal),
        score=score, not_applicable=na, is_reverse_scored=reverse,
    )


def cell(domain, sub, scores):
    """Four items for one subdomain. `None` means N/A."""
    return [answer(domain, sub, i + 1,
                   score=s, na=(s is None))
            for i, s in enumerate(scores)]


def full_domain(domain, per_sub):
    out = []
    for sub, scores in zip(domains.SUBDOMAINS[domain], per_sub):
        out.extend(cell(domain, sub, scores))
    return out


# ── Level 1: the item ───────────────────────────────────────────────────────

def test_a_plain_answer_scores_itself():
    assert sc.item_value(answer('GEM', 'G', 1, score=4)) == 4.0


def test_an_na_answer_contributes_nothing():
    assert sc.item_value(answer('GEM', 'G', 1, na=True)) is None


def test_a_reverse_scored_item_is_six_minus_the_score():
    """
    Honoured from day one even though no supplied item is reverse-worded.
    Retrofitting this through the ladder after real data exists would silently
    flip a subdomain for every historical wave.
    """
    assert sc.item_value(answer('GEM', 'G', 1, score=5, reverse=True)) == 1.0
    assert sc.item_value(answer('GEM', 'G', 1, score=1, reverse=True)) == 5.0
    assert sc.item_value(answer('GEM', 'G', 1, score=3, reverse=True)) == 3.0


def test_a_score_outside_the_scale_raises_rather_than_being_clamped():
    """
    The database CHECK constraint should have refused it. Reaching here means
    it was bypassed, and a clamped 7 silently becomes a 5 — a real answer
    replaced by a plausible one.
    """
    with pytest.raises(sc.ScoringError, match='outside 1-5'):
        sc.item_value(answer('GEM', 'G', 1, score=7))


# ── Level 2: the subdomain ──────────────────────────────────────────────────

def test_a_subdomain_is_the_mean_of_its_answered_items():
    assert sc.subdomain_score(cell('GEM', 'G', [2, 3, 4, 5])) == 3.5


def test_na_is_excluded_from_the_denominator_not_counted_as_a_middle_answer():
    """
    THE RULE §9.1 STATES OUTRIGHT. Two 4s and two N/A is 4.0, not 3.5 (which
    is what counting N/A as a neutral 3 would give) and not 2.0 (which is what
    counting it as 0 would give).
    """
    assert sc.subdomain_score(cell('GEM', 'G', [4, 4, None, None])) == 4.0

    # The two wrong answers, stated explicitly so a future change that
    # reintroduces either one fails here rather than in a client report.
    assert sc.subdomain_score(cell('GEM', 'G', [4, 4, None, None])) != 3.5
    assert sc.subdomain_score(cell('GEM', 'G', [4, 4, None, None])) != 2.0


def test_a_subdomain_with_one_answered_item_is_insufficient():
    """
    SUBDOMAIN_MIN_ITEMS = 2. A cell resting on a single item is not a weak
    score, it is an unknown one, and reporting it as a number invites a reader
    to act on one keystroke.
    """
    result = sc.subdomain_score(cell('GEM', 'G', [5, None, None, None]))
    assert isinstance(result, sc.Insufficient)
    assert '1 of 4' in result.reason


def test_insufficient_is_falsy_but_is_not_none():
    """
    Falsy so `if score:` does not treat it as real. Not None, because None
    invites `or 0` downstream, and 0 on a 1-5 scale is worse than the worst
    possible answer rather than absent.
    """
    result = sc.subdomain_score(cell('GEM', 'G', [5, None, None, None]))
    assert not result
    assert result is not None
    assert result != 0


def test_two_answered_items_is_exactly_enough():
    assert sc.subdomain_score(cell('GEM', 'G', [5, 3, None, None])) == 4.0


# ── Level 3: the domain, and the two methods ────────────────────────────────

def test_mean_of_subdomains_is_the_mean_of_the_three_cells():
    answers = full_domain('TID', [[5, 5, 5, 5], [4, 4, 4, 4], [3, 3, 3, 3]])
    assert sc.domain_score(answers, method=sc.MEAN_OF_SUBDOMAINS) == 4.0


def test_the_two_methods_agree_when_everything_is_answered():
    answers = full_domain('TID', [[5, 5, 5, 5], [4, 4, 4, 4], [3, 3, 3, 3]])
    assert (sc.domain_score(answers, method=sc.MEAN_OF_SUBDOMAINS)
            == sc.domain_score(answers, method=sc.MEAN_OF_ITEMS) == 4.0)


def test_THE_METHODS_DISAGREE_WHEN_COMPLETENESS_VARIES():
    """
    THE FIXTURE THAT JUSTIFIES §10.2's DOCUMENTATION REQUIREMENT, and the one
    that must fail if anyone ever collapses the two methods into one.

        Autonomy  4/4 answered, all 5s  -> 5.0
        Purpose   4/4 answered, all 5s  -> 5.0
        Challenge 2/4 answered, both 1s -> 1.0

        mean_of_subdomains = (5 + 5 + 1) / 3   = 3.666...
        mean_of_items      = (5x8 + 1x2) / 10  = 4.2

    Half a point apart on identical answers. mean_of_items lets the two
    well-covered subdomains outvote the third eight answers to two, diluting
    exactly the thing this respondent is unhappiest about.
    """
    answers = full_domain('TID', [[5, 5, 5, 5], [5, 5, 5, 5], [1, 1, None, None]])

    by_subdomain = sc.domain_score(answers, method=sc.MEAN_OF_SUBDOMAINS)
    by_item = sc.domain_score(answers, method=sc.MEAN_OF_ITEMS)

    assert math.isclose(by_subdomain, 11 / 3)
    assert math.isclose(by_item, 4.2)
    assert by_subdomain != by_item
    assert by_item - by_subdomain > 0.5


def test_an_insufficient_subdomain_drops_out_of_mean_of_subdomains():
    """
    Two usable cells average to the domain score; the third does not become a
    zero and does not drag the domain down.
    """
    answers = full_domain('TID', [[4, 4, 4, 4], [2, 2, 2, 2], [5, None, None, None]])
    assert sc.domain_score(answers, method=sc.MEAN_OF_SUBDOMAINS) == 3.0


def test_a_domain_nobody_could_answer_is_insufficient_not_zero():
    answers = full_domain('PIE', [[None] * 4, [None] * 4, [None] * 4])
    for method in sc.SCORING_METHODS:
        result = sc.domain_score(answers, method=method)
        assert isinstance(result, sc.Insufficient)


def test_an_unknown_scoring_method_is_refused_not_defaulted():
    """
    §10.2 requires the method in force to be documented. Silently defaulting
    an unrecognised one produces a number whose rule nobody can state.
    """
    with pytest.raises(sc.ScoringError, match='unknown scoring method'):
        sc.domain_score(cell('GEM', 'G', [4, 4, 4, 4]), method='median')


# ── The profile ─────────────────────────────────────────────────────────────

def test_a_profile_carries_every_cell_and_domain_it_has_answers_for():
    answers = (full_domain('GEM', [[4] * 4, [4] * 4, [4] * 4])
               + full_domain('TID', [[2] * 4, [2] * 4, [2] * 4]))
    profile = sc.build_profile('s1', answers)

    assert profile.domain_scores['GEM'] == 4.0
    assert profile.domain_scores['TID'] == 2.0
    assert ('GEM', 'G') in profile.subdomain_scores
    assert ('TID', 'D') in profile.subdomain_scores
    # Domains with no answers are absent rather than present-and-zero.
    assert 'PIE' not in profile.domain_scores


def test_a_profile_holds_no_identity():
    """
    The session id is an opaque uuid and there is nowhere to put a name. If a
    field ever appears here that could identify someone, the §13 guarantee has
    been lost in the analytics layer rather than in the schema.
    """
    profile = sc.build_profile('s1', cell('GEM', 'G', [4, 4, 4, 4]),
                               segment_values={'department': 'FIN'})
    fields = set(vars(profile))
    assert fields == {'session_id', 'segment_values', 'subdomain_scores',
                      'domain_scores', 'answers'}
    for forbidden in ('email', 'name', 'ip', 'user_id', 'employee'):
        assert not any(forbidden in f for f in fields)


# ── Level 4: THE WEIGHTING RULE ─────────────────────────────────────────────

def test_a_respondent_using_twelve_na_neither_GAINS_NOR_LOSES_WEIGHT():
    """
    THE NAMED ACCEPTANCE CRITERION, and the most consequential test in the
    suite.

    Two respondents. Both score the Reward domain 2.0 on everything they
    answered. One answered all 12 items; the other answered 6 and marked 6
    N/A. The group mean must be exactly 2.0.

    Pooling raw answers instead gives the complete respondent twice the weight
    — silently, plausibly, and in the direction that under-represents whoever
    had least basis to answer. On the Reward domain, where N/A is most common
    because people genuinely do not know what their peers earn, those are
    precisely the people whose view is most at issue.
    """
    complete = sc.build_profile('complete', full_domain(
        'PIE', [[2, 2, 2, 2], [2, 2, 2, 2], [2, 2, 2, 2]]))
    sparse = sc.build_profile('sparse', full_domain(
        'PIE', [[2, 2, None, None], [2, 2, None, None], [2, 2, None, None]]))

    assert complete.domain_scores['PIE'] == 2.0
    assert sparse.domain_scores['PIE'] == 2.0

    result = sc.group_result([complete, sparse], 'PIE', min_n=1)
    assert result.mean == 2.0


def test_the_sparse_respondent_pulls_the_mean_exactly_as_far_as_the_complete_one():
    """
    The complement, which is what actually proves equal weight. If weighting
    were by answer count the mean would land at 2.75 (33 points over 12
    answers), not at the midpoint of the two respondents' scores.
    """
    complete = sc.build_profile('complete', full_domain(
        'PIE', [[3, 3, 3, 3], [3, 3, 3, 3], [3, 3, 3, 3]]))      # 3.0
    sparse = sc.build_profile('sparse', full_domain(
        'PIE', [[1, 1, None, None], [1, 1, None, None], [1, 1, None, None]]))  # 1.0

    result = sc.group_result([complete, sparse], 'PIE', min_n=1)
    assert result.mean == 2.0                       # the midpoint

    pooled = (3 * 12 + 1 * 6) / 18                  # what answer-weighting gives
    assert math.isclose(pooled, 2.3333333, abs_tol=1e-6)
    assert result.mean != pytest.approx(pooled)


def test_a_respondent_who_could_not_answer_at_all_is_excluded_from_n():
    """
    They are not a zero and not a middling respondent — they are absent from
    this measure. Their inability to answer is reported as coverage instead.
    """
    answered = sc.build_profile('a', full_domain(
        'PIE', [[4] * 4, [4] * 4, [4] * 4]))
    blank = sc.build_profile('b', full_domain(
        'PIE', [[None] * 4, [None] * 4, [None] * 4]))

    result = sc.group_result([answered, blank], 'PIE', min_n=1)
    assert result.n == 1
    assert result.mean == 4.0


def test_a_hand_computed_group_reproduces_exactly():
    """
    THE NAMED ACCEPTANCE CRITERION: a fixture worked out on paper.

    Three respondents on Relationships Satisfaction, mean_of_subdomains.

        R1  B [5,5,5,5]=5.0  I [4,4,4,4]=4.0  T [3,3,3,3]=3.0  -> 4.0
        R2  B [2,2,2,2]=2.0  I [2,2,2,2]=2.0  T [2,2,2,2]=2.0  -> 2.0
        R3  B [4,4,N,N]=4.0  I [5,5,N,N]=5.0  T [3,3,N,N]=3.0  -> 4.0

        group mean = (4.0 + 2.0 + 4.0) / 3 = 3.3333...
        population sd of [4,2,4]           = 0.9428...
        min 2.0, max 4.0
    """
    r1 = sc.build_profile('r1', full_domain(
        'BIT', [[5, 5, 5, 5], [4, 4, 4, 4], [3, 3, 3, 3]]))
    r2 = sc.build_profile('r2', full_domain(
        'BIT', [[2, 2, 2, 2], [2, 2, 2, 2], [2, 2, 2, 2]]))
    r3 = sc.build_profile('r3', full_domain(
        'BIT', [[4, 4, None, None], [5, 5, None, None], [3, 3, None, None]]))

    assert r1.domain_scores['BIT'] == 4.0
    assert r2.domain_scores['BIT'] == 2.0
    assert r3.domain_scores['BIT'] == 4.0

    result = sc.group_result([r1, r2, r3], 'BIT', min_n=1)
    assert result.n == 3
    assert math.isclose(result.mean, 10 / 3)
    assert math.isclose(result.dispersion.sd, math.sqrt(8 / 9))
    assert result.dispersion.minimum == 2.0
    assert result.dispersion.maximum == 4.0


def test_a_subdomain_measure_aggregates_the_same_way():
    r1 = sc.build_profile('r1', cell('BIT', 'T', [5, 5, 5, 5]))
    r2 = sc.build_profile('r2', cell('BIT', 'T', [1, 1, 1, 1]))
    result = sc.group_result([r1, r2], ('BIT', 'T'), min_n=1)
    assert result.n == 2
    assert result.mean == 3.0


# ── The distribution is the primary display ────────────────────────────────

def test_the_distribution_counts_every_answered_response():
    answers = cell('GEM', 'G', [1, 3, 5, 5])
    dist = sc.distribution(answers)
    assert dist.counts == {1: 1, 2: 0, 3: 1, 4: 0, 5: 2}
    assert dist.answered == 4
    assert dist.not_applicable == 0


def test_the_distribution_separates_na_from_the_counts():
    dist = sc.distribution(cell('GEM', 'G', [4, 4, None, None]))
    assert dist.counts == {1: 0, 2: 0, 3: 0, 4: 2, 5: 0}
    assert dist.not_applicable == 2
    assert dist.total_responses == 4
    assert dist.na_pct == 50.0


def test_favourable_and_unfavourable_are_percentages_of_ANSWERED_responses():
    """
    Not of total. Including N/A in the denominator would make a question people
    could not answer look like a question they answered unfavourably.
    """
    dist = sc.distribution(cell('GEM', 'G', [5, 5, 1, None]))
    assert dist.answered == 3
    assert math.isclose(dist.favourable_pct, 200 / 3)
    assert math.isclose(dist.unfavourable_pct, 100 / 3)
    assert dist.neutral_pct == 0.0


def test_neutral_is_reported_as_its_own_share_not_folded_either_way():
    """
    "Neither agree nor disagree" is an answer, not a near-miss in either
    direction, and the three shares must sum to 100.
    """
    dist = sc.distribution(cell('GEM', 'G', [3, 3, 3, 3]))
    assert dist.neutral_pct == 100.0
    assert dist.favourable_pct == 0.0
    assert dist.unfavourable_pct == 0.0


def test_the_three_shares_sum_to_one_hundred():
    dist = sc.distribution(cell('GEM', 'G', [1, 3, 4, 5]))
    total = dist.favourable_pct + dist.neutral_pct + dist.unfavourable_pct
    assert math.isclose(total, 100.0)


def test_a_reverse_item_is_counted_at_its_SCORED_value():
    """
    Otherwise a distribution bar reads in the opposite direction to the mean
    printed beside it — a reverse item's agreement would appear at the
    unfavourable end of its own chart.
    """
    reversed_answer = [answer('GEM', 'G', 1, score=5, reverse=True)]
    dist = sc.distribution(reversed_answer)
    assert dist.counts[1] == 1
    assert dist.counts[5] == 0


def test_two_identical_means_can_have_opposite_distributions():
    """
    §10.5's whole argument, as a test. A 3.0 of all-3s and a 3.0 of half-1s and
    half-5s are different organisations needing different responses, and the
    mean cannot tell them apart — which is why the distribution leads.
    """
    bland = sc.distribution(cell('GEM', 'G', [3, 3, 3, 3]))
    split = sc.distribution(cell('GEM', 'G', [1, 1, 5, 5]))

    mean_bland = sum(k * v for k, v in bland.counts.items()) / bland.answered
    mean_split = sum(k * v for k, v in split.counts.items()) / split.answered
    assert mean_bland == mean_split == 3.0

    assert bland.neutral_pct == 100.0
    assert split.neutral_pct == 0.0
    assert split.favourable_pct == 50.0
    assert split.unfavourable_pct == 50.0


# ── Dispersion ──────────────────────────────────────────────────────────────

def test_dispersion_of_a_split_group_is_large_and_is_a_finding():
    low = [sc.build_profile(f'l{i}', cell('BIT', 'T', [1, 1, 1, 1]))
           for i in range(3)]
    high = [sc.build_profile(f'h{i}', cell('BIT', 'T', [5, 5, 5, 5]))
            for i in range(3)]
    result = sc.group_result(low + high, ('BIT', 'T'), min_n=1)
    assert result.mean == 3.0
    assert result.dispersion.sd == 2.0
    assert result.dispersion.minimum == 1.0
    assert result.dispersion.maximum == 5.0


def test_a_single_respondent_has_no_spread_rather_than_an_error():
    """
    statistics.quantiles needs two points. A group of one genuinely has zero
    spread — true rather than missing — and at MIN_REPORTING_N=1 this path is
    reached constantly in local testing.
    """
    only = sc.build_profile('one', cell('BIT', 'T', [4, 4, 4, 4]))
    result = sc.group_result([only], ('BIT', 'T'), min_n=1)
    assert result.n == 1
    assert result.dispersion.sd == 0.0
    assert result.dispersion.iqr == 0.0


def test_dispersion_uses_the_population_sd_not_the_sample_sd():
    """
    We describe the respondents we have, not a sample inferring to a wider
    population — and population sd is defined at n=1 where the sample version
    divides by zero.
    """
    values = [2.0, 4.0]
    assert sc.dispersion(values).sd == 1.0          # population: sqrt(1) = 1.0
    assert sc.dispersion(values).sd != pytest.approx(math.sqrt(2))


def test_dispersion_of_an_empty_group_raises():
    with pytest.raises(sc.ScoringError, match='empty group'):
        sc.dispersion([])


# ── Coverage ────────────────────────────────────────────────────────────────

def test_coverage_reports_the_na_rate():
    """
    A high rate is a FINDING. 30% N/A on a Peer Comparison item means people do
    not know what their peers earn — a fact about pay transparency worth
    naming, not a gap to apologise for.
    """
    answers = cell('PIE', 'P', [4, 4, None, None]) + cell('PIE', 'I', [3, None, None, None])
    result = sc.coverage(answers)
    assert result['total'] == 8
    assert result['not_applicable'] == 5
    assert result['answered'] == 3
    assert math.isclose(result['na_pct'], 62.5)


def test_coverage_of_nothing_is_none_not_zero():
    """0% N/A and "no responses at all" are different facts."""
    assert sc.coverage([])['na_pct'] is None


# ── The things that must not exist ─────────────────────────────────────────

def test_gem_composite_raises_with_the_specs_reason():
    """
    THE NAMED ACCEPTANCE CRITERION.

    Returning a mean of the three would be one line, would look entirely
    reasonable in review, and is the single most likely way this requirement
    gets lost — it is the thing clients ask for most.
    """
    with pytest.raises(sc.ScoringError) as exc:
        sc.gem_composite({'GEM': 4.0})
    message = str(exc.value)
    assert '§10.3' in message
    assert 'SEPARATELY' in message
    assert 'not interchangeable' in message


def test_there_is_no_function_that_returns_a_composite_by_another_name():
    """
    A guard against the obvious workaround: adding `overall_score()` beside the
    raising `gem_composite()`.
    """
    suspicious = [name for name in dir(sc)
                  if any(w in name.lower()
                         for w in ('composite', 'overall', 'headline', 'index'))]
    assert suspicious == ['gem_composite'], suspicious


def test_band_raises_while_bands_are_not_validated():
    assert domains.BANDS_VALIDATED is False
    with pytest.raises(sc.ScoringError, match='not validated'):
        sc.band(4.6)


def test_the_engine_exposes_no_band_vocabulary():
    """
    Both sibling products have a four-band palette — Strength / Minor Gap /
    Moderate Gap / Critical Gap — and inheriting it here is the easiest
    mistake available. §10.5 forbids it until pilot evidence exists.

    Scans the AST for real string LITERALS rather than raw text, and skips
    docstrings: the module deliberately quotes `return 'Strength'` as the thing
    not to do, and a naive text scan flags its own warning as a violation.
    This is the same negation-awareness problem the report "no verdict" guard
    has, met earlier than expected.
    """
    import ast

    tree = ast.parse(open(sc.__file__, encoding='utf-8').read())

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, 'body', [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))

    forbidden = {'strength', 'minor gap', 'moderate gap', 'critical gap',
                 'low', 'moderate', 'high', 'critical'}
    offenders = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
        and node.value.strip().lower() in forbidden
    ]
    assert offenders == [], offenders


# ── Guards on the engine's own shape ───────────────────────────────────────

def test_the_engine_does_not_import_flask():
    """
    Pinned, not merely intended. The moment scoring.py needs an application
    context, every test above becomes an integration test and the hand-computed
    fixtures stop being runnable in isolation.
    """
    source = open(sc.__file__, encoding='utf-8').read()
    assert 'import flask' not in source.lower()
    assert 'from flask' not in source.lower()
    assert 'current_app' not in source


def test_a_zero_threshold_is_refused():
    only = sc.build_profile('one', cell('BIT', 'T', [4, 4, 4, 4]))
    with pytest.raises(sc.ScoringError, match='at least 1'):
        sc.group_result([only], ('BIT', 'T'), min_n=0)


def test_the_documented_default_method_is_mean_of_subdomains():
    """
    Changing this changes every reported figure, so it is pinned here as well
    as documented in docs/scoring_rule.md and printed in every report.
    """
    assert sc.DOMAIN_SCORING_METHOD == sc.MEAN_OF_SUBDOMAINS
    assert sc.SUBDOMAIN_MIN_ITEMS == 2
