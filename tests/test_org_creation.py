"""
The L1 org wizard, user creation and the audit trail.

The thread running through these: an organisation must never exist in a state
that LOOKS provisioned and is not. The two ways that happens are an org with no
instrument (its survey link renders a form with no questions, discovered only
when a respondent opens it) and an org with no administrator (nobody at the
client can sign in, discovered when they try).
"""
import pytest

from app import db
from app.domains import DOMAINS, MASTER_ORG_ID
from app.models import AuditEvent, Org, Segment, SurveyItem, User
from app.permissions import BRANCH_ADMIN, ORG_ADMIN
from tests.conftest import login
from tests.factories import (
    make_branch, make_branch_admin, make_master_admin, make_master_instrument,
    make_org, make_org_admin,
)


@pytest.fixture
def l1(db):
    make_master_instrument()
    admin = make_master_admin(email='l1@leadto.test')
    db.session.commit()
    return admin


# ── Creating an organisation ───────────────────────────────────────────────

def test_creating_an_org_clones_the_instrument_in_the_same_transaction(
        client, db, l1):
    """
    An org without its 48 items is an org whose survey link renders a form with
    no questions — and that failure surfaces only when a respondent opens it,
    by which point the invitation has gone out to the whole workforce.
    """
    login(client, l1)
    client.post('/admin/orgs/new', data={'name': 'Acme Ltd',
                                         'industry': 'Manufacturing'},
                follow_redirects=True)

    org = db.session.query(Org).filter_by(name='Acme Ltd').one()
    assert db.session.query(SurveyItem).filter_by(org_id=org.id).count() == 48
    assert sorted(org.active_domains) == sorted(DOMAINS)


def test_a_new_org_starts_as_a_resumable_draft(client, db, l1):
    """
    setup_complete=False from step one, rather than the wizard holding its
    state in the session. An admin who closes the tab after naming the
    organisation loses nothing, and the board offers to resume instead of
    leaving a half-built tenant that looks finished.
    """
    login(client, l1)
    client.post('/admin/orgs/new', data={'name': 'Acme Ltd'},
                follow_redirects=True)
    assert db.session.query(Org).filter_by(name='Acme Ltd').one() \
        .setup_complete is False


def test_creating_an_org_seeds_the_suggested_segments_with_no_options(
        client, db, l1):
    """
    Deliberately empty of options. Only the client knows what their departments
    are called, and a helpfully pre-filled list of plausible names would be
    wrong for every client and accepted by at least one of them.

    Inactive too: an active segment with no options is an empty dropdown on the
    survey form.
    """
    login(client, l1)
    client.post('/admin/orgs/new', data={'name': 'Acme Ltd'},
                follow_redirects=True)
    org = db.session.query(Org).filter_by(name='Acme Ltd').one()

    segments = db.session.query(Segment).filter_by(org_id=org.id).all()
    assert {s.segment_key for s in segments} == {
        'department', 'function', 'location', 'tenure_band'}
    assert all(not s.is_active for s in segments)
    assert all(s.options == [] for s in segments)


def test_an_org_with_no_instrument_available_is_not_created_at_all(client, db):
    """
    The clone and the org row land in ONE transaction. A failure must leave no
    org behind — a tenant on the board with no questions is worse than a
    refusal, because nothing about it looks wrong.
    """
    admin = make_master_admin(email='l1@leadto.test')
    db.session.commit()                      # NOTE: no master instrument

    login(client, admin)
    res = client.post('/admin/orgs/new', data={'name': 'Acme Ltd'},
                      follow_redirects=True)
    assert 'master instrument is empty' in res.get_data(as_text=True)
    assert db.session.query(Org).filter_by(name='Acme Ltd').first() is None


def test_an_org_needs_a_name(client, db, l1):
    login(client, l1)
    res = client.post('/admin/orgs/new', data={'name': '   '},
                      follow_redirects=True)
    assert 'name is required' in res.get_data(as_text=True)
    assert db.session.query(Org).count() == 1          # the master only


