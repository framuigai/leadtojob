"""
Fixture builders. Imported by the DB-backed tests.

Deliberately NOT pytest fixtures: a test that builds two orgs and four users
needs to name them, and a web of interdependent fixtures makes the arrangement
harder to read than the three lines it replaces.

bcrypt at the production cost factor (rounds=12) dominates the runtime of any
test that creates a user — about 0.3s each, which is most of a suite's wall
clock once there are a few hundred. make_user patches it down; TestingConfig
records the intent as BCRYPT_ROUNDS.
"""
import uuid
from datetime import datetime, timezone

import bcrypt

from app import db
from app.domains import DOMAINS
from app.models import (
    Branch, MeasurementCycle, Org, Segment, SegmentOption, SurveyDeployment,
    SurveyItem, User,
)
from app.permissions import BRANCH_ADMIN, MASTER_ADMIN, ORG_ADMIN

# A real bcrypt hash of 'password', computed once at 4 rounds. Reused rather
# than recomputed per user.
_CHEAP_HASH = bcrypt.hashpw(b'password', bcrypt.gensalt(rounds=4)).decode()

PASSWORD = 'password'


# The org's standing §9.2 statement. A new cycle is created with a copy of it,
# so an org without one cannot create a cycle at all — which is the gate, and
# is tested on its own. Every other test wants an org past that gate, so the
# factory supplies one. Pass default_purpose_statement=None to get an org that
# still owes it.
DEFAULT_PURPOSE = (
    'We are asking how satisfied you are with your job so that we can decide '
    'what to improve. Results are reported in groups only, and answering '
    'honestly carries no consequence.'
)


def make_org(name='Acme Ltd', **kw):
    org = Org(name=name, industry=kw.pop('industry', 'Manufacturing'),
              active_domains=kw.pop('active_domains', list(DOMAINS)),
              default_purpose_statement=kw.pop('default_purpose_statement',
                                               DEFAULT_PURPOSE),
              setup_complete=kw.pop('setup_complete', True),
              is_active=kw.pop('is_active', True), **kw)
    db.session.add(org)
    db.session.flush()
    return org


def make_branch(org, name='Nairobi', **kw):
    branch = Branch(org_id=org.id, name=name,
                    is_active=kw.pop('is_active', True), **kw)
    db.session.add(branch)
    db.session.flush()
    return branch


def make_user(role, org=None, branch=None, email=None, **kw):
    user = User(
        email=email or f'{role}-{uuid.uuid4().hex[:8]}@example.com',
        role=role,
        org_id=org.id if org is not None else None,
        branch_id=branch.id if branch is not None else None,
        is_active=kw.pop('is_active', True),
        full_name=kw.pop('full_name', None),
    )
    user.password_hash = _CHEAP_HASH
    db.session.add(user)
    db.session.flush()
    return user


def make_master_admin(**kw):
    return make_user(MASTER_ADMIN, **kw)


def make_org_admin(org, **kw):
    return make_user(ORG_ADMIN, org=org, **kw)


def make_branch_admin(org, branch, **kw):
    return make_user(BRANCH_ADMIN, org=org, branch=branch, **kw)


def make_cycle(org, cycle_id=None, branch=None, **kw):
    cycle = MeasurementCycle(
        id=cycle_id or f'JS-{uuid.uuid4().hex[:8]}',
        org_id=org.id,
        branch_id=branch.id if branch is not None else None,
        cycle_name=kw.pop('cycle_name', 'Employee Satisfaction 2026 H1'),
        purpose_statement=kw.pop(
            'purpose_statement',
            'We are asking how satisfied you are with your job so that we can '
            'decide what to improve. Results are reported in groups of five or '
            'more only, and answering honestly carries no consequence.'),
        assessed_domains=kw.pop('assessed_domains', list(DOMAINS)),
        status=kw.pop('status', 'draft'),
        min_reporting_n=kw.pop('min_reporting_n', 5),
        scoring_method=kw.pop('scoring_method', 'mean_of_subdomains'),
        retention_months=kw.pop('retention_months', 24),
        is_pilot=kw.pop('is_pilot', False),
        **kw,
    )
    db.session.add(cycle)
    db.session.flush()
    return cycle


def make_deployment(org, cycle, branch=None, **kw):
    deployment = SurveyDeployment(
        org_id=org.id,
        branch_id=branch.id if branch is not None else None,
        cycle_id=cycle.id,
        assessed_domains=list(cycle.assessed_domains or DOMAINS),
        public_slug=kw.pop('public_slug', uuid.uuid4().hex),
        delivery_mode=kw.pop('delivery_mode', 'open_link'),
        expected_respondents=kw.pop('expected_respondents', 50),
        responses_received=kw.pop('responses_received', 0),
        deployed_at=kw.pop('deployed_at', datetime.now(timezone.utc)),
        closed=kw.pop('closed', False),
        **kw,
    )
    db.session.add(deployment)
    db.session.flush()
    return deployment


def make_segment(org, key='department', label='Department', options=(), **kw):
    segment = Segment(org_id=org.id, segment_key=key, label=label,
                      collect_at_survey=kw.pop('collect_at_survey', True),
                      is_active=kw.pop('is_active', True),
                      sort_order=kw.pop('sort_order', 1), **kw)
    db.session.add(segment)
    db.session.flush()
    for i, (code, option_label) in enumerate(options, start=1):
        db.session.add(SegmentOption(segment_id=segment.id, option_code=code,
                                     option_label=option_label, sort_order=i,
                                     is_active=True))
    db.session.flush()
    return segment


def make_master_instrument():
    """
    The 48 master items, read from the real CSVs.

    Reads the committed files rather than generating placeholder text, AND
    applies the same _interleaved() ordering seed_templates.py does.

    That second half was missing at first, and it mattered: the fixture seeded
    sort_order in file order, which is blocked by subdomain, while production
    seeds it interleaved. Every DB-backed test was therefore exercising an
    ordering the product never ships — and the anti-straight-lining test
    passed only because it called _interleaved() directly rather than reading
    what had been seeded.
    """
    from app.domains import MASTER_ORG_ID
    from seed_templates import _interleaved, read_all_items

    master = db.session.get(Org, MASTER_ORG_ID)
    if master is None:
        master = Org(id=MASTER_ORG_ID, name='leadTo Master Template',
                     industry='Template', active_domains=list(DOMAINS),
                     setup_complete=True)
        db.session.add(master)
        db.session.flush()

    rows = _interleaved(read_all_items())
    for sort_order, row in enumerate(rows, start=1):
        db.session.add(SurveyItem(org_id=MASTER_ORG_ID, version=1,
                                  sort_order=sort_order, anchor_type='agreement',
                                  allows_na=True, is_reverse_scored=False, **row))
    db.session.flush()
    return master
