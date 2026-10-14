"""
PHASE 4'S ACCEPTANCE TEST.

    two open cycles for one org+branch rejected by the DATABASE;
    segments frozen after the first response;
    assessed_domains frozen likewise.

"Rejected by the database" is the part that matters. Two administrators
pressing Deploy at the same moment is a race the route check cannot win: both
read, both see nothing open, both write. Only a unique index decides.
"""
import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.domains import DOMAINS, GEM, OUTCOME_DOMAIN
from app.models import MeasurementCycle, Segment, SegmentOption, SurveyDeployment
from app.provisioning import freeze_segments, validate_segment_values
from tests.conftest import login
from tests.factories import (
    make_branch, make_cycle, make_deployment, make_org, make_org_admin,
    make_segment,
)


@pytest.fixture
def org_world(db):
    org = make_org('Acme Ltd')
    branch = make_branch(org, 'Nairobi')
    admin = make_org_admin(org, email='l2@acme.test')
    db.session.commit()
    return {'org': org, 'branch': branch, 'admin': admin}


# ── The database, not the route, blocks a duplicate open wave ───────────────

def test_two_open_org_wide_deployments_for_one_cycle_are_rejected_by_the_db(
        db, org_world):
    """
    THE NAMED ACCEPTANCE CRITERION, and the subtle half of it.

    `uq_open_deployment` wraps branch_id in COALESCE precisely for this case.
    Postgres treats NULLs as DISTINCT in a unique index, so a bare nullable
    `branch_id` column would index these two rows as different and quietly let
    both through — the constraint would look present and do nothing for the
    org-wide case, which is the default case.
    """
    cycle = make_cycle(org_world['org'], 'ACME-H1')
    make_deployment(org_world['org'], cycle)          # branch_id IS NULL
    db.session.commit()

    with pytest.raises(IntegrityError):
        make_deployment(org_world['org'], cycle)      # branch_id IS NULL again
        db.session.commit()
    db.session.rollback()


def test_two_open_deployments_for_one_branch_are_rejected(db, org_world):
    cycle = make_cycle(org_world['org'], 'ACME-H1', branch=org_world['branch'])
    make_deployment(org_world['org'], cycle, branch=org_world['branch'])
    db.session.commit()

    with pytest.raises(IntegrityError):
        make_deployment(org_world['org'], cycle, branch=org_world['branch'])
        db.session.commit()
    db.session.rollback()


def test_an_org_wide_and_a_branch_deployment_may_coexist(db, org_world):
    """
    The complement. Without it, a constraint that blocked everything would
    leave the two tests above green while making the product unusable.
    """
    cycle = make_cycle(org_world['org'], 'ACME-H1')
    make_deployment(org_world['org'], cycle)
    make_deployment(org_world['org'], cycle, branch=org_world['branch'])
    db.session.commit()
    assert db.session.query(SurveyDeployment).count() == 2


def test_a_closed_deployment_frees_the_slot_for_a_redeploy(db, org_world):
    """
    The index is PARTIAL — `WHERE closed = false`. A wave that has been closed
    no longer blocks a new one, which is what makes a mis-deployed wave
    recoverable.
    """
    cycle = make_cycle(org_world['org'], 'ACME-H1')
    first = make_deployment(org_world['org'], cycle)
    db.session.commit()

    first.closed = True
    db.session.commit()

    make_deployment(org_world['org'], cycle)
    db.session.commit()
    assert db.session.query(SurveyDeployment).count() == 2


# ── Retention and status bounds are in the database too ────────────────────

def test_retention_outside_three_to_120_months_is_rejected(db, org_world):
    """
    §13.8 requires appropriate retention and deletion rules. A nullable or
    unbounded column means "indefinitely" in practice — and an unbounded
    integer is how "indefinitely" gets typed as 9999.
    """
    with pytest.raises(IntegrityError):
        make_cycle(org_world['org'], 'ACME-BAD', retention_months=9999)
        db.session.commit()
    db.session.rollback()

    with pytest.raises(IntegrityError):
        make_cycle(org_world['org'], 'ACME-BAD2', retention_months=1)
        db.session.commit()
    db.session.rollback()


def test_an_unknown_scoring_method_is_rejected_by_the_database(db, org_world):
    with pytest.raises(IntegrityError):
        make_cycle(org_world['org'], 'ACME-BAD3', scoring_method='median_ish')
        db.session.commit()
    db.session.rollback()


# ── Creating a cycle through the screen ────────────────────────────────────

_GOOD_PURPOSE = (
    'We are asking how satisfied you are with your job so that we can decide '
    'what to improve. Results are only reported in groups of five or more, and '
    'answering honestly carries no consequence.'
)