# ── Completing setup ───────────────────────────────────────────────────────

def test_setup_cannot_be_completed_without_an_administrator(client, db, l1):
    org = make_org('Acme Ltd', setup_complete=False)
    db.session.commit()

    login(client, l1)
    res = client.post(f'/admin/orgs/{org.id}/complete', follow_redirects=True)
    assert 'before completing setup' in res.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(Org, org.id).setup_complete is False


def test_setup_completes_once_an_admin_exists(client, db, l1):
    org = make_org('Acme Ltd', setup_complete=False)
    make_org_admin(org, email='l2@acme.test')
    db.session.commit()

    login(client, l1)
    client.post(f'/admin/orgs/{org.id}/complete', follow_redirects=True)
    db.session.expire_all()
    assert db.session.get(Org, org.id).setup_complete is True


def test_a_branch_admin_does_not_satisfy_the_requirement(client, db, l1):
    """
    An L3 can only reach their own branch. An org whose only account is a
    branch admin has nobody who can create a cycle or define segments.
    """
    org = make_org('Acme Ltd', setup_complete=False)
    branch = make_branch(org, 'Nairobi')
    make_branch_admin(org, branch, email='l3@acme.test')
    db.session.commit()

    login(client, l1)
    res = client.post(f'/admin/orgs/{org.id}/complete', follow_redirects=True)
    assert 'before completing setup' in res.get_data(as_text=True)


# ── Creating administrators ────────────────────────────────────────────────

def test_l1_creates_an_org_admin(client, db, l1):
    org = make_org('Acme Ltd')
    db.session.commit()

    login(client, l1)
    client.post(f'/admin/orgs/{org.id}/admins/new',
                data={'email': 'L2@Acme.Test', 'password': 'password123',
                      'full_name': 'Asha N'},
                follow_redirects=True)

    user = db.session.query(User).filter_by(email='l2@acme.test').one()
    assert user.role == ORG_ADMIN
    assert user.org_id == org.id
    assert user.branch_id is None
    assert user.check_password('password123')


def test_an_email_is_unique_across_the_whole_platform(client, db, l1):
    """
    Not per organisation. One address holding two accounts would make the role
    a person gets depend on which row the login query happened to find first,
    and the query has no ORDER BY.
    """
    acme = make_org('Acme Ltd')
    globex = make_org('Globex Inc')
    make_org_admin(acme, email='shared@example.com')
    db.session.commit()

    login(client, l1)
    res = client.post(f'/admin/orgs/{globex.id}/admins/new',
                      data={'email': 'shared@example.com',
                            'password': 'password123'},
                      follow_redirects=True)
    assert 'already has an account' in res.get_data(as_text=True)
    assert db.session.query(User).filter_by(email='shared@example.com').count() == 1


def test_a_short_password_is_refused(client, db, l1):
    org = make_org('Acme Ltd')
    db.session.commit()

    login(client, l1)
    res = client.post(f'/admin/orgs/{org.id}/admins/new',
                      data={'email': 'l2@acme.test', 'password': 'short'},
                      follow_redirects=True)
    assert 'at least 8 characters' in res.get_data(as_text=True)
    assert db.session.query(User).filter_by(email='l2@acme.test').first() is None


def test_an_org_admin_creates_a_branch_admin_for_their_own_branch(client, db):
    org = make_org('Acme Ltd')
    branch = make_branch(org, 'Nairobi')
    admin = make_org_admin(org, email='l2@acme.test')
    db.session.commit()

    login(client, admin)
    client.post(f'/org/branches/{branch.id}/admins/new',
                data={'email': 'l3@acme.test', 'password': 'password123'},
                follow_redirects=True)

    user = db.session.query(User).filter_by(email='l3@acme.test').one()
    assert user.role == BRANCH_ADMIN
    assert user.branch_id == branch.id
    assert user.org_id == org.id


# ── Deactivation ───────────────────────────────────────────────────────────

