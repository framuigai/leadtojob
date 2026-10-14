"""
PHASE 7'S ACCEPTANCE TEST — the diagnostic layer.

    an association below the floor is withheld rather than shown;
    the §10.4 caveat is present on every association output.

Plus the decision taken in this phase: NO CORRELATION COEFFICIENT. The
specification never asks for one, and §10.5 asks for comparisons instead.

Pure Python — no app context, no database, except where a Segment object is
needed.
"""
import math
from types import SimpleNamespace

import pytest

from app import domains
from app.analytics import association as assoc
from app.analytics import scoring as sc


# ── Builders ────────────────────────────────────────────────────────────────

def respondent(outcome_score, diagnostic_scores, label='r'):
    """
    One profile with a chosen General Job Satisfaction score and chosen
    diagnostic subdomain scores. `diagnostic_scores` maps (domain, sub) -> int.
    """
    answers = []
    for i in range(4):
        answers.append(sc.Answer(
            domain_code='GEM', subdomain_code='G',
            item_code=f'GEM-G-0{i + 1}', score=outcome_score))
    for (domain, sub), score in diagnostic_scores.items():
        for i in range(4):
            answers.append(sc.Answer(
                domain_code=domain, subdomain_code=sub,
                item_code=f'{domain}-{sub}-0{i + 1}', score=score))
    return sc.build_profile(label, answers)


def population(pairs, diagnostic=('TID', 'T')):
    """`pairs` is a list of (outcome_score, diagnostic_score)."""
    return [respondent(o, {diagnostic: d}, label=f'r{i}')
            for i, (o, d) in enumerate(pairs)]


# ── The floor ───────────────────────────────────────────────────────────────

def test_the_floor_is_the_larger_of_the_two_constraints():
    """
    Each tertile is a REPORTED GROUP and is subject to the reporting threshold
    like any other. At a threshold of 5, a group of 12 would publish two
    four-person figures — exactly what the threshold forbids, and easy to
    overlook because the GROUP of 12 passes.
    """
    assert assoc.contrast_floor(1) == 12       # the association floor binds
    assert assoc.contrast_floor(4) == 12
    assert assoc.contrast_floor(5) == 15       # 3 x 5 now binds
    assert assoc.contrast_floor(10) == 30


def test_a_group_below_the_floor_is_withheld_with_a_reason():
    """THE NAMED ACCEPTANCE CRITERION."""
    profiles = population([(2, 2)] * 5 + [(5, 5)] * 5)     # n=10 < 12
    result = assoc.tertile_contrast(profiles, ('TID', 'T'))

    assert isinstance(result, assoc.NotComputed)
    assert not result
    assert result.n == 10
    assert '12 are needed' in result.reason
    assert 'thirds' in result.reason


def test_a_group_at_the_floor_is_computed():
    profiles = population([(1, 1)] * 6 + [(5, 5)] * 6)      # n=12
    result = assoc.tertile_contrast(profiles, ('TID', 'T'))
    assert isinstance(result, assoc.Contrast)
    assert result.group_n == 12


def test_the_threshold_raises_the_floor():
    """A group of 12 is enough at threshold 1 and not enough at threshold 5."""
    profiles = population([(1, 1)] * 6 + [(5, 5)] * 6)
    assert isinstance(assoc.tertile_contrast(profiles, ('TID', 'T'),
                                             min_reporting_n=1), assoc.Contrast)
    assert isinstance(assoc.tertile_contrast(profiles, ('TID', 'T'),
                                             min_reporting_n=5), assoc.NotComputed)


# ── The contrast itself ─────────────────────────────────────────────────────

