"""
The three gaps found by walking the finished product rather than the plan.

  1. L1 held "Reports: all, read-only" in the permission table and could not
     reach one — every report route lived in app/org/, whose every query is
     scoped by current_user.org_id, and L1 has no org_id.
  2. Usage & Costs was a placeholder while ai_cost_events held real rows.
  3. improvement_actions existed as a table, was read by the reports, and had
     no way to create one — so §16's follow-up loop could not be exercised.

All three are the same class of mistake: a capability that exists on paper and
has no route behind it.
"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app import db, domains
from app.analytics import reports
from app.models import (
    AiCostEvent, ImprovementActions, ReportEvent, ResponseSession,
    SurveyItem, SurveyResponse,
)
from app.provisioning import clone_instrument_to_org
from tests.conftest import login
from tests.factories import (
    make_branch, make_branch_admin, make_cycle, make_deployment,
    make_master_admin, make_master_instrument, make_org, make_org_admin,
)


@pytest.fixture
def world(db):
    make_master_instrument()
    org = make_org('Kilimo Dairies')
    clone_instrument_to_org(org.id)
    admin = make_org_admin(org, email='l2@kilimo.test')
    l1 = make_master_admin(email='l1@leadto.test')
    db.session.commit()
    return {'org': org, 'admin': admin, 'l1': l1}


def _closed_wave(world, cycle_id='KD-1', n=18, branch=None, min_n=1):
    cycle = make_cycle(world['org'], cycle_id, status='open', branch=branch,
                       min_reporting_n=min_n)
    wave = make_deployment(world['org'], cycle, branch=branch,
                           expected_respondents=n + 6,
                           public_slug=f'slug-{cycle_id}')
    items = db.session.query(SurveyItem).filter_by(
        org_id=world['org'].id, version=1).all()
    for i in range(n):
        tier = i * 3 // n
        session = ResponseSession(
            deployment_id=wave.id, org_id=world['org'].id,
            branch_id=wave.branch_id, segment_values={},
            submitted_at=datetime.now(timezone.utc))
        db.session.add(session)
        db.session.flush()
        for item in items:
            db.session.add(SurveyResponse(
                session_id=session.id, deployment_id=wave.id,
                org_id=world['org'].id, item_id=item.id,
                score=(2, 3, 5)[tier], not_applicable=False))
        wave.responses_received = (wave.responses_received or 0) + 1
    wave.closed = True
    cycle.status = 'closed'
    db.session.commit()
    return cycle, wave


# ── 1. L1 can reach a client's reports ─────────────────────────────────────

def test_l1_can_open_a_clients_report(client, world):
    """
    The permission table always said so; until now no route could satisfy it,
    because L1 has no org_id and every report route was org-scoped.
    """
    cycle, _ = _closed_wave(world)
    login(client, world['l1'])

    res = client.get(f'/admin/orgs/{world["org"].id}/reports/{cycle.id}/executive')
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert 'Executive Report' in body
    assert 'Read-only' in body


def test_l1_can_download_a_clients_report(client, world):
    cycle, _ = _closed_wave(world)
    login(client, world['l1'])

    res = client.get(
        f'/admin/orgs/{world["org"].id}/reports/{cycle.id}/executive/pdf')
    assert res.status_code == 200
    assert res.data[:4] == b'%PDF'


def test_l1_gets_no_interpret_action(client, world):
    """
    READ-ONLY IS THE POINT. A narrative characterising a client's workforce is
    written by that client's own administrator, who is accountable for it — not
    by the platform operator on their behalf.
    """
    cycle, _ = _closed_wave(world)
    login(client, world['l1'])

    body = client.get(
        f'/admin/orgs/{world["org"].id}/reports/{cycle.id}/executive'
    ).get_data(as_text=True)
    assert 'Write the interpretation' not in body
    assert 'Regenerate' not in body
    assert 'written by that client' in body


def test_an_l1_download_is_audited(client, world):
    """
    A platform operator opening a client's workforce feedback is precisely the
    access §13.4 exists to make reviewable.
    """
    cycle, _ = _closed_wave(world)
    login(client, world['l1'])
    client.get(f'/admin/orgs/{world["org"].id}/reports/{cycle.id}/hr_od/pdf')

    event = db.session.query(ReportEvent).one()
    assert event.generated_by == world['l1'].id

    body = client.get('/admin/audit').get_data(as_text=True)
    assert 'Platform operator downloaded' in body


def test_l1_still_cannot_reach_a_report_below_the_threshold(client, world):
    """The suppression gate does not relax for the platform operator."""
    branch = make_branch(world['org'], 'Nakuru plant')
    cycle, _ = _closed_wave(world, 'KD-SMALL', n=3, branch=branch, min_n=5)
    login(client, world['l1'])

    body = client.get(
        f'/admin/orgs/{world["org"].id}/reports/{cycle.id}/executive'
    ).get_data(as_text=True)
    assert 'at least 5 are needed' in body


def test_an_org_admin_cannot_use_the_l1_report_route(client, world):
    """
    THE CROSS-TENANT LEAK THIS FILE FOUND.

    These routes were first gated on VIEW_REPORTS — which L2 and L3 also hold —
    and nothing in the admin blueprint is org-scoped, because the org comes
    from the URL. One client's administrator could therefore read another
    client's results by editing the id in the address bar.

    The org blueprint can gate on VIEW_REPORTS safely because every query there
    is filtered by current_user.org_id. Here there is no such filter, so the
    gate has to be a capability only L1 holds.
    """
    cycle, _ = _closed_wave(world)
    login(client, world['admin'])
    res = client.get(
        f'/admin/orgs/{world["org"].id}/reports/{cycle.id}/executive')
    assert res.status_code == 302
    assert '/org/' in res.headers['Location']


def test_an_org_admin_cannot_read_ANOTHER_orgs_report_through_the_admin_route(
        client, db, world):
    """The leak itself, pinned: Acme's admin must not reach Globex's results."""
    globex = make_org('Globex Inc')
    clone_instrument_to_org(globex.id)
    other_cycle = make_cycle(globex, 'GBX-1', status='open', min_reporting_n=1)
    db.session.commit()

    login(client, world['admin'])          # an ACME administrator
    res = client.get(f'/admin/orgs/{globex.id}/reports/GBX-1/executive')
    assert res.status_code == 302
    assert 'Globex' not in res.get_data(as_text=True)

    res = client.get(f'/admin/orgs/{globex.id}/reports/GBX-1/executive/pdf')
    assert res.data[:4] != b'%PDF'


def test_a_branch_admin_cannot_use_the_l1_report_route(client, db, world):
    branch = make_branch(world['org'], 'Nakuru plant')
    l3 = make_branch_admin(world['org'], branch, email='l3@kilimo.test')
    cycle, _ = _closed_wave(world)
    db.session.commit()

    login(client, l3)
    res = client.get(
        f'/admin/orgs/{world["org"].id}/reports/{cycle.id}/executive')
    assert res.status_code == 302


def test_the_master_template_org_has_no_reports(client, world):
    login(client, world['l1'])
    assert client.get(
        f'/admin/orgs/{domains.MASTER_ORG_ID}/reports/KD-1/executive'
    ).status_code == 404


# ── 2. Usage & Costs ───────────────────────────────────────────────────────

def test_the_usage_screen_reports_real_cost_events(client, world):
    db.session.add(AiCostEvent(
        org_id=world['org'].id, cycle_id=None, report_type='executive',
        model='claude-opus-5-5', input_tokens=5000, output_tokens=3000,
        cost_usd=Decimal('0.1000')))
    db.session.commit()

    login(client, world['l1'])
    body = client.get('/admin/usage').get_data(as_text=True)
    assert 'Kilimo Dairies' in body
    assert '0.10' in body
    assert '5,000' in body


def test_the_usage_screen_is_l1_only(client, world):
    login(client, world['admin'])
    res = client.get('/admin/usage')
    assert res.status_code == 302


def test_cost_and_access_records_stay_separate(client, world):
    """
    ai_cost_events is operational; report_events is a §13.4 compliance record.
    Merging them means a cost cleanup deletes an access record.
    """
    cycle, _ = _closed_wave(world)
    login(client, world['l1'])
    client.get(f'/admin/orgs/{world["org"].id}/reports/{cycle.id}/executive/pdf')

    assert db.session.query(ReportEvent).count() == 1
    assert db.session.query(AiCostEvent).count() == 0   # no narrative was run


# ── 3. Improvement actions — §16's follow-up loop ──────────────────────────

def test_an_action_can_be_recorded_against_a_subdomain(client, world):
    cycle, _ = _closed_wave(world)
    login(client, world['admin'])

    client.post(f'/org/cycles/{cycle.id}/actions/new', data={
        'title': 'Review approval thresholds for routine purchases',
        'subdomain_pair': 'TID|T',
        'intervention_type': 'work_design',
        'owner_label': 'Head of Operations'}, follow_redirects=True)

    action = db.session.query(ImprovementActions).one()
    assert action.domain_code == 'TID'
    assert action.subdomain_code == 'T'
    assert action.status == 'proposed'
    assert action.owner_label == 'Head of Operations'


def test_an_action_naming_a_respondent_is_refused_with_an_explanation(
        client, world):
    """
    "Follow up with whoever scored Trust a 1" is the natural thing for a
    well-meaning manager to type, and it is the re-identification §13.5-§13.6
    exists to prevent. Refused at the point of entry so the person typing it is
    told why, rather than only caught later by a privacy sweep.
    """
    cycle, _ = _closed_wave(world)
    login(client, world['admin'])

    res = client.post(f'/org/cycles/{cycle.id}/actions/new', data={
        'title': 'Speak to the person who scored Trust lowest',
        'owner_label': 'Line manager'}, follow_redirects=True)

    assert 'points at an individual respondent' in res.get_data(as_text=True)
    assert db.session.query(ImprovementActions).count() == 0


def test_an_owner_naming_a_respondent_is_refused_too(client, world):
    cycle, _ = _closed_wave(world)
    login(client, world['admin'])

    res = client.post(f'/org/cycles/{cycle.id}/actions/new', data={
        'title': 'Review workloads',
        'owner_label': 'whoever gave the lowest score'}, follow_redirects=True)
    assert 'points at an individual respondent' in res.get_data(as_text=True)
    assert db.session.query(ImprovementActions).count() == 0


def test_training_is_refused_as_a_response_to_a_reward_finding(client, world):
    """
    §8.5, enforced at the point of entry as well as in the catalogue filter:
    "Training should not be treated as a substitute for addressing
    substantiated compensation inequities or structural reward problems."
    """
    cycle, _ = _closed_wave(world)
    login(client, world['admin'])

    res = client.post(f'/org/cycles/{cycle.id}/actions/new', data={
        'title': 'Resilience workshop for the finance team',
        'subdomain_pair': 'PIE|P',
        'intervention_type': 'training',
        'owner_label': 'HR'}, follow_redirects=True)

    body = res.get_data(as_text=True)
    assert 'not a substitute' in body
    assert 'pay-equity review' in body
    assert db.session.query(ImprovementActions).count() == 0


def test_a_pay_equity_review_against_the_same_finding_is_accepted(client, world):
    """The complement — the refusal is about the TYPE, not about the domain."""
    cycle, _ = _closed_wave(world)
    login(client, world['admin'])

    client.post(f'/org/cycles/{cycle.id}/actions/new', data={
        'title': 'Commission a pay-equity review',
        'subdomain_pair': 'PIE|P',
        'intervention_type': 'policy_or_structural',
        'owner_label': 'Reward team'}, follow_redirects=True)
    assert db.session.query(ImprovementActions).count() == 1


def test_a_half_filled_pair_cannot_be_constructed(client, world):
    """
    The form posts one field, not two. A subdomain without its domain names
    three different constructs — 'I' is Purpose and Impact, Positive
    Interactions AND Industry Comparison.
    """
    cycle, _ = _closed_wave(world)
    login(client, world['admin'])

    res = client.post(f'/org/cycles/{cycle.id}/actions/new', data={
        'title': 'Something', 'subdomain_pair': 'TID'})
    assert res.status_code == 400
    assert db.session.query(ImprovementActions).count() == 0


def test_completing_an_action_records_when_and_what_changed(client, world):
    cycle, _ = _closed_wave(world)
    login(client, world['admin'])
    client.post(f'/org/cycles/{cycle.id}/actions/new', data={
        'title': 'Delegate purchase approvals', 'subdomain_pair': 'TID|T',
        'owner_label': 'Head of Operations'}, follow_redirects=True)

    action = db.session.query(ImprovementActions).one()
    client.post(f'/org/actions/{action.id}/status', data={
        'status': 'completed',
        'evidence_note': 'Threshold raised to KES 50,000 in March.'},
        follow_redirects=True)

    db.session.expire_all()
    action = db.session.query(ImprovementActions).one()
    assert action.status == 'completed'
    assert action.completed_at is not None
    assert 'KES 50,000' in action.evidence_note


def test_actions_appear_in_the_report_alongside_the_change_not_joined_to_it(
        client, world):
    """
    §16: completion and improvement are reported side by side. Asserting the
    first produced the second is the most natural sentence in the world to
    write, and exactly what the caveat forbids.
    """
    first, _ = _closed_wave(world, 'KD-A')
    second = make_cycle(world['org'], 'KD-B', status='open',
                        min_reporting_n=1, previous_cycle_id=first.id)
    db.session.commit()
    _, wave = _closed_wave(world, 'KD-B2')      # ensure data exists
    db.session.commit()

    login(client, world['admin'])
    client.post(f'/org/cycles/{first.id}/actions/new', data={
        'title': 'Delegate purchase approvals', 'subdomain_pair': 'TID|T',
        'owner_label': 'Head of Operations'}, follow_redirects=True)
    action = db.session.query(ImprovementActions).one()
    client.post(f'/org/actions/{action.id}/status',
                data={'status': 'completed'}, follow_redirects=True)

    body = client.get(f'/org/cycles/{first.id}/actions').get_data(as_text=True)
    assert 'without claiming that one produced the other' in body


def test_a_branch_admin_cannot_touch_another_branchs_action(client, db, world):
    nairobi = make_branch(world['org'], 'Nairobi')
    mombasa = make_branch(world['org'], 'Mombasa')
    cycle = make_cycle(world['org'], 'KD-BR', status='open', branch=mombasa)
    action = ImprovementActions(
        org_id=world['org'].id, branch_id=mombasa.id, cycle_id=cycle.id,
        title='Mombasa action', status='proposed')
    db.session.add(action)
    intruder = make_branch_admin(world['org'], nairobi, email='l3@nbo.test')
    db.session.commit()

    login(client, intruder)
    res = client.post(f'/org/actions/{action.id}/status',
                      data={'status': 'completed'})
    assert res.status_code == 404
    db.session.expire_all()
    assert db.session.query(ImprovementActions).one().status == 'proposed'
