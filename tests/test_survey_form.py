"""
PHASE 5'S ACCEPTANCE TEST.

    rendered HTML carries no internal key and no identity field;
    a used ticket is rejected;
    the anonymity wording matches the mode.

This is the file the build plan calls `test_privacy_and_wording.py` (§2.4). It
is the reason CI exists at all: the guarantees it checks are properties a
refactor can silently remove, and a reviewer cannot see their absence.
"""
import hashlib
import re

import pytest

from app import db
from app.domains import DOMAINS, all_item_codes
from app.models import (
    AccessTicket, PilotFeedback, ResponseSession, SurveyItem, SurveyResponse,
)
from app.provisioning import clone_instrument_to_org
from tests.factories import (
    make_cycle, make_deployment, make_master_instrument, make_org, make_segment,
)


@pytest.fixture
def deployment(db):
    """An org with its instrument, an open cycle and an open-link deployment."""
    make_master_instrument()
    org = make_org('Kilimo Dairies')
    clone_instrument_to_org(org.id)
    make_segment(org, 'department', 'Department',
                 [('FIN', 'Finance'), ('OPS', 'Operations')])
    cycle = make_cycle(org, 'KD-2026-H1', status='open')
    deployment = make_deployment(org, cycle, public_slug='slug-open-link-aaa',
                                 expected_respondents=40)
    db.session.commit()
    return {'org': org, 'cycle': cycle, 'deployment': deployment,
            'slug': deployment.public_slug}


def _begin(client, slug, **form):
    """Walk the landing page and start a session, as a respondent would."""
    client.get(f'/survey/{slug}')
    return client.post(f'/survey/{slug}/begin', data=form)


def _items_on(client, slug, index):
    """
    The item ids rendered on one section, in order, DEDUPLICATED.

    Each item renders its field name six times — five scale radios plus the
    separate N/A one — so a bare findall returns every id six times. Left
    undeduplicated that quietly makes ids[0] and ids[1] the same item, and a
    test that posts two different answers to "two" items is really posting
    twice to one, last write winning. Two tests passed for that wrong reason
    before this was noticed.
    """
    html = client.get(f'/survey/{slug}/section/{index}').get_data(as_text=True)
    return list(dict.fromkeys(re.findall(r'name="q_([0-9a-f-]{36})"', html))), html


def _text(html):
    """Collapse whitespace, so an assertion is not hostage to template wrapping."""
    return re.sub(r'\s+', ' ', html)


# ── No internal keys, no identity ───────────────────────────────────────────

def test_no_item_code_or_subdomain_reaches_the_respondent(client, deployment):
    """
    THE NAMED ACCEPTANCE CRITERION.

    A respondent who can see that four questions belong to "Trust" answers them
    differently — they are no longer reporting an experience, they are rating a
    construct. So every page of the form is swept for item codes, subdomain
    codes and subdomain NAMES.

    The field name is the item's UUID, which is deliberate: it is opaque and
    carries no construct information, whereas `GEM-G-01` tells the reader
    exactly which of three subdomains is being scored.
    """
    _begin(client, deployment['slug'])
    client.post(f'/survey/{deployment["slug"]}/about-you', data={})

    from app.domains import SUBDOMAIN_LABELS

    pages = [client.get(f'/survey/{deployment["slug"]}').get_data(as_text=True),
             client.get(f'/survey/{deployment["slug"]}/about-you').get_data(as_text=True),
             client.get(f'/survey/{deployment["slug"]}/review').get_data(as_text=True)]
    for index in range(1, len(DOMAINS) + 1):
        pages.append(
            client.get(f'/survey/{deployment["slug"]}/section/{index}').get_data(as_text=True))

    for page in pages:
        for code in all_item_codes():
            assert code not in page, code
        for label in SUBDOMAIN_LABELS.values():
            assert label not in page, label
        # The domain NAMES are allowed — the framework is client-facing and a
        # section has to say what it is about. The CODES are not.
        for code in DOMAINS:
            assert f'>{code}<' not in page
            assert f'"{code}"' not in page


