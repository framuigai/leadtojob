"""
The bridge between stored rows and the scoring engine.

tests/test_scoring.py deliberately never touches a database — it pins the
arithmetic against hand-computed numbers. This file pins the other half: that
what comes OUT of the database is what the engine expects to go in, with the
right rows included and the wrong ones left out.

That join is where a correct engine still produces a wrong report.
"""
import math

import pytest

from app import db
from app.analytics import scoring as sc
from app.domains import DOMAINS
from app.models import ResponseSession, SurveyItem, SurveyResponse
from app.provisioning import clone_instrument_to_org
from tests.factories import (
    make_cycle, make_deployment, make_master_instrument, make_org,
)


@pytest.fixture
def wave(db):
    make_master_instrument()
    org = make_org('Kilimo Dairies')
    clone_instrument_to_org(org.id)
    cycle = make_cycle(org, 'KD-2026-H1', status='open')
    deployment = make_deployment(org, cycle, expected_respondents=10)
    db.session.commit()
    return {'org': org, 'cycle': cycle, 'deployment': deployment}


def _items(org_id):
    return (db.session.query(SurveyItem)
            .filter_by(org_id=org_id, version=1)
            .order_by(SurveyItem.sort_order).all())


def _respond(wave, scores_by_cell, submitted=True, void=False,
             segment_values=None):
    """
    Write one session's answers. `scores_by_cell` maps (domain, sub) -> list of
    4 scores, where None means N/A.
    """
    session = ResponseSession(
        deployment_id=wave['deployment'].id, org_id=wave['org'].id,
        segment_values=segment_values or {}, is_void=void,
    )
    if submitted:
        from datetime import datetime, timezone
        session.submitted_at = datetime.now(timezone.utc)
    db.session.add(session)
    db.session.flush()

    for item in _items(wave['org'].id):
        scores = scores_by_cell.get((item.domain_code, item.subdomain_code))
        if scores is None:
            continue
        score = scores[item.ordinal - 1]
        db.session.add(SurveyResponse(
            session_id=session.id, deployment_id=wave['deployment'].id,
            org_id=wave['org'].id, item_id=item.id,
            score=None if score is None else score,
            not_applicable=(score is None),
        ))
    db.session.flush()
    return session


def _all_fours(domain='BIT'):
    from app.domains import SUBDOMAINS
    return {(domain, s): [4, 4, 4, 4] for s in SUBDOMAINS[domain]}


# ── What comes out ──────────────────────────────────────────────────────────

def test_a_stored_response_round_trips_into_a_profile(wave):
    _respond(wave, _all_fours('BIT'))
    db.session.commit()

    profiles = sc.load_profiles(db.session, [wave['deployment'].id])
    assert len(profiles) == 1
    assert profiles[0].domain_scores['BIT'] == 4.0


def test_na_rows_survive_the_journey_as_na_and_not_as_a_score(wave):
    """
    The join is where a null score could quietly become a 0 — the single most
    consequential thing that can go wrong between the table and the engine,
    because a 0 on a 1-5 scale is worse than the worst real answer.
    """
    from app.domains import SUBDOMAINS
    _respond(wave, {('BIT', s): [4, 4, None, None] for s in SUBDOMAINS['BIT']})
    db.session.commit()

    answers = sc.load_answers(db.session, [
        s.id for s in db.session.query(ResponseSession).all()])
    flat = [a for rows in answers.values() for a in rows]

    na_rows = [a for a in flat if a.not_applicable]
    assert len(na_rows) == 6
    assert all(a.score is None for a in na_rows)
    assert all(sc.item_value(a) is None for a in na_rows)

    profiles = sc.load_profiles(db.session, [wave['deployment'].id])
    assert profiles[0].domain_scores['BIT'] == 4.0      # not 2.0, not 3.0


def test_the_reverse_flag_travels_with_the_item(wave):
    """
    `is_reverse_scored` lives on survey_items, not on the response. If the
    loader dropped it, a reverse item would score in the wrong direction and
    nothing downstream could detect it.
    """
    item = _items(wave['org'].id)[0]
    item.is_reverse_scored = True
    db.session.flush()

    _respond(wave, {(item.domain_code, item.subdomain_code): [5, 5, 5, 5]})
    db.session.commit()

    answers = sc.load_answers(db.session, [
        s.id for s in db.session.query(ResponseSession).all()])
    flat = [a for rows in answers.values() for a in rows]
    flipped = [a for a in flat if a.item_code == item.item_code]
    assert flipped and flipped[0].is_reverse_scored is True
    assert sc.item_value(flipped[0]) == 1.0


# ── What is left out ────────────────────────────────────────────────────────