def _cycle_form(**over):
    data = {
        'id': 'JS-2026-H1',
        'cycle_name': 'Employee Satisfaction 2026 H1',
        'purpose_statement': _GOOD_PURPOSE,
        'assessed_domains': list(DOMAINS),
        'min_reporting_n': '5',
        'scoring_method': 'mean_of_subdomains',
        'retention_months': '24',
        'previous_cycle_id': '',
    }
    data.update(over)
    return data


def test_an_org_admin_can_create_a_cycle(client, db, org_world):
    login(client, org_world['admin'])
    res = client.post('/org/cycles/new', data=_cycle_form(), follow_redirects=True)
    assert res.status_code == 200

    cycle = db.session.get(MeasurementCycle, 'JS-2026-H1')
    assert cycle is not None
    assert cycle.status == 'draft'
    assert cycle.org_id == org_world['org'].id
    assert cycle.branch_id is None          # an L2 cycle is org-wide
    assert sorted(cycle.assessed_domains) == sorted(DOMAINS)


def test_a_purpose_statement_is_required_and_must_say_something(client, db, org_world):
    """
    NOT NULL in the schema, with a length floor at the point of entry.

    §9.2 requires respondents be told the purpose, how results will be used and
    that there will be no retaliation, BEFORE the first question. This text is
    what the landing page renders. A wave that opens with "Survey" in that box
    is the wave whose answers are not worth having, and no later screen can fix
    it.
    """
    login(client, org_world['admin'])
    res = client.post('/org/cycles/new',
                      data=_cycle_form(purpose_statement='Please fill in.'),
                      follow_redirects=True)
    assert 'at least 40 characters' in res.get_data(as_text=True)
    assert db.session.get(MeasurementCycle, 'JS-2026-H1') is None


def test_a_wave_without_the_outcome_domain_is_refused(client, db, org_world):
    """
    §1 and §10.4: the diagnostic domains are reported IN RELATION TO the
    outcome domain. Without it there is nothing to relate them to, and the
    product degenerates into three unanchored scores that invite exactly the
    "generic satisfaction score" reading the specification exists to replace.

    A shorter pulse wave is legitimate — GEM plus one diagnostic — which is why
    the rule is "the outcome domain must be present", not "all four".
    """
    login(client, org_world['admin'])
    res = client.post('/org/cycles/new',
                      data=_cycle_form(assessed_domains=['TID', 'BIT']),
                      follow_redirects=True)
    assert 'outcome measure' in res.get_data(as_text=True)
    assert db.session.get(MeasurementCycle, 'JS-2026-H1') is None


def test_a_pulse_wave_of_the_outcome_plus_one_diagnostic_is_allowed(
        client, db, org_world):
    login(client, org_world['admin'])
    client.post('/org/cycles/new',
                data=_cycle_form(assessed_domains=[GEM, 'TID']),
                follow_redirects=True)
    cycle = db.session.get(MeasurementCycle, 'JS-2026-H1')
    assert cycle is not None
    assert sorted(cycle.assessed_domains) == sorted([GEM, 'TID'])


def test_the_reporting_threshold_cannot_be_lowered_through_the_form(
        client, db, org_world):
    """
    The platform floor. permissions.LOWER_MIN_N exists and is held by no role
    precisely so that this cannot be waived from a screen under client
    pressure — and this test is what stops someone "fixing" the refusal by
    lowering the config default instead.
    """
    login(client, org_world['admin'])
    res = client.post('/org/cycles/new',
                      data=_cycle_form(min_reporting_n='2'),
                      follow_redirects=True)
    assert 'cannot go below' in res.get_data(as_text=True)
    assert db.session.get(MeasurementCycle, 'JS-2026-H1') is None


def test_the_reporting_threshold_may_be_raised(client, db, org_world):
    login(client, org_world['admin'])
    client.post('/org/cycles/new', data=_cycle_form(min_reporting_n='10'),
                follow_redirects=True)
    assert db.session.get(MeasurementCycle, 'JS-2026-H1').min_reporting_n == 10


def test_a_duplicate_cycle_reference_is_refused(client, db, org_world):
    make_cycle(org_world['org'], 'JS-2026-H1')
    db.session.commit()

    login(client, org_world['admin'])
    res = client.post('/org/cycles/new', data=_cycle_form(), follow_redirects=True)
    assert 'already in use' in res.get_data(as_text=True)


def test_a_cycle_reference_unsafe_for_a_filename_is_refused(client, org_world):
    login(client, org_world['admin'])
    res = client.post('/org/cycles/new', data=_cycle_form(id='../../etc/passwd'),
                      follow_redirects=True)
    assert 'must be 3-50 characters' in res.get_data(as_text=True)


# ── Freezing, once responses exist ─────────────────────────────────────────

