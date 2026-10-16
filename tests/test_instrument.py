"""
PHASE 3'S ACCEPTANCE TEST — the instrument.

    48 items, 4 per subdomain, every item_code unique and well-formed;
    a short subdomain aborts the seed naming the line.

The seeder's strictness is the point. A subdomain delivered with three
questions instead of four does not crash anything: it produces a subdomain
score computed from three items, which looks exactly like a real score and is
wrong by an amount nobody can estimate. It would surface weeks later, as a
finding somebody had already acted on.
"""
import io
import os

import pytest

from app import db
from app.domains import (
    DOMAINS, ITEMS_PER_SUBDOMAIN, MASTER_ORG_ID, SUBDOMAINS, all_item_codes,
    parse_item_code,
)
from app.models import SurveyItem
from app.provisioning import ProvisioningError, clone_instrument_to_org
from seed_templates import SURVEY_DIR, SeedError, read_all_items
from tests.conftest import login
from tests.factories import (
    make_master_admin, make_master_instrument, make_org, make_org_admin,
)


# ── The supplied files (no database needed) ─────────────────────────────────

def test_the_four_csvs_hold_exactly_48_valid_items():
    items = read_all_items()
    assert len(items) == 48
    codes = [i['item_code'] for i in items]
    assert len(set(codes)) == 48
    assert sorted(codes) == sorted(all_item_codes())


def test_every_subdomain_holds_exactly_four_items():
    """
    Checked per cell, not as a total. Five items in one subdomain and three in
    another also totals 12 for the domain — and that is the error most likely
    to survive a human reading the file.
    """
    items = read_all_items()
    for domain in DOMAINS:
        for sub in SUBDOMAINS[domain]:
            cell = [i for i in items
                    if i['domain_code'] == domain and i['subdomain_code'] == sub]
            assert len(cell) == ITEMS_PER_SUBDOMAIN, f'{domain}-{sub}'
            assert sorted(i['ordinal'] for i in cell) == [1, 2, 3, 4]


def test_every_item_code_parses_back_to_its_own_cell():
    for item in read_all_items():
        domain, sub, ordinal = parse_item_code(item['item_code'])
        assert (domain, sub, ordinal) == (
            item['domain_code'], item['subdomain_code'], item['ordinal'])


def test_item_text_never_names_the_construct_it_measures():
    """
    A question that announces its own subdomain tells the respondent what is
    being scored, which changes the answer. The construct names live in
    app/domains.py and on the admin screens; they are not in the wording.
    """
    leaked = ('general job satisfaction', 'emotional experience',
              'peer comparison', 'industry comparison', 'belonging',
              'positive interactions', 'daunting', 'thrilling')
    for item in read_all_items():
        low = item['item_text'].lower()
        for phrase in leaked:
            assert phrase not in low, f'{item["item_code"]}: {phrase!r}'


def test_no_item_mentions_stress_or_overload():
    """
    Specification §6.4: the challenge subdomain "must not be interpreted as
    excessive pressure, unmanageable workload or persistent stress". The
    supplied items are worded carefully; this pins that a later revision cannot
    quietly reintroduce it.
    """
    for item in read_all_items():
        low = item['item_text'].lower()
        for word in ('stress', 'burnout', 'overload', 'exhaust'):
            assert word not in low, f'{item["item_code"]}: {word!r}'


# ── The seeder rejects, never truncates ─────────────────────────────────────

@pytest.fixture
def broken_csv(tmp_path, monkeypatch):
    """Copy the real CSVs to a temp dir so one can be damaged safely."""
    import shutil
    import seed_templates

    dest = tmp_path / 'surveys'
    shutil.copytree(SURVEY_DIR, dest)
    monkeypatch.setattr(seed_templates, 'SURVEY_DIR', str(dest))
    return dest


def _write(path, rows):
    header = 'domain_code,subdomain_code,ordinal,item_code,item_text\n'
    io.open(path, 'w', encoding='utf-8').write(header + ''.join(rows))