def test_a_clear_association_is_found_and_stated():
    """
    §10.4's first bullet, answered: the least satisfied third report less
    autonomy than the most satisfied third.
    """
    profiles = population(
        [(1, 1)] * 6 +      # unhappy, little autonomy
        [(3, 3)] * 6 +      # middle
        [(5, 5)] * 6        # happy, plenty of autonomy
    )
    result = assoc.tertile_contrast(profiles, ('TID', 'T'))

    assert result.lower_mean == 1.0
    assert result.upper_mean == 5.0
    assert result.difference == 4.0
    assert result.lower_n == result.upper_n == 6
    assert result.group_n == 18


def test_no_association_is_reported_as_no_difference_not_as_absence():
    """
    Autonomy identical across the satisfaction range. That is a FINDING — this
    is not where to look — and it must be reported as a contrast of zero rather
    than withheld.
    """
    profiles = population(
        [(1, 3)] * 6 + [(3, 3)] * 6 + [(5, 3)] * 6)
    result = assoc.tertile_contrast(profiles, ('TID', 'T'))

    assert isinstance(result, assoc.Contrast)
    assert result.difference == 0.0
    assert result.lower_mean == result.upper_mean == 3.0


def test_a_negative_association_keeps_its_sign():
    """
    The least satisfied people report MORE of this. Unusual, and exactly the
    sort of thing a diagnostic exists to surface — it means this is not the
    problem, and chasing it would waste the intervention.
    """
    profiles = population([(1, 5)] * 6 + [(3, 3)] * 6 + [(5, 1)] * 6)
    result = assoc.tertile_contrast(profiles, ('TID', 'T'))

    assert result.difference == -4.0
    assert result.size == 4.0        # magnitude, for ordering


def test_the_two_organisations_the_mean_cannot_tell_apart():
    """
    THE ARGUMENT FOR THE WHOLE SESSION DESIGN, as a test.

    Both organisations have identical distributions on both measures — the same
    multiset of scores, so identical means, identical response distributions.
    Only the PAIRING differs. A pooled design cannot see any difference between
    them; this one gives opposite answers, which is the point.
    """
    aligned = population([(1, 1)] * 6 + [(3, 3)] * 6 + [(5, 5)] * 6)
    inverted = population([(1, 5)] * 6 + [(3, 3)] * 6 + [(5, 1)] * 6)

    a = assoc.tertile_contrast(aligned, ('TID', 'T'))
    b = assoc.tertile_contrast(inverted, ('TID', 'T'))

    # Same underlying numbers...
    a_group = sc.group_result(aligned, ('TID', 'T'), min_n=1)
    b_group = sc.group_result(inverted, ('TID', 'T'), min_n=1)
    assert a_group.mean == b_group.mean
    assert a_group.distribution.counts == b_group.distribution.counts

    # ...opposite conclusions.
    assert a.difference == 4.0
    assert b.difference == -4.0


def test_when_nobody_differs_on_the_outcome_that_is_said_not_shown_as_zero():
    """
    Everyone gave the same Overall Satisfaction score. There is no "more
    satisfied" third to compare, so there is no contrast — and reporting 0.0
    would be read as "no association" when it means "no variation to associate
    with". Different findings, different words.
    """
    profiles = population([(4, 2)] * 9 + [(4, 5)] * 9)
    result = assoc.tertile_contrast(profiles, ('TID', 'T'))

    assert isinstance(result, assoc.NotComputed)
    assert 'same level' in result.reason


def test_a_respondent_missing_either_measure_is_excluded():
    """
    Ranking someone on an outcome they could not answer is not possible, and
    treating a missing outcome as a low one would invent the pattern being
    looked for.
    """
    good = population([(1, 1)] * 6 + [(5, 5)] * 6)
    blank = [sc.build_profile('blank', [
        sc.Answer(domain_code='TID', subdomain_code='T',
                  item_code=f'TID-T-0{i + 1}', score=5) for i in range(4)
    ])]
    result = assoc.tertile_contrast(good + blank, ('TID', 'T'))
    assert result.group_n == 12            # the blank respondent excluded


