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
from app import domains
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


def test_the_four_step_wizard_runs_end_to_end(client, db, l1):
    """
    Details & scope -> Review template -> Administrator -> Confirm.

    Each step is a real URL against a draft that already exists in the
    database, so closing the tab loses nothing.
    """
    login(client, l1)

    # 1. Details & scope — creating the draft lands on step 2.
    res = client.post('/admin/orgs/new',
                      data={'name': 'Wizard Ltd', 'industry': 'Logistics',
                            'scope_submitted': '1',
                            'domains': ['TID', 'BIT', 'PIE']})
    org = db.session.query(Org).filter_by(name='Wizard Ltd').one()
    assert res.status_code == 302
    assert f'/admin/orgs/{org.id}/setup/instrument' in res.headers['Location']
    assert org.setup_complete is False

    # 2. Review template — the org's own 48, read-only.
    body = client.get(f'/admin/orgs/{org.id}/setup/instrument').get_data(as_text=True)
    assert 'Review template' in body
    assert 'Overall, I am satisfied with my current job.' in body

    # 4 before 3: confirm refuses to finish without an administrator.
    body = client.get(f'/admin/orgs/{org.id}/setup/confirm').get_data(as_text=True)
    assert 'none yet' in body
    client.post(f'/admin/orgs/{org.id}/complete', follow_redirects=True)
    db.session.refresh(org)
    assert org.setup_complete is False

    # 3. Administrator — creating one returns to Confirm.
    res = client.post(f'/admin/orgs/{org.id}/admins/new',
                      data={'email': 'l2@wizard.test', 'password': 'a-long-password',
                            'full_name': 'Wizard Admin'})
    assert f'/admin/orgs/{org.id}/setup/confirm' in res.headers['Location']

    # 4. Confirm — now it completes.
    client.post(f'/admin/orgs/{org.id}/complete', follow_redirects=True)
    db.session.refresh(org)
    assert org.setup_complete is True


def test_the_outcome_domain_cannot_be_dropped_from_an_org(client, db, l1):
    """§10.4: the diagnostic domains are reported in relation to GEM."""
    login(client, l1)
    client.post('/admin/orgs/new',
                data={'name': 'Diagnostic Ltd', 'scope_submitted': '1',
                      'domains': ['TID']})
    org = db.session.query(Org).filter_by(name='Diagnostic Ltd').one()
    assert domains.OUTCOME_DOMAIN in org.active_domains

    # And GEM alone is refused: nothing to relate the outcome to.
    body = client.post('/admin/orgs/new',
                       data={'name': 'Outcome Only Ltd', 'scope_submitted': '1'},
                       follow_redirects=True).get_data(as_text=True)
    assert 'at least one diagnostic domain' in body
    assert db.session.query(Org).filter_by(name='Outcome Only Ltd').count() == 0


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


def test_creating_an_org_seeds_the_instrument_and_nothing_else(
        client, db, l1):
    """
    A new org used to be seeded with the suggested segments (department,
    function, location, tenure band), inactive until the client filled in
    their options. Segments were removed, so provisioning now clones the
    instrument and stops.
    """
    from app.models import Segment, SurveyItem

    login(client, l1)
    client.post('/admin/orgs/new', data={'name': 'Fresh Ltd',
                                         'industry': 'Manufacturing'},
                follow_redirects=True)

    org = db.session.query(Org).filter_by(name='Fresh Ltd').one()
    assert db.session.query(SurveyItem).filter_by(org_id=org.id).count() == 48
    assert db.session.query(Segment).filter_by(org_id=org.id).count() == 0


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
    assert 'master template is empty' in res.get_data(as_text=True)
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
