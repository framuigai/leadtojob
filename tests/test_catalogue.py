"""
The intervention catalogue, from the supplied curriculum.

THE ONE RULE THIS FILE EXISTS FOR — §8.5:

    "Training should not be treated as a substitute for addressing
     substantiated compensation inequities or structural reward problems."

The filter runs before a row reaches a report or the narrative payload, so the
model cannot recommend a course it was never shown.

It also pins the correction made when the real curriculum arrived: permission
to recommend training comes from §8.5's ONE explicit prohibition, not from our
reading of §11's example categories. The earlier rule would have discarded 14
of the 26 supplied courses.
"""
import json

import pytest

from app import catalogue, db, domains
from app.domains import (
    POLICY_OR_STRUCTURAL, TRAINING, WORK_DESIGN,
)
from app.models import InterventionCatalogue, InterventionCell
from seed_catalogue import CatalogueError, read_catalogue, seed
from tests.conftest import login
from tests.factories import make_master_admin, make_org, make_org_admin


@pytest.fixture
def l1(db):
    admin = make_master_admin(email='l1-cat@leadto.test')
    db.session.commit()
    return admin


@pytest.fixture
def seeded(db):
    seed(force=True)
    return db


# ── The supplied file ──────────────────────────────────────────────────────

def test_the_supplied_curriculum_reads_and_validates():
    payload = read_catalogue()
    assert len(payload['topics']) == 25
    assert sum(len(t['cells']) for t in payload['topics']) == 26
    assert len(payload['reward_guidance']) == 3


def test_one_topic_serves_two_cells_and_is_one_row():
    """
    PO-EPS-T01 "Goal Clarity and Alignment" answers both Match with Overall
    Expectations and Purpose and Impact. Duplicating it under two codes would
    force every report to deduplicate by title, forever.
    """
    payload = read_catalogue()
    shared = [t for t in payload['topics'] if len(t['cells']) > 1]
    assert [t['intervention_code'] for t in shared] == ['PO-EPS-T01']
    cells = {(c['domain_code'], c['subdomain_code']) for c in shared[0]['cells']}
    assert cells == {('GEM', 'M'), ('TID', 'I')}


def test_no_course_is_mapped_to_a_reward_subdomain():
    """The supplied curriculum reached §8.5's conclusion independently."""
    payload = read_catalogue()
    for topic in payload['topics']:
        for cell in topic['cells']:
            assert cell['domain_code'] != domains.PIE, topic['intervention_code']


def test_the_reward_subdomains_carry_guidance_instead_of_courses():
    payload = read_catalogue()
    subdomains = {g['subdomain_code'] for g in payload['reward_guidance']}
    assert subdomains == {'P', 'I', 'E'}
    joined = ' '.join(g['guidance'] for g in payload['reward_guidance']).lower()
    assert 'pay-equity' in joined
    assert 'benchmarking' in joined
    assert 'compensation review' in joined


# ── The seeder rejects rather than skips ───────────────────────────────────

def _write(tmp_path, payload):
    path = tmp_path / 'catalogue.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    return str(path)


def test_a_course_mapped_to_a_reward_subdomain_aborts_the_seed(tmp_path):
    """
    THE LOAD-BEARING REFUSAL. A later curriculum delivery that mapped a
    resilience course to Peer Comparison must fail loudly, not be skipped.
    """
    path = _write(tmp_path, {'topics': [{
        'intervention_code': 'XX-T01', 'title': 'Resilience',
        'intervention_type': 'training',
        'cells': [{'domain_code': 'PIE', 'subdomain_code': 'P'}]}]})
    with pytest.raises(CatalogueError, match='§8.5'):
        read_catalogue(path)


def test_an_unknown_cell_aborts_the_seed(tmp_path):
    path = _write(tmp_path, {'topics': [{
        'intervention_code': 'XX-T01', 'title': 'Something',
        'intervention_type': 'training',
        'cells': [{'domain_code': 'GEM', 'subdomain_code': 'Z'}]}]})
    with pytest.raises(CatalogueError, match='not a known'):
        read_catalogue(path)