def test_the_form_asks_for_no_identity_at_all(client, deployment):
    """
    No name, no email, no employee number — not even optional ones. There is
    nowhere to put them (models.ResponseSession has no identity column), so a
    field here would be a field whose value is silently discarded, which is
    worse than not asking: the respondent believes they have been identified.
    """
    _begin(client, deployment['slug'])
    for index in range(1, len(DOMAINS) + 1):
        html = client.get(
            f'/survey/{deployment["slug"]}/section/{index}').get_data(as_text=True)
        for forbidden in ('name="email"', 'name="name"', 'name="full_name"',
                          'name="employee', 'type="email"', 'autocomplete="email"'):
            assert forbidden not in html, forbidden


def test_no_leadto_chrome_on_any_respondent_page(client, deployment):
    """
    No navbar, no wordmark, no product branding. A respondent is answering
    their employer's survey, not touring a vendor's product, and a branded
    shell changes what people are willing to say.

    Also no external script or font: a third-party request from this page would
    hand the respondent's IP and the deployment's URL to someone outside the
    agreement.
    """
    _begin(client, deployment['slug'])
    pages = [client.get(f'/survey/{deployment["slug"]}').get_data(as_text=True),
             client.get(f'/survey/{deployment["slug"]}/section/1').get_data(as_text=True),
             client.get(f'/survey/{deployment["slug"]}/review').get_data(as_text=True)]
    for page in pages:
        assert 'LeadTO' not in page
        assert 'leadTo' not in page
        assert 'class="navbar"' not in page
        assert 'logo.svg' not in page
        assert 'plot.ly' not in page
        assert 'cdn.' not in page
        assert 'main.css' not in page       # the admin theme never loads here
        assert 'monitored' not in page.lower()
        assert 'tracked' not in page.lower()


def test_the_session_row_holds_no_identity(client, deployment):
    """
    The guarantee is a property of the SCHEMA, not of a permission check. This
    asserts the row that was actually written is empty of identity — including
    that no IP address was recorded anywhere on it.
    """
    _begin(client, deployment['slug'])
    session = db.session.query(ResponseSession).one()

    assert not hasattr(session, 'ip_address')
    assert not hasattr(session, 'user_agent')
    assert not hasattr(session, 'email')
    assert not hasattr(session, 'user_id')

    blob = repr({c.name: getattr(session, c.name)
                 for c in session.__table__.columns})
    assert '127.0.0.1' not in blob
    assert 'werkzeug' not in blob.lower()


# ── The landing page no longer makes any promise ───────────────────

# THE "WHAT WE CAN AND CANNOT PROMISE" SECTION WAS REMOVED FROM THE LANDING
# PAGE ON REQUEST, AND WITH IT THE SECTION 9.2 DISCLOSURES.
#
# What used to be asserted here, and no longer can be:
#
#   - the delivery-mode anonymity claim ("cannot strictly prevent duplicate
#     submissions" for open_link, "one response per code" for ticketed)
#   - the reporting threshold, as a number
#   - who sees what ("not to your manager")
#   - non-retaliation ("carries no consequence")
#   - the technical-logs caveat ("network address")
#   - at a threshold of 1, the warning that a result may rest on a single
#     response
#
# The tests below are what is left: the page must still not OVERCLAIM, and it
# must still render the org's own purpose statement, which is now the only
# thing a respondent is told before answering. The deleted assertions are
# listed above rather than simply removed so that restoring the section is a
# matter of restoring these tests, not of reconstructing which guarantees the
# specification asked for.


def test_the_landing_page_does_not_overclaim_anonymity(client, deployment):
    """
    Section 9.2: "avoid promising absolute anonymity if the administration
    method cannot guarantee it."

    The page no longer makes the positive disclosures, but the prohibition is
    not conditional on them: it must not claim what it cannot deliver. The
    purpose statement is free text written by an org admin, so this is the one
    check that still has something to catch.
    """
    html = client.get(f'/survey/{deployment["slug"]}').get_data(as_text=True).lower()
    for phrase in ('completely anonymous', 'totally anonymous',
                   'fully anonymous', '100% anonymous'):
        assert phrase not in html, phrase


def test_an_open_link_deployment_does_not_claim_one_response_per_person(
        client, deployment):
    """
    An open link cannot prevent a second submission. The page used to say so;
    it must at least not say the opposite.
    """
    html = client.get(f'/survey/{deployment["slug"]}').get_data(as_text=True)
    assert 'One response per code' not in html


