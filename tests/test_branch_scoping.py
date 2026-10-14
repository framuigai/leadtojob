"""
PHASE 2'S ACCEPTANCE TEST.

Two guarantees, and they fail in different directions:

  TENANCY   — org A never sees org B. A failure here is a cross-tenant leak of
              one employer's workforce feedback to another employer.
  BRANCH    — a branch admin sees only their own branch, and never an org-wide
              wave. A failure here is an internal leak, which is likelier and
              is the one a client will actually notice.

Both are enforced by ROUTE-LEVEL SCOPED QUERIES (`_scoped()` in
app/org/routes.py), never by RLS — the app connects as the table owner and
bypasses row-level security entirely.

Every test here switches identity mid-test, which is exactly the case
conftest.login() exists for: without clearing Flask-Login's `g._login_user`
cache the second login is silently a no-op and the whole file would pass as the
first user.
"""
import pytest

from app.models import MeasurementCycle, Segment, SurveyDeployment
from tests.conftest import login
from tests.factories import (
    make_branch, make_branch_admin, make_cycle, make_deployment, make_org,
    make_org_admin, make_segment,
)


@pytest.fixture
def world(db):
    """
    Two organisations. The first has two branches and one org-wide wave.

        Acme            Globex
        ├─ Nairobi      └─ (no branches)
        ├─ Mombasa
        └─ org-wide cycle
    """
    acme = make_org('Acme Ltd')
    globex = make_org('Globex Inc')

    nairobi = make_branch(acme, 'Nairobi')
    mombasa = make_branch(acme, 'Mombasa')

    data = {
        'acme': acme,
        'globex': globex,
        'nairobi': nairobi,
        'mombasa': mombasa,
        'acme_admin': make_org_admin(acme, email='l2@acme.test'),
        'globex_admin': make_org_admin(globex, email='l2@globex.test'),
        'nairobi_admin': make_branch_admin(acme, nairobi, email='l3@nairobi.test'),
        'mombasa_admin': make_branch_admin(acme, mombasa, email='l3@mombasa.test'),
        # One cycle per scope, so every visibility rule has something to hit.
        'acme_orgwide': make_cycle(acme, 'ACME-ORG', cycle_name='Acme org-wide'),
        'acme_nairobi': make_cycle(acme, 'ACME-NBO', branch=nairobi,
                                   cycle_name='Nairobi only'),
        'acme_mombasa': make_cycle(acme, 'ACME-MSA', branch=mombasa,
                                   cycle_name='Mombasa only'),
        'globex_cycle': make_cycle(globex, 'GBX-ORG', cycle_name='Globex org-wide'),
    }
    db.session.commit()
    return data


# ── Tenancy ─────────────────────────────────────────────────────────────────

def test_an_org_admin_sees_only_their_own_organisations_cycles(client, world):
    login(client, world['acme_admin'])
    body = client.get('/org/cycles').get_data(as_text=True)
    assert 'Acme org-wide' in body
    assert 'Nairobi only' in body
    assert 'Mombasa only' in body
    assert 'Globex org-wide' not in body
    assert 'GBX-ORG' not in body


def test_an_org_admin_cannot_open_another_orgs_cycle_by_url(client, world):
    """
    The list hiding it is not enough — the id is guessable and appears in report
    filenames. The detail route re-checks ownership.
    """
    login(client, world['acme_admin'])
    assert client.get('/org/cycles/GBX-ORG').status_code == 404


def test_an_org_admin_cannot_edit_another_orgs_cycle(client, world):
    login(client, world['acme_admin'])
    assert client.get('/org/cycles/GBX-ORG/edit').status_code == 404
    res = client.post('/org/cycles/GBX-ORG/status', data={'status': 'open'})
    assert res.status_code == 404
    # And it really did not change.
    from app import db
    assert db.session.get(MeasurementCycle, 'GBX-ORG').status == 'draft'


def test_switching_identity_actually_switches(client, world):
    """
    Guards the guard. If conftest.login() stopped clearing Flask-Login's cached
    user, every other test in this file would silently run as whoever logged in
    first — and would still pass. This one would not.
    """
    login(client, world['acme_admin'])
    assert 'Acme' in client.get('/org/').get_data(as_text=True)

    login(client, world['globex_admin'])
    body = client.get('/org/').get_data(as_text=True)
    assert 'Globex' in body
    assert 'Acme' not in body


# ── Branch scoping ──────────────────────────────────────────────────────────

def test_a_branch_admin_sees_only_their_own_branchs_cycles(client, world):
    login(client, world['nairobi_admin'])
    body = client.get('/org/cycles').get_data(as_text=True)
    assert 'Nairobi only' in body
    assert 'Mombasa only' not in body


def test_a_branch_admin_never_sees_org_wide_waves(client, world):
    """
    THE RULE THAT IS EASY TO GET BACKWARDS.

    An org-wide cycle has branch_id IS NULL. A naive filter —
    `branch_id == me OR branch_id IS NULL` — reads as "mine plus the shared
    ones" and is wrong: an org-wide wave covers the whole organisation, was run
    deliberately above the branch layer, and its results are not this branch's
    to read.
    """
    login(client, world['nairobi_admin'])
    body = client.get('/org/cycles').get_data(as_text=True)
    assert 'Acme org-wide' not in body
    assert client.get('/org/cycles/ACME-ORG').status_code == 404