def test_a_topic_with_no_cell_aborts_the_seed(tmp_path):
    path = _write(tmp_path, {'topics': [{
        'intervention_code': 'XX-T01', 'title': 'Orphan',
        'intervention_type': 'training', 'cells': []}]})
    with pytest.raises(CatalogueError, match='mapped to no cell'):
        read_catalogue(path)


def test_a_duplicated_topic_code_aborts_the_seed(tmp_path):
    """One topic in two cells is ONE row with two cells, not two rows."""
    topic = {'intervention_code': 'XX-T01', 'title': 'Same',
             'intervention_type': 'training',
             'cells': [{'domain_code': 'GEM', 'subdomain_code': 'G'}]}
    path = _write(tmp_path, {'topics': [topic, dict(topic)]})
    with pytest.raises(CatalogueError, match='appears twice'):
        read_catalogue(path)


def test_an_unknown_intervention_type_aborts_the_seed(tmp_path):
    path = _write(tmp_path, {'topics': [{
        'intervention_code': 'XX-T01', 'title': 'Something',
        'intervention_type': 'vibes',
        'cells': [{'domain_code': 'GEM', 'subdomain_code': 'G'}]}]})
    with pytest.raises(CatalogueError, match='intervention_type'):
        read_catalogue(path)


# ── Seeding ────────────────────────────────────────────────────────────────

def test_seeding_creates_the_topics_and_their_cells(seeded):
    assert db.session.query(InterventionCatalogue).count() == 25
    assert db.session.query(InterventionCell).count() == 26


def test_seeding_is_idempotent(seeded):
    assert seed(force=False) == 0
    assert db.session.query(InterventionCatalogue).count() == 25


def test_every_cell_is_covered_or_deliberately_course_free(seeded):
    """
    The sibling product shipped with one uncovered cell and had to name the gap
    in its reports. This curriculum covers all nine trainable subdomains.
    """
    gaps = [row for row in catalogue.coverage(db.session) if not row['covered']]
    assert gaps == [], [g['label'] for g in gaps]


# ── The filter ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize('subdomain', ['P', 'I', 'E'])
def test_a_reward_subdomain_returns_no_course(seeded, subdomain):
    """THE NAMED RULE. Checked before the database is even queried."""
    assert catalogue.for_cell(db.session, 'PIE', subdomain) == []


def test_a_reward_finding_returns_guidance_rather_than_an_empty_list(seeded):
    """
    An empty recommendation reads as "nothing to do here", which is the worst
    available reading of a pay finding.
    """
    rec = catalogue.recommendations(db.session, 'PIE', 'P')
    assert rec['courses'] == []
    assert rec['training_permitted'] is False
    assert 'pay-equity' in rec['guidance']
    assert rec['response_categories'] == 'Role and pay-equity review'


def test_a_trainable_subdomain_returns_its_supplied_courses(seeded):
    rec = catalogue.recommendations(db.session, 'BIT', 'T')
    titles = {c.title for c in rec['courses']}
    assert 'Psychological Safety Fundamentals' in titles
    assert 'Building Trust and Credibility' in titles
    assert len(rec['courses']) == 4


def test_the_shared_topic_appears_under_both_its_cells(seeded):
    for pair in (('GEM', 'M'), ('TID', 'I')):
        codes = {c.intervention_code
                 for c in catalogue.for_cell(db.session, *pair)}
        assert 'PO-EPS-T01' in codes, pair


def test_every_recommendation_carries_the_specs_own_categories(seeded):
    """
    §11: "Training is one intervention category among several." A report that
    listed courses and nothing else would have made training the answer.
    """
    for domain_code, subdomain_code in domains.subdomain_pairs():
        rec = catalogue.recommendations(db.session, domain_code, subdomain_code)
        assert rec['response_categories']
        assert rec['areas_to_investigate']


def test_the_order_is_deterministic(seeded):
    """
    Two runs of the same report must list the same courses in the same order,
    or a client comparing a saved PDF with a regenerated one finds a difference
    that means nothing.
    """
    first = [c.intervention_code for c in catalogue.for_cell(db.session, 'BIT', 'T')]
    second = [c.intervention_code for c in catalogue.for_cell(db.session, 'BIT', 'T')]
    assert first == second == sorted(first)