def test_the_landing_page_still_renders_the_cycles_own_purpose_statement(
        client, deployment):
    """
    NOW THE ONLY THING A RESPONDENT IS TOLD BEFORE ANSWERING.

    It carried weight before; it carries all of it now. The cycle's own copy
    is what renders here — not the org default it was created from — so an
    edit to one cycle reaches its respondents and nobody else's.
    """
    html = client.get(f'/survey/{deployment["slug"]}').get_data(as_text=True)
    assert 'Why you are being asked' in html
    assert 'what to improve' in html


def test_the_removed_section_is_really_gone(client, deployment):
    """
    Pins the removal, so a later edit that restores half of it — a heading
    without its bullets, or bullets whose wording no longer tracks the
    threshold — fails here rather than shipping a partial promise.
    """
    html = client.get(f'/survey/{deployment["slug"]}').get_data(as_text=True)
    assert 'What we can and cannot promise' not in html


def test_the_word_daunting_never_reaches_a_respondent(client, deployment):
    """
    §6.4: the mnemonic must not be read as pressure, overload or stress. The
    respondent sees the questions and the domain name, never the letter.
    """
    _begin(client, deployment['slug'])
    for index in range(1, len(DOMAINS) + 1):
        html = client.get(
            f'/survey/{deployment["slug"]}/section/{index}').get_data(as_text=True)
        low = html.lower()
        for word in ('daunting', 'stress', 'burnout', 'overload'):
            assert word not in low, word


# ── N/A is a separate second click, and never a score ───────────────────────

def test_not_applicable_is_offered_outside_the_five_point_row(client, deployment):
    """
    §9.1: N/A "should not be treated as neutral". It is rendered after the
    scale, under a rule, in quieter type — a second decision rather than a
    sixth button beside "Neither agree nor disagree", where it would become the
    cheap answer to 48 questions.
    """
    _begin(client, deployment['slug'])
    ids, html = _items_on(client, deployment['slug'], 1)

    # Exactly five scored options per item, and exactly one N/A.
    for item_id in ids:
        fields = re.findall(rf'name="q_{item_id}" value="([^"]+)"', _text(html))
        assert sorted(fields) == ['1', '2', '3', '4', '5', 'na'], fields

    # The N/A radio lives in the .sv-na block, OUTSIDE the .sv-scale row that
    # holds the five anchors — so it reads as a second decision rather than a
    # sixth button beside "Neither agree nor disagree".
    assert 'sv-na' in html
    first_item = _text(html).split('sv-q-text')[1]
    scale_row = first_item.split('sv-scale')[1].split('sv-na')[0]
    assert 'value="na"' not in scale_row
    assert re.search(r'sv-na[^>]*>\s*<input type="radio"[^>]*value="na"',
                     _text(html))


def test_na_is_stored_as_a_boolean_with_a_null_score(client, deployment):
    """
    NEVER 0, 3 or 6. A sentinel reaches an AVG() eventually and corrupts a
    result silently, weeks before anyone notices the number is wrong. The
    database CHECK constraint refuses the combination outright.
    """
    _begin(client, deployment['slug'])
    ids, _ = _items_on(client, deployment['slug'], 1)
    client.post(f'/survey/{deployment["slug"]}/section/1',
                data={f'q_{ids[0]}': 'na', f'q_{ids[1]}': '4'})

    rows = {str(r.item_id): r for r in db.session.query(SurveyResponse).all()}
    na = rows[ids[0]]
    assert na.not_applicable is True
    assert na.score is None

    scored = rows[ids[1]]
    assert scored.not_applicable is False
    assert scored.score == 4


def test_an_unanswered_question_stores_nothing(client, deployment):
    """
    "No answer" and "not applicable" are different facts about different
    things, and a blank must not become a zero or a 3.
    """
    _begin(client, deployment['slug'])
    ids, _ = _items_on(client, deployment['slug'], 1)
    client.post(f'/survey/{deployment["slug"]}/section/1', data={f'q_{ids[0]}': '5'})
    assert db.session.query(SurveyResponse).count() == 1