def test_a_short_subdomain_aborts_the_seed_naming_the_file(broken_csv):
    """THE NAMED ACCEPTANCE CRITERION."""
    path = broken_csv / 'gem.csv'
    lines = io.open(path, encoding='utf-8').read().splitlines(keepends=True)
    # Drop GEM-G-04 — leaving its subdomain with three items.
    kept = [l for l in lines[1:] if 'GEM-G-04' not in l]
    _write(path, kept)

    with pytest.raises(SeedError) as exc:
        read_all_items()
    message = str(exc.value)
    assert 'gem.csv' in message
    assert 'GEM-G' in message
    assert '[1, 2, 3, 4]' in message or 'exactly 4' in message


def test_an_unknown_subdomain_aborts_naming_the_line(broken_csv):
    path = broken_csv / 'tid.csv'
    lines = io.open(path, encoding='utf-8').read().splitlines(keepends=True)
    lines[1] = lines[1].replace('TID,T,', 'TID,Z,', 1)
    _write(path, lines[1:])

    with pytest.raises(SeedError) as exc:
        read_all_items()
    message = str(exc.value)
    assert 'tid.csv:2' in message
    assert "'Z'" in message


def test_a_mismatched_item_code_is_rejected_rather_than_guessed(broken_csv):
    """
    When the code disagrees with the domain, subdomain and ordinal beside it,
    one of the four is wrong and the file does not say which. Trusting either
    silently mis-files an item.
    """
    path = broken_csv / 'bit.csv'
    lines = io.open(path, encoding='utf-8').read().splitlines(keepends=True)
    lines[1] = lines[1].replace('BIT-B-01', 'BIT-T-01', 1)
    _write(path, lines[1:])

    with pytest.raises(SeedError, match='does not match'):
        read_all_items()


def test_an_empty_question_is_rejected(broken_csv):
    path = broken_csv / 'pie.csv'
    lines = io.open(path, encoding='utf-8').read().splitlines(keepends=True)
    lines[1] = 'PIE,P,1,PIE-P-01,\n'
    _write(path, lines[1:])

    with pytest.raises(SeedError, match='item_text is empty'):
        read_all_items()


def test_a_missing_file_is_named(broken_csv):
    os.remove(broken_csv / 'pie.csv')
    with pytest.raises(SeedError, match='pie.csv'):
        read_all_items()


# ── Cloning (database) ──────────────────────────────────────────────────────

def test_cloning_gives_an_org_its_own_48_items(db):
    make_master_instrument()
    org = make_org('Acme Ltd')
    assert clone_instrument_to_org(org.id) == 48
    db.session.commit()

    items = db.session.query(SurveyItem).filter_by(org_id=org.id).all()
    assert len(items) == 48
    assert len({i.item_code for i in items}) == 48


def test_a_clone_is_independent_of_the_master(db):
    """
    THE POINT OF CLONING AT ALL.

    A client's instrument is evidence: once a wave has run, "what did this
    question say?" has exactly one correct answer. Editing the master must
    never reach an org that already cloned it, or 300 people's answers silently
    become answers to a different question.
    """
    make_master_instrument()
    org = make_org('Acme Ltd')
    clone_instrument_to_org(org.id)
    db.session.commit()

    master_item = (db.session.query(SurveyItem)
                   .filter_by(org_id=MASTER_ORG_ID, item_code='GEM-G-01').one())
    master_item.item_text = 'REWRITTEN AFTER THE CLIENT CLONED IT'
    db.session.commit()

    client_item = (db.session.query(SurveyItem)
                   .filter_by(org_id=org.id, item_code='GEM-G-01').one())
    assert client_item.item_text != master_item.item_text
    assert client_item.item_text.startswith('Overall, I am satisfied')


def test_cloning_twice_is_refused(db):
    make_master_instrument()
    org = make_org('Acme Ltd')
    clone_instrument_to_org(org.id)
    db.session.commit()

    with pytest.raises(ProvisioningError, match='already holds'):
        clone_instrument_to_org(org.id)


def test_cloning_from_an_empty_master_is_refused(db):
    """
    An org cloned from an empty master gets a survey with no questions, and that
    failure surfaces only when a respondent opens the link — by which point the
    invitation has gone out.
    """
    org = make_org('Acme Ltd')
    with pytest.raises(ProvisioningError, match='master instrument is empty'):
        clone_instrument_to_org(org.id)


def test_cloning_a_partial_master_is_refused(db):
    make_master_instrument()
    db.session.query(SurveyItem).filter_by(
        org_id=MASTER_ORG_ID, domain_code='PIE').delete()
    db.session.commit()

    org = make_org('Acme Ltd')
    with pytest.raises(ProvisioningError, match='expected 48'):
        clone_instrument_to_org(org.id)


