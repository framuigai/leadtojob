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
from app.models import InterventionCatalogue, InterventionCell
from seed_catalogue import CatalogueError, read_catalogue, seed


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


def test_the_catalogue_screen_is_l1_only(client, seeded):
    from tests.conftest import login
    from tests.factories import make_org, make_org_admin

    org = make_org('Acme')
    admin = make_org_admin(org, email='l2@acme.test')
    db.session.commit()

    login(client, admin)
    assert client.get('/admin/catalogue').status_code == 302