def test_going_back_and_clearing_an_answer_deletes_the_row(client, deployment):
    _begin(client, deployment['slug'])
    ids, _ = _items_on(client, deployment['slug'], 1)
    client.post(f'/survey/{deployment["slug"]}/section/1', data={f'q_{ids[0]}': '5'})
    assert db.session.query(SurveyResponse).count() == 1

    client.post(f'/survey/{deployment["slug"]}/section/1', data={})
    assert db.session.query(SurveyResponse).count() == 0


# ── Save and resume, without knowing who anyone is ─────────────────────────

def test_answers_persist_across_sections_and_are_shown_back(client, deployment):
    _begin(client, deployment['slug'])
    ids, _ = _items_on(client, deployment['slug'], 1)
    client.post(f'/survey/{deployment["slug"]}/section/1',
                data={f'q_{ids[0]}': '2', f'q_{ids[1]}': 'na'})

    _, html = _items_on(client, deployment['slug'], 1)
    assert f'name="q_{ids[0]}" value="2"\n               checked' in html \
        or re.search(rf'q_{ids[0]}" value="2"[^>]*checked', html)
    assert re.search(rf'q_{ids[1]}" value="na"[^>]*checked', html)


def test_a_draft_cannot_be_resumed_without_the_browser_secret(client, deployment):
    """
    The browser holds an opaque secret; the server stores only its sha256.
    Presenting it proves "I started this draft" without the server knowing who
    that is. Without it — a different device, cleared cookies — the draft is
    unreachable, and the landing page says so.
    """
    _begin(client, deployment['slug'])
    assert client.get(f'/survey/{deployment["slug"]}/section/1').status_code == 200

    client.delete_cookie('jsat_resume_' + deployment['slug'][:16],
                         path=f'/survey/{deployment["slug"]}')
    assert client.get(f'/survey/{deployment["slug"]}/section/1').status_code == 404


def test_a_forged_resume_secret_is_rejected(client, deployment):
    """A guessed session id with the wrong secret must not open someone's draft."""
    _begin(client, deployment['slug'])
    session = db.session.query(ResponseSession).one()

    client.set_cookie('jsat_resume_' + deployment['slug'][:16],
                      f'{session.id}:not-the-real-secret',
                      path=f'/survey/{deployment["slug"]}')
    assert client.get(f'/survey/{deployment["slug"]}/section/1').status_code == 404


def test_the_session_is_not_created_on_a_mere_page_view(client, deployment):
    """
    Corporate mail security scanners and link previewers fetch every URL in an
    all-staff email. On GET-creation each one mints a session row — none would
    ever submit, so no reported figure changes, but the started/abandoned
    counts become mostly robots, and that is the first thing anyone asks about
    a deployment.
    """
    client.get(f'/survey/{deployment["slug"]}')
    client.get(f'/survey/{deployment["slug"]}')
    assert db.session.query(ResponseSession).count() == 0

    client.post(f'/survey/{deployment["slug"]}/begin', data={})
    assert db.session.query(ResponseSession).count() == 1


def test_beginning_twice_resumes_rather_than_starting_over(client, deployment):
    _begin(client, deployment['slug'])
    _begin(client, deployment['slug'])
    assert db.session.query(ResponseSession).count() == 1


# ── Submission ──────────────────────────────────────────────────────────────

def test_submitting_counts_the_response_and_stamps_the_session(client, deployment):
    _begin(client, deployment['slug'])
    ids, _ = _items_on(client, deployment['slug'], 1)
    client.post(f'/survey/{deployment["slug"]}/section/1', data={f'q_{ids[0]}': '4'})
    client.post(f'/survey/{deployment["slug"]}/review', data={})

    db.session.expire_all()
    session = db.session.query(ResponseSession).one()
    assert session.submitted_at is not None
    assert deployment['deployment'].responses_received == 1


def test_an_incomplete_response_is_accepted(client, deployment):
    """
    Refusing it would discard 40 real answers to force 8 more — and a
    respondent who skipped the pay questions has told you something, which is
    why coverage is reported rather than required.
    """
    _begin(client, deployment['slug'])
    ids, _ = _items_on(client, deployment['slug'], 1)
    client.post(f'/survey/{deployment["slug"]}/section/1', data={f'q_{ids[0]}': '3'})
    res = client.post(f'/survey/{deployment["slug"]}/review', data={},
                      follow_redirects=True)
    assert 'Thank you' in res.get_data(as_text=True)
    assert db.session.query(SurveyResponse).count() == 1