def test_a_contrast_works_at_domain_level_too():
    """§10.4's bullets are about domains; the priority view works on subdomains."""
    profiles = [
        respondent(o, {('TID', s): d for s in domains.SUBDOMAINS['TID']},
                   label=f'r{i}')
        for i, (o, d) in enumerate([(1, 1)] * 6 + [(3, 3)] * 6 + [(5, 5)] * 6)
    ]
    result = assoc.tertile_contrast(profiles, 'TID')
    assert isinstance(result, assoc.Contrast)
    assert result.difference == 4.0


# ── No composite, by any route ──────────────────────────────────────────────

def test_the_outcome_must_be_a_named_subdomain_never_a_composite():
    """
    §10.4 says "employees reporting lower overall satisfaction"; §10.3 forbids
    combining the three Overall Satisfaction subdomains into one number. Both
    hold: respondents are ranked by ONE named subdomain at a time.
    """
    profiles = population([(1, 1)] * 6 + [(5, 5)] * 6)

    with pytest.raises(sc.ScoringError, match='composite'):
        assoc.tertile_contrast(profiles, ('TID', 'T'), outcome='GEM')

    with pytest.raises(sc.ScoringError, match='composite'):
        assoc.tertile_contrast(profiles, ('TID', 'T'), outcome=('TID', 'T'))


def test_each_outcome_subdomain_can_be_ranked_by_separately():
    """
    §5: an employee may consider the job a good fit and still experience
    frequent frustration. Ranking by General Job Satisfaction and by Emotional
    Experience are different questions, and the answer may differ.
    """
    assert len(assoc.OUTCOME_SUBDOMAINS) == 3
    for outcome in assoc.OUTCOME_SUBDOMAINS:
        assert outcome[0] == 'GEM'


def test_every_contrast_names_the_outcome_it_ranked_by():
    """Otherwise the figure is uninterpretable — against what?"""
    profiles = population([(1, 1)] * 6 + [(5, 5)] * 6)
    result = assoc.tertile_contrast(profiles, ('TID', 'T'))
    assert result.outcome == assoc.DEFAULT_OUTCOME
    assert result.outcome_label == 'General Job Satisfaction'
    assert 'General Job Satisfaction' in result.sentence()


# ── THE CAVEAT IS ON THE RESULT, NOT THE TEMPLATE ──────────────────────────

def test_every_association_output_carries_the_10_4_caveat():
    """
    THE NAMED ACCEPTANCE CRITERION.

    Carried ON THE RESULT OBJECT rather than left to a template, so a new
    chart, export or screen cannot omit it by forgetting it exists. That is how
    a caveat gets lost — not by anyone deciding to drop it.
    """
    profiles = population([(1, 1)] * 6 + [(3, 3)] * 6 + [(5, 5)] * 6)

    contrast = assoc.tertile_contrast(profiles, ('TID', 'T'))
    view = assoc.priority_view(profiles)

    for output in (contrast, view):
        assert output.caveat == domains.ASSOCIATION_CAVEAT
        assert 'do not, by themselves, establish' in output.caveat


def test_the_caveat_is_the_specifications_own_sentence():
    assert domains.ASSOCIATION_CAVEAT == (
        'Such patterns indicate associations and areas for investigation. They '
        'do not, by themselves, establish that one factor caused another.'
    )


def test_every_result_type_that_can_be_rendered_has_a_caveat_field():
    """
    A guard against a NEW association result type being added without one.
    NotComputed is exempt: it reports an absence, and there is no association
    to caveat.
    """
    import dataclasses

    renderable = [assoc.Contrast, assoc.PriorityView, assoc.SegmentComparison]
    for cls in renderable:
        fields = {f.name for f in dataclasses.fields(cls)}
        assert 'caveat' in fields, cls.__name__