def test_assessed_domains_freeze_once_a_response_exists(client, db, org_world):
    """
    THE NAMED ACCEPTANCE CRITERION.

    Narrowing the domains mid-wave makes the two halves of one wave
    incomparable, and the trend section would rather refuse than plot a missing
    domain as a drop. Nothing in the data reveals it happened afterwards, which
    is why it is blocked rather than warned about.
    """
    cycle = make_cycle(org_world['org'], 'JS-2026-H1', status='open')
    make_deployment(org_world['org'], cycle, responses_received=12)
    db.session.commit()

    login(client, org_world['admin'])
    client.post('/org/cycles/JS-2026-H1/edit',
                data=_cycle_form(assessed_domains=[GEM],
                                 cycle_name='Renamed'),
                follow_redirects=True)

    db.session.expire_all()
    cycle = db.session.get(MeasurementCycle, 'JS-2026-H1')
    assert sorted(cycle.assessed_domains) == sorted(DOMAINS)   # unchanged
    assert cycle.cycle_name == 'Renamed'                       # the rest saved


def test_the_scoring_method_freezes_once_a_response_exists(client, db, org_world):
    """
    §10.2 does not mandate a method; it mandates documenting which one is used.
    Switching it mid-wave silently changes every figure already reported from
    that wave — including figures somebody has acted on.
    """
    cycle = make_cycle(org_world['org'], 'JS-2026-H1', status='open')
    make_deployment(org_world['org'], cycle, responses_received=12)
    db.session.commit()

    login(client, org_world['admin'])
    client.post('/org/cycles/JS-2026-H1/edit',
                data=_cycle_form(scoring_method='mean_of_items'),
                follow_redirects=True)

    db.session.expire_all()
    assert db.session.get(
        MeasurementCycle, 'JS-2026-H1').scoring_method == 'mean_of_subdomains'


def test_domains_remain_editable_before_any_response(client, db, org_world):
    """The complement — a draft wave must still be adjustable."""
    make_cycle(org_world['org'], 'JS-2026-H1', status='draft')
    db.session.commit()

    login(client, org_world['admin'])
    client.post('/org/cycles/JS-2026-H1/edit',
                data=_cycle_form(assessed_domains=[GEM, 'PIE']),
                follow_redirects=True)

    db.session.expire_all()
    assert sorted(db.session.get(
        MeasurementCycle, 'JS-2026-H1').assessed_domains) == sorted([GEM, 'PIE'])


# ── Status transitions ─────────────────────────────────────────────────────

def test_a_cycle_moves_draft_to_open_to_closed_and_no_further(client, db, org_world):
    """
    Reopening a closed wave is deliberately absent. The retention clock starts
    at close, its figures have been reported, and letting new responses arrive
    afterwards changes a number somebody has already acted on. A new wave
    linked by previous_cycle_id is the supported path — and it is what makes
    the result a trend rather than a revision.
    """
    make_cycle(org_world['org'], 'JS-2026-H1', status='draft')
    db.session.commit()
    login(client, org_world['admin'])

    client.post('/org/cycles/JS-2026-H1/status', data={'status': 'open'})
    db.session.expire_all()
    cycle = db.session.get(MeasurementCycle, 'JS-2026-H1')
    assert cycle.status == 'open' and cycle.opened_at is not None

    client.post('/org/cycles/JS-2026-H1/status', data={'status': 'closed'})
    db.session.expire_all()
    cycle = db.session.get(MeasurementCycle, 'JS-2026-H1')
    assert cycle.status == 'closed' and cycle.closed_at is not None

    res = client.post('/org/cycles/JS-2026-H1/status', data={'status': 'open'},
                      follow_redirects=True)
    assert 'cannot move to' in res.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(MeasurementCycle, 'JS-2026-H1').status == 'closed'


def test_a_wave_cannot_open_while_an_active_segment_has_no_options(
        client, db, org_world):
    """
    An active segment with no options renders an empty dropdown on the survey
    form — and because segments freeze on the first response, it cannot be
    repaired once the wave is running.
    """
    make_segment(org_world['org'], 'department', 'Department', options=())
    make_cycle(org_world['org'], 'JS-2026-H1', status='draft')
    db.session.commit()

    login(client, org_world['admin'])
    res = client.post('/org/cycles/JS-2026-H1/status', data={'status': 'open'},
                      follow_redirects=True)
    assert 'at least one reporting segment' in res.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(MeasurementCycle, 'JS-2026-H1').status == 'draft'


# ── Segments ───────────────────────────────────────────────────────────────