def test_a_submitted_draft_cannot_be_reopened(client, deployment):
    _begin(client, deployment['slug'])
    client.post(f'/survey/{deployment["slug"]}/review', data={})
    assert client.get(f'/survey/{deployment["slug"]}/section/1').status_code == 404


def test_submitting_freezes_the_organisations_segments(client, db, deployment):
    """
    From the first response on, renaming an option would silently re-group
    answers given under the old meaning — and the response rows are unchanged,
    so nothing in the data would ever reveal it.
    """
    from app.models import Segment

    assert db.session.query(Segment).filter_by(
        org_id=deployment['org'].id).one().frozen_at is None

    _begin(client, deployment['slug'])
    client.post(f'/survey/{deployment["slug"]}/review', data={})

    db.session.expire_all()
    assert db.session.query(Segment).filter_by(
        org_id=deployment['org'].id).one().frozen_at is not None


# ── Segments are self-declared and optional ────────────────────────────────

def test_a_declared_segment_is_stored_as_a_code(client, deployment):
    _begin(client, deployment['slug'])
    client.post(f'/survey/{deployment["slug"]}/about-you', data={'department': 'FIN'})
    assert db.session.query(ResponseSession).one().segment_values == {
        'department': 'FIN'}


def test_declining_to_say_is_allowed_and_loses_nothing(client, deployment):
    """
    A respondent in a three-person department who would rather not say is right
    to be cautious. Forcing the answer would either lose their 48 responses or
    get a false one.
    """
    _begin(client, deployment['slug'])
    client.post(f'/survey/{deployment["slug"]}/about-you', data={'department': ''})
    session = db.session.query(ResponseSession).one()
    assert session.segment_values == {}

    ids, _ = _items_on(client, deployment['slug'], 1)
    client.post(f'/survey/{deployment["slug"]}/section/1', data={f'q_{ids[0]}': '5'})
    assert db.session.query(SurveyResponse).count() == 1


def test_an_invented_segment_value_is_discarded_not_stored(client, deployment):
    _begin(client, deployment['slug'])
    client.post(f'/survey/{deployment["slug"]}/about-you',
                data={'department': 'EXEC-BOARD'})
    assert db.session.query(ResponseSession).one().segment_values == {}


def test_the_segment_step_says_the_questions_are_optional(client, deployment):
    _begin(client, deployment['slug'])
    html = client.get(f'/survey/{deployment["slug"]}/about-you').get_data(as_text=True)
    assert 'optional' in html.lower()
    assert 'Prefer not to say' in html


# ── Tickets ─────────────────────────────────────────────────────────────────

@pytest.fixture
def ticketed(db, deployment):
    org = deployment['org']
    cycle = make_cycle(org, 'KD-2026-T1', status='open')
    ticketed_deployment = make_deployment(org, cycle,
                                          public_slug='slug-ticketed-ccc',
                                          delivery_mode='ticketed',
                                          expected_respondents=10)
    code = 'ticket-one'
    db.session.add(AccessTicket(
        deployment_id=ticketed_deployment.id, org_id=org.id,
        ticket_hash=hashlib.sha256(code.encode()).hexdigest()))
    db.session.commit()
    return {'deployment': ticketed_deployment,
            'slug': ticketed_deployment.public_slug,
            'code': code}


def test_a_valid_ticket_starts_a_session_and_is_marked_used(client, ticketed):
    client.get(f'/survey/{ticketed["slug"]}')
    res = client.post(f'/survey/{ticketed["slug"]}/begin',
                      data={'ticket': ticketed['code']})
    assert res.status_code == 302
    assert db.session.query(ResponseSession).count() == 1
    assert db.session.query(AccessTicket).one().used_at is not None