def test_reward_cells_do_not_crowd_out_every_course(seeded):
    """
    All three Reward subdomains ranking first used to leave a report naming no
    course at all: PIE carries none by §8.5, and the quadrant held only those.
    The priority order is unchanged — Reward still leads — but trainable cells
    below the cut now reach the report.
    """
    class _Entry:
        def __init__(self, diagnostic, quadrant):
            self.diagnostic = diagnostic
            self.quadrant = quadrant

    class _View:
        entries = [
            _Entry(('PIE', 'P'), 'investigate_first'),
            _Entry(('PIE', 'I'), 'investigate_first'),
            _Entry(('PIE', 'E'), 'investigate_first'),
            _Entry(('BIT', 'T'), 'monitor'),
            _Entry(('TID', 'D'), 'monitor'),
        ]

    recs = catalogue.for_priority(db.session, _View())

    # Reward still leads, and still carries guidance rather than a course.
    assert [r['subdomain_code'] for r in recs[:3]] == ['P', 'I', 'E']
    assert all(not r['courses'] for r in recs[:3])
    assert all(r['guidance'] for r in recs[:3])

    # And at least one course is now nameable, with its code.
    named = [c for r in recs for c in r['courses']]
    assert named
    assert all(c.intervention_code for c in named)


# ── The correction made when the real curriculum arrived ───────────────────

def test_training_permission_comes_from_the_one_explicit_prohibition():
    """
    §8.5 names ONE domain. Nothing else in the specification excludes training,
    and §11's closing line is a rule about evidence rather than a whitelist of
    cells.

    The earlier version derived permission from our reading of §11's example
    categories, and would have discarded 14 of the 26 supplied courses —
    including every course for Trust, Purpose and Impact, and Emotional
    Experience.
    """
    assert domains.TRAINING_EXCLUDED_DOMAINS == [domains.PIE]

    for domain_code, subdomain_code in domains.subdomain_pairs():
        permitted = domains.allows_training(domain_code, subdomain_code)
        assert permitted == (domain_code != domains.PIE), (domain_code,
                                                           subdomain_code)


def test_the_supplied_curriculum_would_not_be_silently_reduced(seeded):
    """
    The regression guard for that correction: every supplied course must be
    reachable through the filter.
    """
    payload = read_catalogue()
    expected = sum(len(t['cells']) for t in payload['topics'])
    reachable = sum(len(catalogue.for_cell(db.session, d, s))
                    for d, s in domains.subdomain_pairs())
    assert reachable == expected == 26


# ── The screen ─────────────────────────────────────────────────────────────

def test_the_catalogue_screen_shows_cells_and_names_the_reward_exception(
        client, seeded):
    from tests.conftest import login
    from tests.factories import make_master_admin

    l1 = make_master_admin(email='l1@leadto.test')
    db.session.commit()

    login(client, l1)
    # Whitespace collapsed: the template wraps mid-phrase, and an exact
    # substring match would fail on wording that is plainly on the page.
    import re
    body = re.sub(r'\s+', ' ', client.get('/admin/catalogue').get_data(as_text=True))
    assert 'Psychological Safety Fundamentals' in body
    assert 'no course, by design' in body
    assert 'not a substitute for addressing a compensation problem' in body
    assert 'Peer Comparison' in body
    # And the gap banner must NOT be showing: this curriculum covers every cell.
    assert 'cell(s) have no course' not in body


def test_the_coverage_grid_shows_every_cell_and_flags_an_empty_one(
        client, seeded):
    """
    The grid is the whole 4 x 3 map, and a cell with no topic says so rather
    than rendering blank — a blank cell reads as "nothing needed here".
    """
    import re
    from app import domains
    from app.models import InterventionCell
    from tests.conftest import login
    from tests.factories import make_master_admin

    # Empty one cell that IS allowed training, so it must read "no topic"
    # rather than "no course, by design".
    db.session.query(InterventionCell).filter_by(
        domain_code='TID', subdomain_code='T').delete()
    l1 = make_master_admin(email='l1@leadto.test')
    db.session.commit()

    login(client, l1)
    body = re.sub(r'\s+', ' ', client.get('/admin/catalogue').get_data(as_text=True))

    assert 'Coverage by capability area' in body
    for domain_code, subdomain_code in domains.subdomain_pairs():
        assert domains.subdomain_label(domain_code, subdomain_code) in body
    assert 'no topic' in body