def test_the_master_cannot_be_cloned_into_itself(db):
    make_master_instrument()
    with pytest.raises(ProvisioningError, match='into itself'):
        clone_instrument_to_org(MASTER_ORG_ID)


# ── Presentation order ──────────────────────────────────────────────────────

def test_subdomains_are_interleaved_within_a_domain_not_blocked(db):
    """
    Four near-identical autonomy questions in a row invite straight-lining: the
    respondent answers the first and pattern-matches the rest. Nothing on the
    form names a subdomain, so interleaving costs them nothing in coherence.
    """
    make_master_instrument()
    from seed_templates import _interleaved

    order = _interleaved(read_all_items())
    tid = [i for i in order if i['domain_code'] == 'TID']
    subs = [i['subdomain_code'] for i in tid]
    assert subs[:3] == ['T', 'I', 'D'], subs
    # No subdomain ever appears twice in a row.
    assert not any(a == b for a, b in zip(subs, subs[1:])), subs


def test_domains_stay_blocked_even_though_subdomains_interleave(db):
    """Each domain is its own step of the form, with its own introduction."""
    from seed_templates import _interleaved

    order = [i['domain_code'] for i in _interleaved(read_all_items())]
    # Each domain appears as one contiguous run.
    runs = [k for k, _ in __import__('itertools').groupby(order)]
    assert runs == DOMAINS


# ── The screens ─────────────────────────────────────────────────────────────

def test_l1_can_read_the_master_instrument(client, db):
    make_master_instrument()
    l1 = make_master_admin(email='l1@leadto.test')
    db.session.commit()

    login(client, l1)
    body = client.get('/admin/templates').get_data(as_text=True)
    assert 'Assessment Instrument' in body

    body = client.get('/admin/templates/GEM').get_data(as_text=True)
    assert 'Overall, I am satisfied with my current job.' in body
    assert 'GEM-G-01' in body


def test_the_challenge_subdomain_carries_its_caveat_on_the_admin_screen(client, db):
    """
    §6.4 again. The screen that shows the mnemonic is exactly the screen that
    should explain that a low score can mean under-stretched as easily as
    overwhelmed.
    """
    make_master_instrument()
    l1 = make_master_admin(email='l1@leadto.test')
    db.session.commit()

    login(client, l1)
    body = client.get('/admin/templates/TID').get_data(as_text=True)
    assert 'Appropriate Challenge and Development' in body
    assert 'Daunting' not in body
    assert 'not sufficiently stretching' in body


def test_an_org_admin_sees_their_instrument_read_only(client, db):
    make_master_instrument()
    org = make_org('Acme Ltd')
    clone_instrument_to_org(org.id)
    admin = make_org_admin(org, email='l2@acme.test')
    db.session.commit()

    login(client, admin)
    body = client.get('/org/instrument').get_data(as_text=True)
    assert 'Overall, I am satisfied with my current job.' in body
    # Read-only: no internal keys, and no edit affordance.
    assert 'GEM-G-01' not in body
    assert 'Read-only' in body or 'read-only' in body.lower()


def test_an_org_admin_cannot_reach_the_master_template_editor(client, db):
    make_master_instrument()
    org = make_org('Acme Ltd')
    admin = make_org_admin(org, email='l2@acme.test')
    db.session.commit()

    login(client, admin)
    res = client.get('/admin/templates')
    assert res.status_code == 302
    assert '/org/' in res.headers['Location']


# ── The L1 editor ───────────────────────────────────────────────────────────

def _edit_form(items, **overrides):
    """The editor posts every row back, not just the changed one."""
    form = {}
    for i in items:
        form[f'text-{i.id}'] = overrides.get(str(i.id), i.item_text)
        if i.is_active:
            form[f'active-{i.id}'] = 'on'
        if i.is_reverse_scored:
            form[f'reverse-{i.id}'] = 'on'
    return form


def _master_items(domain_code=None, version=1):
    q = db.session.query(SurveyItem).filter_by(
        org_id=MASTER_ORG_ID, version=version)
    if domain_code:
        q = q.filter_by(domain_code=domain_code)
    return q.order_by(SurveyItem.sort_order).all()