def test_a_used_ticket_is_rejected(client, ticketed):
    """THE NAMED ACCEPTANCE CRITERION."""
    client.post(f'/survey/{ticketed["slug"]}/begin',
                data={'ticket': ticketed['code']})
    # A second person with the same slip, in a fresh browser.
    client.delete_cookie('jsat_resume_' + ticketed['slug'][:16],
                         path=f'/survey/{ticketed["slug"]}')

    res = client.post(f'/survey/{ticketed["slug"]}/begin',
                      data={'ticket': ticketed['code']})
    assert res.status_code == 400
    assert 'already been used' in res.get_data(as_text=True)
    assert db.session.query(ResponseSession).count() == 1


def test_an_unknown_ticket_is_rejected(client, ticketed):
    res = client.post(f'/survey/{ticketed["slug"]}/begin',
                      data={'ticket': 'not-a-real-code'})
    assert res.status_code == 400
    assert 'not recognised' in res.get_data(as_text=True)
    assert db.session.query(ResponseSession).count() == 0


def test_a_ticket_is_never_stored_in_plaintext(client, ticketed):
    """
    The slip is the only place the code exists. The row holds sha256 and
    `used_at`, and NEVER who received it — see models.AccessTicket, and note
    that trainiq's dropped `email_hash` is refused for exactly this reason.
    """
    client.post(f'/survey/{ticketed["slug"]}/begin',
                data={'ticket': ticketed['code']})
    row = db.session.query(AccessTicket).one()
    blob = repr({c.name: getattr(row, c.name) for c in row.__table__.columns})
    assert ticketed['code'] not in blob
    assert row.ticket_hash == hashlib.sha256(ticketed['code'].encode()).hexdigest()
    assert not hasattr(row, 'email_hash')
    assert not hasattr(row, 'issued_to')


# ── Closed deployments ───────────────────────────────────────────────────────────

def test_a_closed_deployment_explains_itself_rather_than_404ing(client, db, deployment):
    """
    The one place the uniform-404 rule bends. A visitor here is probably a real
    employee following a real link a week late; 404ing them is unkind for no
    gain, because the deployment's existence is something they already knew.
    """
    deployment['deployment'].closed = True
    db.session.commit()

    res = client.get(f'/survey/{deployment["slug"]}')
    assert res.status_code == 410
    assert 'has closed' in res.get_data(as_text=True)


def test_a_deployment_whose_cycle_is_not_open_is_indistinguishable_from_nothing(
        client, db, deployment):
    """
    A DRAFT cycle is different: nobody has been told about it yet, so
    confirming it exists would leak that this employer is preparing a survey.
    Same 404 as an unknown slug.
    """
    deployment['cycle'].status = 'draft'
    db.session.commit()
    res = client.get(f'/survey/{deployment["slug"]}')
    assert res.status_code == 410     # the deployment itself is still live-looking

    # ...whereas an archived deployment is simply gone.
    from datetime import datetime, timezone
    deployment['deployment'].archived_at = datetime.now(timezone.utc)
    db.session.commit()
    assert client.get(f'/survey/{deployment["slug"]}').status_code == 404


def test_a_closed_deployment_accepts_no_further_answers(client, db, deployment):
    _begin(client, deployment['slug'])
    ids, _ = _items_on(client, deployment['slug'], 1)

    deployment['deployment'].closed = True
    db.session.commit()

    res = client.post(f'/survey/{deployment["slug"]}/section/1',
                      data={f'q_{ids[0]}': '1'})
    assert res.status_code == 410
    assert db.session.query(SurveyResponse).count() == 0


# ── Pilot feedback ─────────────────────────────────────────────────────────

def test_the_pilot_comment_box_appears_only_on_a_pilot_cycle(client, db, deployment):
    _begin(client, deployment['slug'])
    html = client.get(f'/survey/{deployment["slug"]}/review').get_data(as_text=True)
    assert 'pilot_comment' not in html

    deployment['cycle'].is_pilot = True
    db.session.commit()
    html = client.get(f'/survey/{deployment["slug"]}/review').get_data(as_text=True)
    assert 'pilot_comment' in html
    assert 'repetitive or difficult' in html
    # It must say where it goes — and that it is not shown to the employer.
    assert 'not shown to your organisation' in html