def test_a_segment_key_is_unique_per_org(db, org_world):
    make_segment(org_world['org'], 'department', 'Department')
    db.session.commit()
    with pytest.raises(IntegrityError):
        make_segment(org_world['org'], 'department', 'Dept (again)')
        db.session.commit()
    db.session.rollback()


def test_an_option_code_is_unique_within_a_segment(db, org_world):
    segment = make_segment(org_world['org'], 'department', 'Department',
                           [('FIN', 'Finance')])
    db.session.commit()
    db.session.add(SegmentOption(segment_id=segment.id, option_code='FIN',
                                 option_label='Finance Dept', sort_order=2))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_freezing_stamps_only_active_segments(db, org_world):
    active = make_segment(org_world['org'], 'department', 'Department',
                          [('FIN', 'Finance')])
    dormant = make_segment(org_world['org'], 'location', 'Location',
                           [('HQ', 'Head office')], is_active=False)
    db.session.commit()

    frozen = freeze_segments(org_world['org'].id)
    db.session.commit()

    assert [s.id for s in frozen] == [active.id]
    assert active.frozen_at is not None
    assert dormant.frozen_at is None


def test_a_frozen_segment_rejects_a_new_option_through_the_screen(
        client, db, org_world):
    """
    THE NAMED ACCEPTANCE CRITERION.

    After the freeze, renaming or adding an option silently re-groups answers
    given under the old meaning — and the response rows themselves are
    unchanged, so nothing in the data would ever reveal it.
    """
    segment = make_segment(org_world['org'], 'department', 'Department',
                           [('FIN', 'Finance')])
    db.session.commit()
    freeze_segments(org_world['org'].id)
    db.session.commit()

    login(client, org_world['admin'])
    res = client.post(f'/org/segments/{segment.id}',
                      data={'action': 'add_option', 'option_code': 'OPS',
                            'option_label': 'Operations'},
                      follow_redirects=True)
    assert 'frozen' in res.get_data(as_text=True).lower()
    assert db.session.query(SegmentOption).filter_by(
        segment_id=segment.id, option_code='OPS').first() is None


def test_an_unfrozen_segment_accepts_options(client, db, org_world):
    segment = make_segment(org_world['org'], 'department', 'Department')
    db.session.commit()

    login(client, org_world['admin'])
    client.post(f'/org/segments/{segment.id}',
                data={'action': 'add_option', 'option_code': 'ops',
                      'option_label': 'Operations'},
                follow_redirects=True)
    option = db.session.query(SegmentOption).filter_by(
        segment_id=segment.id).one()
    # Codes are upper-cased on entry: 'ops' and 'OPS' typed by two admins must
    # not become two groups, neither of which reaches the threshold.
    assert option.option_code == 'OPS'


# ── Self-declared segment values ───────────────────────────────────────────

def test_a_valid_declaration_is_kept(db, org_world):
    make_segment(org_world['org'], 'department', 'Department',
                 [('FIN', 'Finance'), ('OPS', 'Operations')])
    db.session.commit()
    assert validate_segment_values(
        org_world['org'].id, {'department': 'FIN'}) == {'department': 'FIN'}


def test_an_unknown_code_is_dropped_not_rejected(db, org_world):
    """
    A respondent whose department was renamed between the wave opening and
    their answering must not LOSE THEIR 48 ANSWERS over it. The segment is
    simply absent for them and they fall into the unspecified group, which the
    report shows as its own row rather than silently folding into another.
    """
    make_segment(org_world['org'], 'department', 'Department',
                 [('FIN', 'Finance')])
    db.session.commit()
    assert validate_segment_values(
        org_world['org'].id, {'department': 'GONE'}) == {}
    assert validate_segment_values(
        org_world['org'].id, {'not_a_segment': 'FIN'}) == {}


def test_an_inactive_segment_is_not_collected(db, org_world):
    make_segment(org_world['org'], 'department', 'Department',
                 [('FIN', 'Finance')], is_active=False)
    db.session.commit()
    assert validate_segment_values(
        org_world['org'].id, {'department': 'FIN'}) == {}


def test_segment_values_store_codes_so_a_rename_cannot_regroup_history(
        db, org_world):
    """
    `response_sessions.segment_values` holds the option CODE, never the label.
    Renaming 'Finance' to 'Finance & Procurement' must not re-group a single
    historical response.
    """
    segment = make_segment(org_world['org'], 'department', 'Department',
                           [('FIN', 'Finance')])
    db.session.commit()

    stored = validate_segment_values(org_world['org'].id, {'department': 'FIN'})
    option = db.session.query(SegmentOption).filter_by(
        segment_id=segment.id).one()
    option.option_label = 'Finance & Procurement'
    db.session.commit()

    assert stored == {'department': 'FIN'}
    assert validate_segment_values(
        org_world['org'].id, {'department': 'FIN'}) == stored