def test_the_sentence_carries_its_own_sample_sizes():
    """
    Most of why a sentence is preferred to a coefficient: a reader seeing
    "lowest third n=6" calibrates instantly, where r = 0.72 hides its n inside
    a decimal and loses it entirely on the way into a board paper.
    """
    profiles = population([(1, 1)] * 6 + [(3, 3)] * 6 + [(5, 5)] * 6)
    sentence = assoc.tertile_contrast(profiles, ('TID', 'T')).sentence()
    assert 'n=6' in sentence
    assert 'lowest third' in sentence and 'highest third' in sentence


# ── Priority view ───────────────────────────────────────────────────────────

def _mixed_population():
    """
    Nine diagnostic subdomains, deliberately varied so the quadrants populate:
      - Trust:    low score, strong contrast  -> investigate first
      - Autonomy: high score, strong contrast -> maintain
      - the rest: middling score, flat        -> monitor / lower priority
    """
    profiles = []
    for i in range(18):
        tier = i // 6                      # 0 = unhappy, 1 = middle, 2 = happy
        outcome = (1, 3, 5)[tier]
        scores = {}
        for domain, sub in assoc.DIAGNOSTIC_SUBDOMAINS:
            if (domain, sub) == ('BIT', 'T'):
                scores[(domain, sub)] = (1, 2, 4)[tier]      # low, moves
            elif (domain, sub) == ('TID', 'T'):
                scores[(domain, sub)] = (2, 4, 5)[tier]      # high, moves
            else:
                scores[(domain, sub)] = 3                    # flat
        profiles.append(respondent(outcome, scores, label=f'r{i}'))
    return profiles


def test_the_priority_view_is_ordered_by_the_size_of_the_contrast():
    """THE EXPLICIT INSTRUCTION for this phase."""
    view = assoc.priority_view(_mixed_population())
    sizes = [e.contrast.size for e in view.entries]
    assert sizes == sorted(sizes, reverse=True)
    assert len(view.entries) == 9


def test_a_low_scoring_strongly_associated_subdomain_is_investigate_first():
    view = assoc.priority_view(_mixed_population())
    first = {e.label for e in view.investigate_first()}
    assert 'Trust' in first


def test_a_high_scoring_strongly_associated_subdomain_is_not_investigate_first():
    """
    Autonomy moves with satisfaction AND is already the strongest score. That
    is something to protect, not a problem to fix — so it lands in Maintain.
    """
    view = assoc.priority_view(_mixed_population())
    autonomy = next(e for e in view.entries if e.diagnostic == ('TID', 'T'))
    assert autonomy.quadrant == 'maintain'


def test_the_quadrant_split_is_relative_to_this_group_not_absolute():
    """
    §10.5 forbids calling a score "low" while bands are unvalidated. The axes
    divide at THIS GROUP'S OWN MEDIAN, which is a statement about the data in
    front of the reader rather than a claim about the scale.
    """
    view = assoc.priority_view(_mixed_population())
    assert view.score_median is not None
    assert view.contrast_median is not None
    # Every entry's quadrant is explicable from those two medians alone.
    for entry in view.entries:
        below = entry.score <= view.score_median
        strong = (entry.contrast.size > 0
                  and entry.contrast.size > view.contrast_median)
        expected = ('investigate_first' if below and strong else
                    'monitor' if below else
                    'maintain' if strong else 'lower_priority')
        assert entry.quadrant == expected, entry.label


def test_a_subdomain_with_no_contrast_is_never_investigate_first():
    """
    THE BUG THE LIVE RUN FOUND.

    When one issue dominates — the ordinary case — most subdomains are flat and
    the MEDIAN contrast is 0. Under a `size >= median` rule, every flat
    subdomain satisfies `0 >= 0` and the whole list is flagged "Investigate
    first", including the eight that showed no association whatsoever.

    A priority list containing everything is not a priority list. It sends an
    organisation to investigate eight things that moved not at all.
    """
    profiles = []
    for i in range(18):
        tier = i // 6
        scores = {}
        for pair in assoc.DIAGNOSTIC_SUBDOMAINS:
            # Only Trust moves with satisfaction. Everything else is flat, so
            # the median contrast is exactly zero.
            scores[pair] = (1, 3, 5)[tier] if pair == ('BIT', 'T') else 3
        profiles.append(respondent((1, 3, 5)[tier], scores, label=f'r{i}'))

    view = assoc.priority_view(profiles)

    assert view.contrast_median == 0           # the degenerate case
    first = [e for e in view.entries if e.quadrant == 'investigate_first']
    assert [e.diagnostic for e in first] == [('BIT', 'T')],         [e.label for e in first]

    for entry in view.entries:
        if entry.contrast.size == 0:
            assert entry.quadrant != 'investigate_first', entry.label


