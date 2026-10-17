"""
The Reports screen and the on-screen analysis view.

The engine is pinned by tests/test_association.py without a database. This file
pins what a user actually sees: the three "needs" states, and that the §10.4
caveat survives the journey from the result object onto the page.
"""
import re

import pytest

from app import db
from app.analytics import association as assoc
from app.domains import ASSOCIATION_CAVEAT, SUBDOMAINS
from app.models import ResponseSession, SurveyItem, SurveyResponse
from app.provisioning import clone_instrument_to_org
from tests.conftest import login
from tests.factories import (
    make_cycle, make_deployment, make_master_instrument, make_org,
    make_org_admin, make_segment,
)

flat = lambda html: re.sub(r'\s+', ' ', html)


@pytest.fixture
def org(db):
    make_master_instrument()
    organisation = make_org('Kilimo Dairies')
    clone_instrument_to_org(organisation.id)
    admin = make_org_admin(organisation, email='l2@kilimo.test')
    db.session.commit()
    return {'org': organisation, 'admin': admin}


def _respond(org_id, deployment, outcome, diagnostic, segment=None):
    """One submitted session: a chosen outcome score and diagnostic score."""
    from datetime import datetime, timezone

    session = ResponseSession(
        deployment_id=deployment.id, org_id=org_id,
        segment_values=({'department': segment} if segment else {}),
        submitted_at=datetime.now(timezone.utc),
    )
    db.session.add(session)
    db.session.flush()

    items = db.session.query(SurveyItem).filter_by(org_id=org_id, version=1).all()
    for item in items:
        score = outcome if item.domain_code == 'GEM' else diagnostic
        db.session.add(SurveyResponse(
            session_id=session.id, deployment_id=deployment.id,
            org_id=org_id, item_id=item.id, score=score, not_applicable=False))
    db.session.flush()
    deployment.responses_received = (deployment.responses_received or 0) + 1


# ── The three states ───────────────────────────────────────────────────────

def test_a_cycle_with_no_deployment_says_deploy_it(client, org):
    make_cycle(org['org'], 'KD-1', status='open')
    db.session.commit()

    login(client, org['admin'])
    body = flat(client.get('/org/reports').get_data(as_text=True))
    assert 'not deployed yet' in body
    assert 'deploy the cycle' in body


def test_a_cycle_below_the_threshold_says_how_many_more_and_why(client, org):
    """
    THE THIRD STATE, and the one that is specific to this product.

    It is not a fault and the wording must not read as one — no amount of
    waiting fixes a group that is simply too small, and an admin who reads it
    as an error will go looking for a way to clear it.
    """
    cycle = make_cycle(org['org'], 'KD-1', status='open', min_reporting_n=5)
    deployment = make_deployment(org['org'], cycle, expected_respondents=20)
    for _ in range(2):
        _respond(org['org'].id, deployment, 4, 4)
    db.session.commit()

    login(client, org['admin'])
    body = flat(client.get('/org/reports').get_data(as_text=True))
    assert '2 of the 5 responses needed' in body
    assert '3 more' in body
    assert 'not a fault' in body
    assert 'could be traced back to them' in body


def test_an_open_deployment_is_reportable_and_marked_provisional(client, org):
    """
    The reports are offered, and the screen qualifies them. Withholding them
    until a close was this product's own invention; both siblings gate on
    responses alone.
    """
    cycle = make_cycle(org['org'], 'KD-1', status='open', min_reporting_n=1)
    deployment = make_deployment(org['org'], cycle, expected_respondents=20)
    for _ in range(3):
        _respond(org['org'].id, deployment, 4, 4)
    db.session.commit()

    login(client, org['admin'])
    body = flat(client.get('/org/reports').get_data(as_text=True))
    assert 'provisional' in body.lower()
    assert 'still collecting' in body
    assert '/report/executive' in body


def test_a_closed_deployment_above_the_threshold_is_ready(client, org):
    cycle = make_cycle(org['org'], 'KD-1', status='open', min_reporting_n=1)
    deployment = make_deployment(org['org'], cycle, expected_respondents=20)
    for _ in range(3):
        _respond(org['org'].id, deployment, 4, 4)
    deployment.closed = True
    db.session.commit()

    login(client, org['admin'])
    body = flat(client.get('/org/reports').get_data(as_text=True))
    assert 'ready' in body
    assert 'View the analysis' in body


# ── The analysis view ──────────────────────────────────────────────────────

def _deployment_with(org_record, pairs, segments=None):
    """pairs: list of (outcome, diagnostic). segments: parallel list of codes."""
    cycle = make_cycle(org_record['org'], 'KD-A', status='open',
                       min_reporting_n=1)
    deployment = make_deployment(org_record['org'], cycle, expected_respondents=50)
    for i, (outcome, diagnostic) in enumerate(pairs):
        _respond(org_record['org'].id, deployment, outcome, diagnostic,
                 segment=(segments[i] if segments else None))
    db.session.commit()
    return cycle