def test_an_unsubmitted_draft_is_not_counted(wave):
    """
    A partial response is not a response. Counting one would move a reported
    figure every time somebody opened the form and wandered off — and a figure
    that drifts for no visible reason is one nobody can defend.
    """
    _respond(wave, _all_fours('BIT'), submitted=False)
    db.session.commit()
    assert sc.load_profiles(db.session, [wave['deployment'].id]) == []


def test_a_voided_session_is_excluded(wave):
    """
    That is what voiding is for — a test submission, or a straight line through
    48 items in 40 seconds. The exclusion is visible in the audit log rather
    than silently changing a count.
    """
    _respond(wave, _all_fours('BIT'))
    _respond(wave, _all_fours('BIT'), void=True)
    db.session.commit()

    profiles = sc.load_profiles(db.session, [wave['deployment'].id])
    assert len(profiles) == 1


def test_another_deployments_responses_do_not_leak_in(wave, db):
    """
    Two waves in one organisation must not pool. A cross-wave total would mix
    two populations measured at different times and report it as one.
    """
    other_cycle = make_cycle(wave['org'], 'KD-2026-H2', status='open')
    other = make_deployment(wave['org'], other_cycle, public_slug='other-slug')
    db.session.flush()

    _respond(wave, _all_fours('BIT'))
    db.session.commit()

    assert len(sc.load_profiles(db.session, [wave['deployment'].id])) == 1
    assert sc.load_profiles(db.session, [other.id]) == []


# ── Through to a group result ──────────────────────────────────────────────

def test_the_weighting_rule_holds_through_the_database(wave):
    """
    The N/A weighting test from test_scoring.py, re-run end to end — because
    the rule could hold in the engine and still be lost in the query.
    """
    from app.domains import SUBDOMAINS
    _respond(wave, {('PIE', s): [2, 2, 2, 2] for s in SUBDOMAINS['PIE']})
    _respond(wave, {('PIE', s): [2, 2, None, None] for s in SUBDOMAINS['PIE']})
    db.session.commit()

    profiles = sc.load_profiles(db.session, [wave['deployment'].id])
    result = sc.group_result(profiles, 'PIE', min_n=1)
    assert result.n == 2
    assert result.mean == 2.0


def test_a_segment_breakdown_works_from_stored_declarations(wave):
    """
    Grouped on the stored CODE, so a later rename of the option label cannot
    re-group a historical response.
    """
    for _ in range(3):
        _respond(wave, _all_fours('BIT'), segment_values={'department': 'FIN'})
    for _ in range(2):
        _respond(wave, _all_fours('BIT'), segment_values={'department': 'OPS'})
    _respond(wave, _all_fours('BIT'), segment_values={})
    db.session.commit()

    profiles = sc.load_profiles(db.session, [wave['deployment'].id])
    out = sc.segment_breakdown(profiles, ('BIT', 'T'), 'department', min_n=1)

    assert out['FIN'].n == 3
    assert out['OPS'].n == 2
    assert out[None].n == 1


def test_suppression_bites_on_real_rows_at_a_real_threshold(wave):
    """
    Threshold 1 is the local default, so this passes 5 explicitly — otherwise
    the suppression path would never be exercised against the database at all.
    """
    for _ in range(3):
        _respond(wave, _all_fours('BIT'), segment_values={'department': 'FIN'})
    for _ in range(20):
        _respond(wave, _all_fours('BIT'), segment_values={'department': 'OPS'})
    for _ in range(20):
        _respond(wave, _all_fours('BIT'), segment_values={'department': 'SALES'})
    db.session.commit()

    profiles = sc.load_profiles(db.session, [wave['deployment'].id])
    out = sc.segment_breakdown(profiles, ('BIT', 'T'), 'department', min_n=5)

    hidden = [v for v in out.values() if isinstance(v, sc.Suppressed)]
    assert isinstance(out['FIN'], sc.Suppressed)
    assert len(hidden) >= 2                     # FIN alone would be recoverable
    assert sum(v.n for v in hidden) >= 5


def test_the_cycles_frozen_scoring_method_is_what_gets_used(wave):
    """
    `measurement_cycles.scoring_method` is frozen per wave and printed in every
    report. The loader must honour it rather than the module default, or the
    figure and the stated method disagree.
    """
    from app.domains import SUBDOMAINS
    _respond(wave, {('TID', 'T'): [5, 5, 5, 5], ('TID', 'I'): [5, 5, 5, 5],
                    ('TID', 'D'): [1, 1, None, None]})
    db.session.commit()

    by_sub = sc.load_profiles(db.session, [wave['deployment'].id],
                              method=sc.MEAN_OF_SUBDOMAINS)
    by_item = sc.load_profiles(db.session, [wave['deployment'].id],
                               method=sc.MEAN_OF_ITEMS)

    assert math.isclose(by_sub[0].domain_scores['TID'], 11 / 3)
    assert math.isclose(by_item[0].domain_scores['TID'], 4.2)