def test_quadrant_labels_are_investigative_never_verdicts():
    """
    "Investigate first", not "Critical". The output is a reading order, and a
    verdict label would be the band §10.5 forbids wearing a different hat.
    """
    labels = set(assoc._QUADRANT_LABELS.values())
    assert labels == {'Investigate first', 'Monitor', 'Maintain',
                      'Lower priority'}

    # Matched on WHOLE WORDS. "Lower priority" is a statement about reading
    # order and is fine; a bare "Low" would be the band §10.5 forbids. A
    # substring scan cannot tell those apart and flags the legitimate label.
    import re
    words = {w for label in labels for w in re.findall(r"[a-z]+", label.lower())}
    for forbidden in ('critical', 'failing', 'risk', 'poor', 'bad',
                      'strength', 'gap', 'low', 'high', 'moderate',
                      'severe', 'urgent'):
        assert forbidden not in words, forbidden


def test_a_priority_view_below_the_floor_says_so_rather_than_being_empty():
    """
    "Too few people to look" and "nothing stood out" are different findings. An
    empty list renders as the second while meaning the first.
    """
    result = assoc.priority_view(population([(1, 1)] * 3 + [(5, 5)] * 3))
    assert isinstance(result, assoc.NotComputed)
    assert 'enough respondents' in result.reason


# ── Segment comparison ─────────────────────────────────────────────────────

def _segment(key='department', options=(('FIN', 'Finance'), ('OPS', 'Operations'))):
    return SimpleNamespace(
        segment_key=key, label=key.title(),
        options=[SimpleNamespace(option_code=c, option_label=l, sort_order=i,
                                 is_active=True)
                 for i, (c, l) in enumerate(options)],
    )


def _segmented(sizes, score_by_code):
    profiles = []
    for code, n in sizes.items():
        for i in range(n):
            p = respondent(4, {('BIT', 'T'): score_by_code[code]},
                           label=f'{code}{i}')
            p.segment_values = {'department': code} if code else {}
            profiles.append(p)
    return profiles


def test_a_segment_comparison_reports_each_group_against_the_whole():
    profiles = _segmented({'FIN': 6, 'OPS': 6}, {'FIN': 2, 'OPS': 4})
    result = assoc.segment_comparison(profiles, ('BIT', 'T'), _segment(),
                                      min_reporting_n=1)

    assert result.overall.mean == 3.0
    by_code = {c[0]: c for c in result.cells}
    assert by_code['FIN'][3] == -1.0        # difference from the whole group
    assert by_code['OPS'][3] == 1.0


def test_cells_come_back_in_the_segments_declared_order_not_ranked():
    """
    DELIBERATELY NOT A LEAGUE TABLE. A list of departments sorted by
    satisfaction is read as a performance ranking of their managers within
    about four seconds, and §13 closes by saying this instrument is not for
    ranking.
    """
    segment = _segment(options=(('FIN', 'Finance'), ('OPS', 'Operations'),
                                ('SAL', 'Sales')))
    profiles = _segmented({'FIN': 6, 'OPS': 6, 'SAL': 6},
                          {'FIN': 1, 'OPS': 5, 'SAL': 3})
    result = assoc.segment_comparison(profiles, ('BIT', 'T'), segment,
                                      min_reporting_n=1)

    assert [c[0] for c in result.cells] == ['FIN', 'OPS', 'SAL']
    # i.e. NOT ['OPS', 'SAL', 'FIN'], which is score order.