def test_deactivating_an_account_ends_its_session_on_the_next_request(
        client, db, l1):
    """
    The user_loader returns None for an inactive account, so the session ends
    at the next request rather than running until they next sign in. On this
    product that matters: the account being removed may be one that can read a
    whole workforce's feedback.
    """
    org = make_org('Acme Ltd')
    admin = make_org_admin(org, email='l2@acme.test')
    db.session.commit()

    login(client, admin)
    assert client.get('/org/').status_code == 200

    admin.is_active = False
    db.session.commit()

    res = client.get('/org/')
    assert res.status_code == 302
    assert '/login' in res.headers['Location']


def test_l1_cannot_deactivate_itself(client, db, l1):
    """
    Not a permission question — a usability one. An L1 who deactivates
    themselves is locked out of the only screen that could undo it.
    """
    login(client, l1)
    res = client.post(f'/admin/users/{l1.id}/toggle', follow_redirects=True)
    assert 'cannot deactivate your own account' in res.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(User, l1.id).is_active is True


# ── Audit ──────────────────────────────────────────────────────────────────

def test_org_creation_and_user_creation_are_audited(client, db, l1):
    login(client, l1)
    client.post('/admin/orgs/new', data={'name': 'Acme Ltd'},
                follow_redirects=True)
    org = db.session.query(Org).filter_by(name='Acme Ltd').one()
    client.post(f'/admin/orgs/{org.id}/admins/new',
                data={'email': 'l2@acme.test', 'password': 'password123'},
                follow_redirects=True)

    actions = [e.action for e in db.session.query(AuditEvent).all()]
    assert 'org.create' in actions
    assert 'user.create' in actions

    event = db.session.query(AuditEvent).filter_by(action='user.create').one()
    # Denormalised, so "who did this" survives the actor being deactivated or
    # reassigned later.
    assert event.actor_email == 'l1@leadto.test'
    assert event.actor_role == 'master_admin'


def test_an_audit_payload_never_carries_a_password_or_a_hash(client, db, l1):
    """
    `payload` is the one long-lived, rarely-reviewed place a secret could be
    smuggled into. user_payload() carries the email, role, branch and active
    flag — deliberately not the hash.
    """
    org = make_org('Acme Ltd')
    db.session.commit()

    login(client, l1)
    client.post(f'/admin/orgs/{org.id}/admins/new',
                data={'email': 'l2@acme.test', 'password': 'password123'},
                follow_redirects=True)

    for event in db.session.query(AuditEvent).all():
        blob = repr(event.payload or {})
        assert 'password123' not in blob
        assert 'password_hash' not in blob
        assert '$2b$' not in blob            # a bcrypt hash prefix


def test_the_audit_screen_renders_and_filters(client, db, l1):
    login(client, l1)
    client.post('/admin/orgs/new', data={'name': 'Acme Ltd'},
                follow_redirects=True)

    body = client.get('/admin/audit').get_data(as_text=True)
    assert 'org.create' in body
    assert 'l1@leadto.test' in body

    body = client.get('/admin/audit?action=user.create').get_data(as_text=True)
    assert 'Created organisation Acme Ltd' not in body


def test_the_users_screen_carries_a_filter_bar_bound_to_the_table(client, db, l1):
    """Filtering is the shared component over rows the route already sent."""
    login(client, l1)
    body = client.get('/admin/users').get_data(as_text=True)

    assert 'data-filter-for="user-list"' in body
    assert 'id="user-list"' in body
    # L1's own row offers no toggle button; it reads "You" instead.
    assert '>You<' in body
    assert 'user_toggle' not in body


def test_the_master_org_is_absent_from_the_users_screen_as_a_tenant(
        client, db, l1):
    login(client, l1)
    body = client.get('/admin/users').get_data(as_text=True)
    # L1 itself has no org; the row says so rather than naming a fixture org.
    assert 'platform' in body
    assert str(MASTER_ORG_ID) not in body