def test_the_topic_list_shows_every_active_topic_with_its_cells(client, seeded):
    """
    One row per topic, carrying the filter keys the shared component reads.
    A topic mapped to two cells stays one row, so the count is a topic count.
    """
    import re
    from app.models import InterventionCatalogue
    from tests.conftest import login
    from tests.factories import make_master_admin

    l1 = make_master_admin(email='l1@leadto.test')
    db.session.commit()

    login(client, l1)
    raw = client.get('/admin/catalogue').get_data(as_text=True)
    body = re.sub(r'\s+', ' ', raw)

    active = db.session.query(InterventionCatalogue).filter_by(
        is_active=True).all()
    assert raw.count('data-programme=') == len(active)
    for topic in active:
        assert topic.intervention_code in body

    # The filter bar is wired to the table, not hand-rolled.
    assert 'data-filter-for="course-list"' in body
    assert 'id="course-list"' in body


def test_the_catalogue_screen_is_l1_only(client, seeded):
    from tests.conftest import login
    from tests.factories import make_org, make_org_admin

    org = make_org('Acme')
    admin = make_org_admin(org, email='l2@acme.test')
    db.session.commit()

    login(client, admin)
    assert client.get('/admin/catalogue').status_code == 302


# ── Editing the catalogue through the screen ──────────────────────

def _topic_form(**over):
    data = {
        'intervention_code': 'PO-NEW-T01',
        'title': 'Goal Clarity and Alignment',
        'description': 'A short description.',
        'intervention_type': WORK_DESIGN,
        'actionable_by': 'org',
        'source_programme': 'Performance Optimisation',
        'cells': ['GEM:M'],
    }
    data.update(over)
    return data


def test_l1_can_add_a_topic_to_the_catalogue(client, db, l1):
    login(client, l1)
    client.post('/admin/catalogue/new', data=_topic_form(),
                follow_redirects=True)

    row = db.session.query(InterventionCatalogue).filter_by(
        intervention_code='PO-NEW-T01').one()
    assert row.is_active is True
    assert row.intervention_type == WORK_DESIGN
    cells = db.session.query(InterventionCell).filter_by(
        intervention_id=row.id).all()
    assert [(c.domain_code, c.subdomain_code) for c in cells] == [('GEM', 'M')]


def test_a_topic_may_serve_several_cells(client, db, l1):
    """
    PO-EPS-T01 in the supplied curriculum answers both (GEM, M) and (TID, I).
    That is correct content, not a data error, which is why the cells are a
    separate table.
    """
    login(client, l1)
    client.post('/admin/catalogue/new',
                data=_topic_form(cells=['GEM:M', 'TID:I']),
                follow_redirects=True)

    row = db.session.query(InterventionCatalogue).filter_by(
        intervention_code='PO-NEW-T01').one()
    cells = db.session.query(InterventionCell).filter_by(
        intervention_id=row.id).all()
    assert sorted((c.domain_code, c.subdomain_code) for c in cells) == [
        ('GEM', 'M'), ('TID', 'I')]


def test_training_cannot_be_mapped_to_a_reward_area(client, db, l1):
    """
    SPECIFICATION §8.5, THE ONE EXPLICIT PROHIBITION: "Training should not be
    treated as a substitute for addressing substantiated compensation
    inequities or structural reward problems."

    app/catalogue.py already filters it out at read time. This is the other
    half: a row that could never legally be recommended must not be storable
    in the first place, because a stored one looks like an oversight to the
    next operator who reads the table.
    """
    login(client, l1)
    res = client.post('/admin/catalogue/new',
                      data=_topic_form(intervention_type=TRAINING,
                                       cells=['PIE:P']),
                      follow_redirects=True)

    assert '8.5' in res.get_data(as_text=True)
    assert db.session.query(InterventionCatalogue).filter_by(
        intervention_code='PO-NEW-T01').first() is None