def test_l1_saving_an_edit_creates_a_new_version_and_leaves_the_old_one(client, db):
    """
    An item is never rewritten in place: version 1 must still answer "what did
    this question say?" after version 2 exists.
    """
    make_master_instrument()
    l1 = make_master_admin(email='l1@leadto.test')
    db.session.commit()
    login(client, l1)

    items = _master_items('GEM')
    target, original = items[0], items[0].item_text
    form = _edit_form(items, **{str(target.id): 'Reworded for the pilot.'})

    res = client.post('/admin/templates/GEM/edit', data=form)
    assert res.status_code == 302

    old = db.session.query(SurveyItem).filter_by(
        org_id=MASTER_ORG_ID, version=1, item_code=target.item_code).one()
    assert old.item_text == original

    new = db.session.query(SurveyItem).filter_by(
        org_id=MASTER_ORG_ID, version=2, item_code=target.item_code).one()
    assert new.item_text == 'Reworded for the pilot.'


def test_a_new_version_carries_the_whole_instrument_not_just_the_edited_domain(client, db):
    """
    A version holding only GEM is a 12-item instrument, and cloning one into a
    client produces a survey that is short in a way nothing downstream checks.
    """
    make_master_instrument()
    l1 = make_master_admin(email='l1@leadto.test')
    db.session.commit()
    login(client, l1)

    items = _master_items('GEM')
    form = _edit_form(items, **{str(items[0].id): 'Changed.'})
    client.post('/admin/templates/GEM/edit', data=form)

    assert len(_master_items(version=2)) == 48


def test_an_edit_does_not_reach_an_org_that_has_already_cloned(client, db):
    """The client copy is evidence. Editing the master never rewrites it."""
    make_master_instrument()
    org = make_org('Acme Ltd')
    clone_instrument_to_org(org.id)
    l1 = make_master_admin(email='l1@leadto.test')
    db.session.commit()
    login(client, l1)

    items = _master_items('GEM')
    before = db.session.query(SurveyItem).filter_by(
        org_id=org.id, item_code=items[0].item_code).one().item_text

    form = _edit_form(items, **{str(items[0].id): 'Changed at the master.'})
    client.post('/admin/templates/GEM/edit', data=form)

    after = db.session.query(SurveyItem).filter_by(
        org_id=org.id, item_code=items[0].item_code).all()
    assert len(after) == 1
    assert after[0].item_text == before


def test_an_org_provisioned_after_an_edit_receives_the_edited_version(client, db):
    make_master_instrument()
    l1 = make_master_admin(email='l1@leadto.test')
    db.session.commit()
    login(client, l1)

    items = _master_items('GEM')
    code = items[0].item_code
    form = _edit_form(items, **{str(items[0].id): 'The current wording.'})
    client.post('/admin/templates/GEM/edit', data=form)

    later = make_org('Latecomer Ltd')
    clone_instrument_to_org(later.id)
    db.session.commit()

    cloned = db.session.query(SurveyItem).filter_by(
        org_id=later.id, item_code=code).one()
    assert cloned.version == 2
    assert cloned.item_text == 'The current wording.'


def test_empty_question_text_is_refused_and_writes_no_version(client, db):
    make_master_instrument()
    l1 = make_master_admin(email='l1@leadto.test')
    db.session.commit()
    login(client, l1)

    items = _master_items('GEM')
    form = _edit_form(items, **{str(items[0].id): '   '})
    res = client.post('/admin/templates/GEM/edit', data=form)

    assert res.status_code == 200
    assert _master_items(version=2) == []


def test_saving_with_nothing_changed_creates_no_version(client, db):
    make_master_instrument()
    l1 = make_master_admin(email='l1@leadto.test')
    db.session.commit()
    login(client, l1)

    client.post('/admin/templates/GEM/edit', data=_edit_form(_master_items('GEM')))
    assert _master_items(version=2) == []


def test_an_org_admin_cannot_reach_the_editor(client, db):
    make_master_instrument()
    org = make_org('Acme Ltd')
    admin = make_org_admin(org, email='l2@acme.test')
    db.session.commit()

    login(client, admin)
    res = client.get('/admin/templates/GEM/edit')
    assert res.status_code == 302
    assert '/org/' in res.headers['Location']

    res = client.post('/admin/templates/GEM/edit', data={})
    assert res.status_code == 302
    assert _master_items(version=2) == []