def test_the_analysis_view_reports_the_three_outcome_subdomains_separately(
        client, org):
    _deployment_with(org, [(4, 3)] * 6 + [(2, 3)] * 6)

    login(client, org['admin'])
    body = client.get('/org/cycles/KD-A/analysis').get_data(as_text=True)

    for sub in SUBDOMAINS['GEM']:
        from app.domains import subdomain_label
        assert subdomain_label('GEM', sub) in body
    # And no composite anywhere.
    assert 'Overall Satisfaction Score' not in body
    assert 'Composite' not in body


def test_the_analysis_view_carries_the_10_4_caveat(client, org):
    """
    THE NAMED ACCEPTANCE CRITERION, at the point it matters — on the page a
    person reads, not merely on the object.
    """
    _deployment_with(org, [(1, 1)] * 6 + [(3, 3)] * 6 + [(5, 5)] * 6)

    login(client, org['admin'])
    body = flat(client.get('/org/cycles/KD-A/analysis').get_data(as_text=True))
    assert ASSOCIATION_CAVEAT in body
    # Present more than once: beside the contrasts AND beside the priority view.
    assert body.count('do not, by themselves, establish') >= 2


def test_the_analysis_view_shows_the_contrast_as_a_sentence(client, org):
    _deployment_with(org, [(1, 1)] * 6 + [(3, 3)] * 6 + [(5, 5)] * 6)

    login(client, org['admin'])
    body = flat(client.get('/org/cycles/KD-A/analysis').get_data(as_text=True))
    assert 'Employees in the lowest third on General Job Satisfaction' in body
    assert 'Those in the highest third score' in body
    assert 'n=6' in body


def test_the_priority_view_is_ordered_by_contrast_on_the_page(client, org):
    _deployment_with(org, [(1, 1)] * 6 + [(3, 3)] * 6 + [(5, 5)] * 6)

    login(client, org['admin'])
    body = client.get('/org/cycles/KD-A/analysis').get_data(as_text=True)
    assert 'Where to look first' in body
    assert 'Investigate first' in body or 'Lower priority' in body
    assert 'suggested order for looking' in flat(body)


def test_a_group_too_small_for_a_contrast_says_so_on_the_page(client, org):
    """Not an empty panel — "too few people" and "nothing found" differ."""
    _deployment_with(org, [(1, 1)] * 3 + [(5, 5)] * 3)

    login(client, org['admin'])
    body = flat(client.get('/org/cycles/KD-A/analysis').get_data(as_text=True))
    assert 'respondents are needed' in body or 'are needed' in body
    assert f'at least {assoc.ASSOCIATION_MIN_N} respondents' in body


def test_the_analysis_view_never_shows_a_band_label(client, org):
    """§10.5, on the page."""
    _deployment_with(org, [(1, 1)] * 6 + [(3, 3)] * 6 + [(5, 5)] * 6)

    login(client, org['admin'])
    body = client.get('/org/cycles/KD-A/analysis').get_data(as_text=True)
    assert 'not been validated' in body
    for forbidden in ('Critical Gap', 'Moderate Gap', 'Minor Gap', 'Strength'):
        assert forbidden not in body


def test_the_analysis_view_states_the_scoring_method_in_force(client, org):
    """
    §10.2 requires the method to be documented. A figure whose rule is not on
    the page is not an auditable figure.
    """
    _deployment_with(org, [(4, 4)] * 6)

    login(client, org['admin'])
    body = flat(client.get('/org/cycles/KD-A/analysis').get_data(as_text=True))
    assert 'mean of the three subdomain scores' in body


def test_a_segment_comparison_appears_and_is_not_sorted_by_score(client, org):
    """
    Groups in their DEFINED order. A list of departments sorted by satisfaction
    is read as a ranking of their managers within seconds.
    """
    make_segment(org['org'], 'department', 'Department',
                 [('FIN', 'Finance'), ('OPS', 'Operations')])
    db.session.commit()

    codes = ['FIN'] * 6 + ['OPS'] * 6
    _deployment_with(org, [(2, 2)] * 6 + [(5, 5)] * 6, segments=codes)

    login(client, org['admin'])
    body = client.get('/org/cycles/KD-A/analysis').get_data(as_text=True)
    assert 'This is not a ranking' in flat(body)
    # Finance (lower scoring) still appears before Operations.
    assert body.index('Finance') < body.index('Operations')


def test_the_analysis_view_lists_what_it_does_not_show(client, org):
    _deployment_with(org, [(4, 4)] * 6)

    login(client, org['admin'])
    body = flat(client.get('/org/cycles/KD-A/analysis').get_data(as_text=True))
    assert 'What this does not show' in body
    assert 'No individual response' in body
    assert 'No causal claim' in body
    assert 'No single overall satisfaction figure' in body


# ── Scoping ────────────────────────────────────────────────────────────────

def test_another_orgs_analysis_is_not_reachable(client, db, org):
    other = make_org('Globex Inc')
    other_cycle = make_cycle(other, 'GBX-A', status='open')
    db.session.commit()

    login(client, org['admin'])
    assert client.get('/org/cycles/GBX-A/analysis').status_code == 404