def test_a_non_training_topic_may_serve_a_reward_area(client, db, l1):
    """
    The prohibition is on TRAINING, not on the Reward domain. A pay-equity
    review is exactly what belongs there, and refusing it would leave the
    domain with no response at all.
    """
    login(client, l1)
    client.post('/admin/catalogue/new',
                data=_topic_form(intervention_type=POLICY_OR_STRUCTURAL,
                                 cells=['PIE:P']),
                follow_redirects=True)

    row = db.session.query(InterventionCatalogue).filter_by(
        intervention_code='PO-NEW-T01').one()
    assert row.intervention_type == POLICY_OR_STRUCTURAL


def test_a_topic_with_no_capability_area_is_refused(client, db, l1):
    """
    InterventionCell's own docstring: a topic with no row there reaches no
    report. Saving one stores something that can never be recommended, with
    nothing on screen to say so.
    """
    login(client, l1)
    res = client.post('/admin/catalogue/new', data=_topic_form(cells=[]),
                      follow_redirects=True)

    assert 'at least one capability area' in res.get_data(as_text=True)
    assert db.session.query(InterventionCatalogue).filter_by(
        intervention_code='PO-NEW-T01').first() is None


def test_a_duplicate_code_is_refused(client, db, l1):
    login(client, l1)
    client.post('/admin/catalogue/new', data=_topic_form(),
                follow_redirects=True)
    res = client.post('/admin/catalogue/new',
                      data=_topic_form(title='Something else'),
                      follow_redirects=True)

    assert 'already in use' in res.get_data(as_text=True)
    assert db.session.query(InterventionCatalogue).filter_by(
        intervention_code='PO-NEW-T01').count() == 1


def test_editing_replaces_the_cells_rather_than_adding_to_them(client, db, l1):
    login(client, l1)
    client.post('/admin/catalogue/new',
                data=_topic_form(cells=['GEM:M', 'TID:I']),
                follow_redirects=True)
    row = db.session.query(InterventionCatalogue).filter_by(
        intervention_code='PO-NEW-T01').one()

    client.post(f'/admin/catalogue/{row.id}/edit',
                data=_topic_form(cells=['BIT:B'], title='Renamed'),
                follow_redirects=True)

    db.session.refresh(row)
    assert row.title == 'Renamed'
    cells = db.session.query(InterventionCell).filter_by(
        intervention_id=row.id).all()
    assert [(c.domain_code, c.subdomain_code) for c in cells] == [('BIT', 'B')]


def test_removing_a_topic_is_reversible_and_hides_it_everywhere(
        client, db, l1):
    """
    SOFT, NOT A DELETE. coverage() and every report filter on is_active, so a
    removal stops the topic being offered everywhere at once - which is what
    "remove" has to mean for data shared by every tenant. The row is kept so
    the code stays reserved and an operator who removes the wrong one does not
    need a database restore.
    """
    login(client, l1)
    client.post('/admin/catalogue/new', data=_topic_form(),
                follow_redirects=True)
    row = db.session.query(InterventionCatalogue).filter_by(
        intervention_code='PO-NEW-T01').one()

    client.post(f'/admin/catalogue/{row.id}/status', data={'restore': '0'},
                follow_redirects=True)
    db.session.refresh(row)
    assert row.is_active is False

    body = client.get('/admin/catalogue').get_data(as_text=True)
    assert 'Removed from the catalogue' in body

    client.post(f'/admin/catalogue/{row.id}/status', data={'restore': '1'},
                follow_redirects=True)
    db.session.refresh(row)
    assert row.is_active is True


def test_the_catalogue_editor_is_l1_only(client, db, l1):
    """
    It carries no org_id, so one edit changes every client's reports. An L2
    admin must not reach it.
    """
    org = make_org('Acme Ltd')
    l2 = make_org_admin(org, email='l2-cat@acme.test')
    db.session.commit()

    login(client, l2)
    for path in ('/admin/catalogue', '/admin/catalogue/new'):
        res = client.get(path, follow_redirects=True)
        assert 'New topic' not in res.get_data(as_text=True), path