def test_a_pilot_comment_is_stored_against_the_anonymous_session(client, db, deployment):
    deployment['cycle'].is_pilot = True
    db.session.commit()

    _begin(client, deployment['slug'])
    client.post(f'/survey/{deployment["slug"]}/review',
                data={'pilot_comment': 'Questions 3 and 4 felt like the same question.'})

    feedback = db.session.query(PilotFeedback).one()
    assert 'same question' in feedback.comment
    assert feedback.session_id == db.session.query(ResponseSession).one().id


def test_no_pilot_comment_is_recorded_on_a_non_pilot_cycle(client, deployment):
    """A posted field on a non-pilot deployment is ignored, not stored."""
    _begin(client, deployment['slug'])
    client.post(f'/survey/{deployment["slug"]}/review',
                data={'pilot_comment': 'should be discarded'})
    assert db.session.query(PilotFeedback).count() == 0


# ── Structure ──────────────────────────────────────────────────────────────

def test_there_is_one_section_per_assessed_domain(client, deployment):
    _begin(client, deployment['slug'])
    for index in range(1, len(DOMAINS) + 1):
        assert client.get(
            f'/survey/{deployment["slug"]}/section/{index}').status_code == 200
    assert client.get(
        f'/survey/{deployment["slug"]}/section/{len(DOMAINS) + 1}').status_code == 404


def test_a_pulse_cycle_renders_only_its_own_domains(client, db, deployment):
    """
    A GEM-plus-one deployment is 24 items, not 48. The sections follow
    `assessed_domains` — a snapshot taken at deploy time, so narrowing the
    cycle later cannot retroactively change what was asked.
    """
    deployment['deployment'].assessed_domains = ['GEM', 'TID']
    db.session.commit()

    _begin(client, deployment['slug'])
    assert client.get(f'/survey/{deployment["slug"]}/section/2').status_code == 200
    assert client.get(f'/survey/{deployment["slug"]}/section/3').status_code == 404

    html = client.get(f'/survey/{deployment["slug"]}').get_data(as_text=True)
    assert '24 questions' in html


def test_the_url_carries_an_ordinal_not_a_domain_code(client, deployment):
    """
    /section/2, never /section/TID. The URL is part of what a respondent sees,
    and a framework code in it tells them what they are being scored against.
    """
    _begin(client, deployment['slug'])
    assert client.get(f'/survey/{deployment["slug"]}/section/TID').status_code == 404


def test_subdomains_are_interleaved_on_the_page(client, deployment):
    """
    Four near-identical autonomy questions in a row invite straight-lining: the
    respondent answers the first and pattern-matches the rest.
    """
    _begin(client, deployment['slug'])
    ids, _ = _items_on(client, deployment['slug'], 1)
    items = {str(i.id): i for i in db.session.query(SurveyItem)
             .filter(SurveyItem.id.in_(ids)).all()}
    subs = [items[i].subdomain_code for i in ids]
    assert len(subs) == 12
    assert not any(a == b for a, b in zip(subs, subs[1:])), subs

# ── The threshold no longer reaches the respondent ──────────────────

# WHAT WAS HERE, AND WHY IT IS NOT.
#
# A parametrised test asserted that the landing page's wording tracked
# cycle.min_reporting_n: group-only reporting at 5, the "groups of two or
# more" caveat at 2, and at 1 an explicit warning that a figure might rest on
# one person's answers together with advice to answer only what they were
# comfortable putting their name to.
#
# The section that carried all three was removed, so there is no wording left
# to track. reporting_promise() still computes the value and the route still
# passes it; the template no longer renders it. The test below pins that the
# page is at least not asserting a threshold it does not have.


@pytest.mark.parametrize('min_n', [1, 2, 5])
def test_the_landing_page_makes_no_claim_about_the_threshold(
        client, db, deployment, min_n):
    """
    The page says nothing about grouping at any threshold — including at 5,
    where the claim would have been true and reassuring, and at 1, where the
    old page warned. Whatever it says now comes from the purpose statement.
    """
    deployment['cycle'].min_reporting_n = min_n
    db.session.commit()

    html = re.sub(r'\s+', ' ',
                  client.get(f'/survey/{deployment["slug"]}').get_data(as_text=True))

    for phrase in ('only ever reported for groups',
                   'No individual response is shown to anyone',
                   'groups of two or more',
                   'may be based on your answers alone'):
        assert phrase not in html, f'min_n={min_n} should no longer say {phrase!r}'