def test_the_spread_is_the_finding_rather_than_the_ranking():
    """
    A wide spread means the organisation is not one place — a prompt to
    investigate, not a scoreboard.
    """
    profiles = _segmented({'FIN': 6, 'OPS': 6}, {'FIN': 1, 'OPS': 5})
    result = assoc.segment_comparison(profiles, ('BIT', 'T'), _segment(),
                                      min_reporting_n=1)
    assert result.spread == 4.0


def test_undeclared_respondents_are_their_own_group_reported_last():
    profiles = _segmented({'FIN': 6, 'OPS': 6, None: 4},
                          {'FIN': 2, 'OPS': 4, None: 3})
    result = assoc.segment_comparison(profiles, ('BIT', 'T'), _segment(),
                                      min_reporting_n=1)
    assert result.cells[-1][0] is None
    assert result.cells[-1][1] == 'Not specified'
    assert result.cells[-1][2].n == 4


def test_a_segment_comparison_suppresses_small_cells_and_says_how_many():
    profiles = _segmented({'FIN': 20, 'OPS': 20, 'SAL': 3},
                          {'FIN': 3, 'OPS': 4, 'SAL': 1})
    segment = _segment(options=(('FIN', 'Finance'), ('OPS', 'Operations'),
                                ('SAL', 'Sales')))
    result = assoc.segment_comparison(profiles, ('BIT', 'T'), segment,
                                      min_reporting_n=5)

    assert result.suppressed_count >= 2      # complementary suppression applied
    suppressed = [c for c in result.cells if isinstance(c[2], sc.Suppressed)]
    assert sum(c[2].n for c in suppressed) >= 5


def test_a_segment_comparison_carries_the_caveat():
    profiles = _segmented({'FIN': 6, 'OPS': 6}, {'FIN': 2, 'OPS': 4})
    result = assoc.segment_comparison(profiles, ('BIT', 'T'), _segment(),
                                      min_reporting_n=1)
    assert result.caveat == domains.ASSOCIATION_CAVEAT


# ── What is deliberately absent ────────────────────────────────────────────

def test_there_is_no_correlation_coefficient():
    """
    The decision taken in this phase. The word "correlation" appears nowhere in
    the specification; §10.5 asks for comparisons during early implementation.
    """
    with pytest.raises(sc.ScoringError) as exc:
        assoc.correlation([], ('TID', 'T'))
    message = str(exc.value)
    assert '§10.4' in message
    assert 'tertile_contrast' in message
    assert 'Validation Report' in message


def test_the_module_computes_no_coefficient_by_another_name():
    """
    A guard against the obvious workaround — adding `pearson()` or
    `association_strength()` beside the raising stub.
    """
    suspicious = [name for name in dir(assoc)
                  if any(w in name.lower() for w in
                         ('correlat', 'pearson', 'spearman', 'regress',
                          'coefficient', 'r_value'))]
    assert suspicious == ['correlation'], suspicious


def test_there_is_no_cross_tab_function():
    """
    ONE SEGMENT AT A TIME. Crossing department by tenure band collapses cell
    sizes fast and is the easiest way to re-identify someone in an otherwise
    safe dataset. No function here takes two segment keys.
    """
    import inspect

    for name, obj in vars(assoc).items():
        if not inspect.isfunction(obj):
            continue
        params = inspect.signature(obj).parameters
        segment_params = [p for p in params if 'segment' in p]
        assert len(segment_params) <= 1, (name, segment_params)


def test_the_engine_does_not_import_flask():
    source = open(assoc.__file__, encoding='utf-8').read()
    assert 'import flask' not in source.lower()
    assert 'from flask' not in source.lower()
    assert 'current_app' not in source
