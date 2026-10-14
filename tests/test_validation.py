"""
THE VALIDATION REPORT — §15 and §18.

The statistics are pinned against hand-computed values with no app context, the
same discipline as the scoring engine: a coefficient that is quietly wrong
looks exactly like one that is right.

This is also the one place in the product where a correlation is legitimate.
tests/test_association.py asserts the client-facing engine refuses to compute
one; this file asserts the validation layer does.
"""
import math
from datetime import datetime, timezone

import pytest

from app import db, domains
from app.analytics import scoring as sc
from app.analytics import validation as val
from app.models import ResponseSession, SurveyItem, SurveyResponse
from app.provisioning import clone_instrument_to_org
from tests.conftest import login
from tests.factories import (
    make_cycle, make_deployment, make_master_admin, make_master_instrument,
    make_org, make_org_admin,
)


# ── Pearson ────────────────────────────────────────────────────────────────

def test_a_perfect_positive_relationship_is_one():
    assert val.pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)


def test_a_perfect_negative_relationship_is_minus_one():
    assert val.pearson([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)


def test_a_hand_computed_correlation_reproduces():
    """
    x = [1,2,3,4,5], y = [2,4,5,4,5]
      mean x = 3, mean y = 4
      Sxy = (-2)(-2)+(-1)(0)+(0)(1)+(1)(0)+(2)(1) = 4+0+0+0+2 = 6
      Sxx = 4+1+0+1+4 = 10 ; Syy = 4+0+1+0+1 = 6
      r = 6 / sqrt(60) = 0.7745966...
    """
    assert val.pearson([1, 2, 3, 4, 5], [2, 4, 5, 4, 5]) == pytest.approx(
        6 / math.sqrt(60))


def test_no_variance_gives_none_not_zero():
    """
    Everyone answering identically means there is nothing to correlate. That is
    a different fact from "no relationship", and rendering it as 0.00 would
    report an absence of variance as an absence of association.
    """
    assert val.pearson([3, 3, 3, 3], [1, 2, 3, 4]) is None
    assert val.pearson([1, 2, 3, 4], [3, 3, 3, 3]) is None


def test_too_few_pairs_gives_none():
    assert val.pearson([1, 2], [2, 4]) is None


# ── Cronbach's alpha ───────────────────────────────────────────────────────

def test_alpha_of_identical_items_is_one():
    """Four items that are copies of each other are perfectly consistent."""
    rows = [[v, v, v, v] for v in (1, 2, 3, 4, 5, 1, 2, 3, 4, 5)]
    assert val.cronbach_alpha(rows) == pytest.approx(1.0)


def test_a_hand_computed_alpha_reproduces():
    """
    Three items, four respondents:
        [1,2,3], [2,3,4], [3,4,5], [4,5,6]

      item variances (population): each column is 1,2,3,4 -> var = 1.25
        sum = 3.75
      totals: 6, 9, 12, 15 -> var = 11.25
      alpha = (3/2) * (1 - 3.75/11.25) = 1.5 * (2/3) = 1.0
    """
    rows = [[1, 2, 3], [2, 3, 4], [3, 4, 5], [4, 5, 6]]
    assert val.cronbach_alpha(rows) == pytest.approx(1.0)


def test_alpha_of_unrelated_items_is_low():
    rows = [[1, 5, 3], [5, 1, 3], [3, 3, 5], [2, 4, 1],
            [4, 2, 5], [1, 4, 2], [5, 2, 4], [3, 5, 1]]
    alpha = val.cronbach_alpha(rows)
    assert alpha is not None
    assert alpha < 0.5


def test_alpha_uses_listwise_deletion():
    """
    Pairwise deletion computes each variance from a different subset, and the
    resulting alpha describes no actual group of people. Rows with a gap are
    dropped entirely, and the n is reported alongside.
    """
    complete = [[1, 2, 3], [2, 3, 4], [3, 4, 5], [4, 5, 6]]
    with_gap = complete + [[5, None, 7]]
    assert val.cronbach_alpha(with_gap) == pytest.approx(
        val.cronbach_alpha(complete))


def test_alpha_is_none_when_everyone_scored_the_same_total():
    assert val.cronbach_alpha([[2, 2], [1, 3], [3, 1]]) is None


def test_alpha_needs_at_least_two_items():
    assert val.cronbach_alpha([[1], [2], [3], [4]]) is None


# ── Corrected item-total ───────────────────────────────────────────────────

def test_the_item_total_correlation_is_corrected():
    """
    THE CLASSIC WAY THIS CHECK IS GOT WRONG.

    Correlating an item with a total that CONTAINS it guarantees a healthy
    figure and tells you nothing. Here item 0 is pure noise against two
    correlated items; corrected, it must look weak.
    """
    # Four items. Items 1-3 move together; item 0 is noise.
    rows = [[5, 1, 1, 1], [1, 2, 2, 2], [4, 3, 3, 3], [2, 4, 4, 4],
            [5, 5, 5, 5], [1, 1, 1, 1], [3, 2, 2, 2], [2, 5, 5, 5]]
    noisy = val.corrected_item_total(rows, 0)
    coherent = val.corrected_item_total(rows, 1)
    assert noisy is not None and coherent is not None

    # Tied to the threshold the code actually acts on — Reliability.weak_items
    # flags anything below 0.30 — rather than to an arbitrary ceiling. Note the
    # coherent item is still diluted by the noisy one in its own "rest" total,
    # which is exactly what a corrected coefficient is supposed to show.
    assert noisy < 0.30
    assert coherent > 0.70
    assert coherent > noisy


def test_an_item_that_does_not_fit_is_flagged_as_weak():
    """The decision the coefficient drives, not just the coefficient."""
    rows = [[5, 1, 1, 1], [1, 2, 2, 2], [4, 3, 3, 3], [2, 4, 4, 4],
            [5, 5, 5, 5], [1, 1, 1, 1], [3, 2, 2, 2], [2, 5, 5, 5]]
    reliability = val.Reliability(
        measure=('BIT', 'T'), alpha=0.8, n=len(rows), items=4,
        item_total={f'BIT-T-0{i + 1}': val.corrected_item_total(rows, i)
                    for i in range(4)})
    assert reliability.weak_items == ['BIT-T-01']


# ── Over real profiles ─────────────────────────────────────────────────────

def _profile(session_id, scores):
    """scores maps (domain, sub) -> list of 4 item scores (None = N/A)."""
    answers = []
    for (domain, sub), values in scores.items():
        for index, value in enumerate(values, start=1):
            answers.append(sc.Answer(
                domain_code=domain, subdomain_code=sub,
                item_code=domains.item_code(domain, sub, index),
                score=value, not_applicable=(value is None)))
    return sc.build_profile(session_id, answers)


def _population(n=40, coherent=True):
    """n respondents, varying across the scale so there is variance."""
    profiles = []
    for i in range(n):
        base = (i % 5) + 1
        scores = {}
        for domain, sub in domains.subdomain_pairs():
            if coherent:
                scores[(domain, sub)] = [base] * 4
            else:
                scores[(domain, sub)] = [((i + j) % 5) + 1 for j in range(4)]
        profiles.append(_profile(f's{i}', scores))
    return profiles


def test_reliability_is_computed_for_every_subdomain():
    report = val.build(_population(40))
    computed = [r for _, r in report.reliability if isinstance(r, val.Reliability)]
    assert len(computed) == 12
    assert all(r.items == 4 for r in computed)
    assert all(r.n == 40 for r in computed)


def test_a_small_sample_refuses_to_report_a_coefficient():
    """
    THE RULE THAT MATTERS MOST HERE. Alpha from eleven people would license
    exactly the claim §18's product note withholds — that the instrument is
    validated.
    """
    report = val.build(_population(10))
    for _, result in report.reliability:
        assert isinstance(result, val.NotComputable)
        assert '30 are needed' in result.reason


def test_the_threshold_is_configurable_and_binds():
    assert all(isinstance(r, val.Reliability)
               for _, r in val.build(_population(12), min_n=10).reliability)
    assert all(isinstance(r, val.NotComputable)
               for _, r in val.build(_population(12), min_n=20).reliability)


def test_the_gem_discriminant_check_runs_on_all_three_pairs():
    """§18 singles out General Job Satisfaction against Emotional Experience."""
    report = val.build(_population(40))
    assert len(report.discriminants) == 3
    usable = [d for d in report.discriminants if isinstance(d, val.Discriminant)]
    assert usable
    labels = {(d.left_label, d.right_label) for d in usable}
    assert ('General Job Satisfaction', 'Emotional Experience') in labels


def test_two_subdomains_that_move_together_are_flagged():
    """
    In this population every subdomain carries the same score, so the three
    Overall Satisfaction subdomains are perfectly correlated — the very case
    §18 asks to be examined.
    """
    report = val.build(_population(40))
    usable = [d for d in report.discriminants if isinstance(d, val.Discriminant)]
    assert all(d.concerning for d in usable)
    assert any('same underlying sentiment' in d.sentence() for d in usable)


def test_distinguishable_subdomains_are_not_flagged():
    """The complement — without it, a check that flagged everything would pass."""
    profiles = []
    for i in range(40):
        scores = {}
        for index, sub in enumerate(domains.SUBDOMAINS['GEM']):
            # Deliberately unrelated patterns across the three subdomains.
            value = (((i * (index + 1)) + index * 2) % 5) + 1
            scores[('GEM', sub)] = [value] * 4
        profiles.append(_profile(f's{i}', scores))

    report = val.build(profiles)
    usable = [d for d in report.discriminants if isinstance(d, val.Discriminant)]
    assert usable
    assert not all(d.concerning for d in usable)


def test_item_quality_surfaces_the_highest_na_rate_first():
    profiles = []
    for i in range(40):
        scores = {p: [3, 3, 3, 3] for p in domains.subdomain_pairs()}
        scores[('PIE', 'P')] = [None, None, 3, 3]      # 50% N/A
        profiles.append(_profile(f's{i}', scores))

    report = val.build(profiles)
    worst = report.items[0]
    assert worst.measure == ('PIE', 'P')
    assert worst.na_rate == pytest.approx(100.0)
    assert worst.high_na is True


def test_straight_lining_counts_both_kinds():
    uniform = [_profile(f'u{i}', {p: [4, 4, 4, 4]
                                  for p in domains.subdomain_pairs()})
               for i in range(5)]
    varied = [_profile(f'v{i}', {p: [1, 2, 3, 4]
                                 for p in domains.subdomain_pairs()})
              for i in range(5)]

    result = val.straight_lining(uniform + varied)
    assert result.sessions == 10
    assert result.identical_whole == 5
    assert result.whole_pct == pytest.approx(50.0)
    assert result.identical_within_domain == 5


# ── The conclusion ─────────────────────────────────────────────────────────

def test_bands_and_the_composite_stay_disabled_even_on_good_numbers():
    """
    DEFAULTS TO NO, and stays there. §18's product note is the standard: the
    framework, items, scoring approach and interpretation rules must be piloted
    and validated before the system is marketed as validated. Coefficients
    alone do not clear that bar, and this page will not pretend they do.
    """
    report = val.build(_population(60))
    assert report.recommendation['bands_may_be_enabled'] is False
    assert report.recommendation['composite_may_be_enabled'] is False
    assert 'remain disabled' in report.recommendation['statement']
    assert 'structured pilot' in report.recommendation['statement']


def test_the_conclusion_names_what_is_outstanding():
    report = val.build(_population(40))
    blockers = ' '.join(report.recommendation['blockers'])
    assert 'same sentiment in different words' in blockers


def test_an_empty_platform_says_so_rather_than_failing():
    report = val.build([])
    assert report.n == 0
    assert report.recommendation['blockers']
    assert all(isinstance(r, val.NotComputable) for _, r in report.reliability)


def test_the_module_does_not_import_flask():
    source = open(val.__file__, encoding='utf-8').read()
    assert 'import flask' not in source.lower()
    assert 'from flask' not in source.lower()


# ── The screen ─────────────────────────────────────────────────────────────

@pytest.fixture
def platform(db):
    make_master_instrument()
    org = make_org('Kilimo Dairies')
    clone_instrument_to_org(org.id)
    l1 = make_master_admin(email='l1@leadto.test')
    cycle = make_cycle(org, 'KD-1', status='open', min_reporting_n=1)
    wave = make_deployment(org, cycle, expected_respondents=60,
                           public_slug='slug-val')
    items = db.session.query(SurveyItem).filter_by(org_id=org.id).all()
    for i in range(40):
        session = ResponseSession(
            deployment_id=wave.id, org_id=org.id, segment_values={},
            submitted_at=datetime.now(timezone.utc))
        db.session.add(session)
        db.session.flush()
        for item in items:
            db.session.add(SurveyResponse(
                session_id=session.id, deployment_id=wave.id, org_id=org.id,
                item_id=item.id, score=(i % 5) + 1, not_applicable=False))
        wave.responses_received += 1
    wave.closed = True
    db.session.commit()
    return {'org': org, 'l1': l1, 'cycle': cycle}


def test_the_validation_screen_renders_with_real_data(client, platform):
    login(client, platform['l1'])
    body = client.get('/admin/validation').get_data(as_text=True)

    assert 'Validation Report' in body
    assert 'Internal consistency' in body
    assert 'General Job Satisfaction' in body
    assert 'Conclusion' in body
    assert 'remain disabled' in body


def test_the_validation_screen_says_it_is_not_about_any_client(client, platform):
    login(client, platform['l1'])
    import re
    body = re.sub(r'\s+', ' ',
                  client.get('/admin/validation').get_data(as_text=True))
    assert 'evidence about the instrument, not about any organisation' in body
    assert 'never shown to a client' in body


def test_the_validation_screen_can_be_narrowed_to_one_organisation(client,
                                                                   platform):
    login(client, platform['l1'])
    body = client.get(
        f'/admin/validation?org={platform["org"].id}').get_data(as_text=True)
    assert 'Kilimo Dairies only' in body


def test_the_validation_screen_is_l1_only(client, db, platform):
    admin = make_org_admin(platform['org'], email='l2@kilimo.test')
    db.session.commit()
    login(client, admin)
    assert client.get('/admin/validation').status_code == 302


def test_an_unknown_org_filter_is_404(client, platform):
    login(client, platform['l1'])
    assert client.get(
        f'/admin/validation?org={domains.MASTER_ORG_ID}').status_code == 404