def test_a_branch_admin_cannot_reach_a_sibling_branch_by_url(client, world):
    login(client, world['nairobi_admin'])
    assert client.get('/org/cycles/ACME-MSA').status_code == 404


def test_a_branch_admin_with_no_branch_sees_nothing_rather_than_everything(
        client, db, world):
    """
    The safe default. `visible_branch_ids` returns [] — not None — for an L3
    with no branch, and None is the value that means "no filter". Getting this
    backwards shows an unassigned branch admin the entire organisation.
    """
    orphan = make_branch_admin(world['acme'], world['nairobi'],
                               email='orphan@acme.test')
    orphan.branch_id = None
    db.session.commit()

    login(client, orphan)
    body = client.get('/org/cycles').get_data(as_text=True)
    for name in ('Nairobi only', 'Mombasa only', 'Acme org-wide'):
        assert name not in body


# ── The dashboard aggregates, not just the lists ────────────────────────────

def test_dashboard_response_counts_are_branch_scoped(client, db, world):
    """
    A count is a leak too. A branch admin seeing the organisation's total
    response count learns the size of waves they are not entitled to see —
    and on a small org that is enough to infer a sibling branch's participation.
    """
    make_deployment(world['acme'], world['acme_orgwide'],
                    responses_received=400, expected_respondents=400)
    make_deployment(world['acme'], world['acme_nairobi'],
                    branch=world['nairobi'],
                    responses_received=7, expected_respondents=10)
    make_deployment(world['acme'], world['acme_mombasa'],
                    branch=world['mombasa'],
                    responses_received=11, expected_respondents=20)
    db.session.commit()

    login(client, world['nairobi_admin'])
    body = client.get('/org/').get_data(as_text=True)
    assert '>7<' in body.replace(' ', '').replace('\n', '')
    assert '400' not in body

    login(client, world['acme_admin'])
    body = client.get('/org/').get_data(as_text=True)
    assert '418' in body          # 400 + 7 + 11, the whole organisation


# ── Branch management is L2's ───────────────────────────────────────────────

def test_a_branch_admin_cannot_reach_branch_management(client, world):
    login(client, world['nairobi_admin'])
    res = client.get('/org/branches')
    assert res.status_code == 302
    assert '/org/' in res.headers['Location']


def test_a_branch_admin_cannot_create_a_branch_admin(client, world):
    login(client, world['nairobi_admin'])
    res = client.post(f'/org/branches/{world["nairobi"].id}/admins/new',
                      data={'email': 'sneaky@acme.test', 'password': 'password123'})
    assert res.status_code == 302
    from app import db
    from app.models import User
    assert db.session.query(User).filter_by(email='sneaky@acme.test').first() is None


def test_an_org_admin_cannot_add_a_branch_to_another_org(client, db, world):
    globex_branch = make_branch(world['globex'], 'Lagos')
    db.session.commit()

    login(client, world['acme_admin'])
    res = client.post(f'/org/branches/{globex_branch.id}/toggle')
    assert res.status_code == 404
    assert db.session.get(type(globex_branch), globex_branch.id).is_active is True


# ── Segments are org-scoped ─────────────────────────────────────────────────

def test_segments_do_not_cross_organisations(client, db, world):
    make_segment(world['acme'], 'department', 'Department',
                 [('FIN', 'Finance'), ('OPS', 'Operations')])
    make_segment(world['globex'], 'region', 'Region', [('EU', 'Europe')])
    db.session.commit()

    login(client, world['acme_admin'])
    body = client.get('/org/segments').get_data(as_text=True)
    assert 'Department' in body
    assert 'Region' not in body


def test_a_branch_admin_may_view_segments_but_not_change_them(client, db, world):
    """
    L3 needs to know which groups their results will be broken down by. They do
    not get to add to the vocabulary: two branches each adding their own
    spelling of one department produces two groups, neither of which reaches
    the reporting threshold.
    """
    segment = make_segment(world['acme'], 'department', 'Department',
                           [('FIN', 'Finance')])
    db.session.commit()

    login(client, world['nairobi_admin'])
    assert client.get('/org/segments').status_code == 200

    res = client.get(f'/org/segments/{segment.id}')
    assert res.status_code == 302

    res = client.post('/org/segments/new',
                      data={'segment_key': 'shift', 'label': 'Shift'})
    assert res.status_code == 302
    assert db.session.query(Segment).filter_by(
        org_id=world['acme'].id, segment_key='shift').first() is None


# ── The master template org is not a tenant ─────────────────────────────────

def test_the_master_template_org_is_not_reachable_as_a_client(client, db, world):
    """
    It is a real row in `orgs` because the 48 master items need an owner, but it
    has no admin, no cycle and no respondent. Offering its detail page invites
    someone to edit the instrument every client clones from, through a screen
    not built for it.
    """
    from app.domains import MASTER_ORG_ID
    from tests.factories import make_master_admin

    l1 = make_master_admin(email='l1@leadto.test')
    db.session.commit()

    login(client, l1)
    assert client.get(f'/admin/orgs/{MASTER_ORG_ID}').status_code == 404

    body = client.get('/admin/').get_data(as_text=True)
    assert 'Master Template' not in body
