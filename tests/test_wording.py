"""
THE GUARDS ARE THEMSELVES TESTED.

tests/wording.py polices the language of every generated narrative and every
rendered report. A guard that returns an empty list for everything would pass
every test that uses it while protecting nothing — so this file checks that
each guard CATCHES REAL VIOLATIONS as well as PERMITTING REAL SAFEGUARDS.

The sibling product learned this the hard way: the naive substring version was
written first, it flagged the reports' own caveats as violations, and the fix
that was reached for first was deleting the caveats.
"""
import pytest

from tests import wording


# ── Causal claims (§10.4, §17.8) ───────────────────────────────────────────

REAL_CAUSAL_VIOLATIONS = [
    'Low autonomy causes dissatisfaction across the organisation.',
    'The fall in Trust is driven by recent management changes.',
    'Employees are dissatisfied because of their compensation.',
    'Poor communication leads to lower belonging scores.',
    'The restructure resulted in a decline in Emotional Experience.',
    'Low pay is responsible for the drop in overall satisfaction.',
    'Weak scores on Purpose and Impact explain the retention problem.',
]

LEGITIMATE_NEGATED_FORMS = [
    'This deployment shows associations between measures, not causes.',
    'It does not show that Trust causes higher or lower satisfaction.',
    'These patterns do not, by themselves, establish that one factor caused '
    'another.',
    'The data cannot tell us what causes this difference.',
    'This is an association and should not be read as the reason for the '
    'difference.',
    'Nothing here establishes that autonomy drives satisfaction.',
]


@pytest.mark.parametrize('sentence', REAL_CAUSAL_VIOLATIONS)
def test_the_causal_guard_catches_a_real_violation(sentence):
    """Without this, an empty-returning guard would pass everything."""
    assert wording.causal_hits(sentence), sentence


@pytest.mark.parametrize('sentence', LEGITIMATE_NEGATED_FORMS)
def test_the_causal_guard_permits_the_required_safeguards(sentence):
    """
    These sentences are REQUIRED by §10.4. A guard that flags them trains
    whoever hits the failure to delete the safeguard, which is worse than
    having no guard at all.
    """
    assert wording.causal_hits(sentence) == [], sentence


def test_the_causal_guard_reports_which_sentence_offended():
    text = ('Overall Satisfaction scores 3.2. Low autonomy causes this. '
            'Trust scores 2.8.')
    hits = wording.causal_hits(text)
    assert len(hits) == 1
    term, sentence = hits[0]
    assert term == 'causes'
    assert sentence == 'Low autonomy causes this.'


def test_a_violation_in_one_sentence_is_not_excused_by_a_caveat_in_another():
    """
    THE CASE A WHOLE-DOCUMENT NEGATION CHECK WOULD MISS.

    A report that asserts causation in paragraph two and disclaims it in
    paragraph nine has still asserted it. Negation is judged per sentence.
    """
    text = ('Low autonomy causes dissatisfaction. '
            'These patterns do not establish that one factor caused another.')
    assert wording.causal_hits(text)


# ── Verdict and band labels (§10.5) ────────────────────────────────────────

REAL_VERDICT_VIOLATIONS = [
    'Trust is a critical gap for this organisation.',
    'Scores on Belonging are poor.',
    'This is a concerning result for the leadership team.',
    'Relationships Satisfaction is healthy.',
    'The Reward domain is failing.',
]

LEGITIMATE_VERDICT_FORMS = [
    'Score bands have not been validated for this instrument, so no result is '
    'described as low, moderate or high.',
    'This is not a critical finding or any other graded label.',
    'Results are presented as scores and distributions rather than as '
    'excellent or poor.',
]


@pytest.mark.parametrize('sentence', REAL_VERDICT_VIOLATIONS)
def test_the_verdict_guard_catches_a_real_violation(sentence):
    assert wording.verdict_hits(sentence), sentence


@pytest.mark.parametrize('sentence', LEGITIMATE_VERDICT_FORMS)
def test_the_verdict_guard_permits_the_required_disclaimers(sentence):
    assert wording.verdict_hits(sentence) == [], sentence


def test_lower_priority_is_not_a_verdict():
    """
    "Lower priority" is a reading order and appears in every priority view. A
    substring scan for "low" flags it; a whole-word scan does not.
    """
    assert wording.verdict_hits('Autonomy and Control: lower priority.') == []


# ── Stress vocabulary (§6.4) ───────────────────────────────────────────────

REAL_STRESS_VIOLATIONS = [
    'The low Appropriate Challenge score suggests employees are overwhelmed.',
    'This points to workplace stress across the team.',
    'Staff are suffering burnout.',
    'The team is under pressure.',
]

LEGITIMATE_STRESS_FORMS = [
    'A lower score here does not indicate stress or an unmanageable workload; '
    'it may equally mean work is not sufficiently stretching.',
    'This subdomain is not a measure of pressure or overload.',
]


@pytest.mark.parametrize('sentence', REAL_STRESS_VIOLATIONS)
def test_the_stress_guard_catches_a_real_violation(sentence):
    assert wording.stress_hits(sentence), sentence


@pytest.mark.parametrize('sentence', LEGITIMATE_STRESS_FORMS)
def test_the_stress_guard_permits_the_required_clarification(sentence):
    """
    §6.4's clarification NAMES the readings it rules out — it has to, or it
    clarifies nothing. The guard must allow the negated form.
    """
    assert wording.stress_hits(sentence) == [], sentence


def test_the_shipped_daunting_caveat_passes_its_own_guard():
    """The constant in app/domains.py is rendered in reports; it must pass."""
    from app import domains
    assert wording.stress_hits(domains.DAUNTING_CAVEAT) == []


# ── Pointing at an individual (§12.3, §13.9) ───────────────────────────────

REAL_IDENTITY_VIOLATIONS = [
    'Speak to the person who rated Trust lowest.',
    'One respondent reported serious concerns about management.',
    'The employee who gave this answer should be approached.',
]


@pytest.mark.parametrize('sentence', REAL_IDENTITY_VIOLATIONS)
def test_the_identity_guard_catches_a_real_violation(sentence):
    assert wording.identity_hits(sentence), sentence


def test_the_identity_guard_permits_ordinary_group_language():
    for sentence in [
        'Employees in the lowest third on General Job Satisfaction score 2.3.',
        'No individual response is shown to anyone.',
        'Respondents who declined to say are reported as their own group.',
    ]:
        assert wording.identity_hits(sentence) == [], sentence


# ── The shipped constants pass their own guards ────────────────────────────

def test_every_caveat_the_product_ships_passes_every_guard():
    """
    The constants in app/domains.py are rendered verbatim into reports. If one
    of them tripped a guard, every populated-render test would fail and the
    obvious fix would be to weaken the guard.
    """
    from app import domains

    for name in ('ASSOCIATION_CAVEAT', 'PERCEPTION_CAVEAT', 'TREND_CAVEAT',
                 'BANDS_DEFERRED_NOTE', 'DAUNTING_CAVEAT',
                 'INTERVENTION_MAP_NOTE'):
        text = getattr(domains, name)
        hits = wording.all_hits(text)
        assert all(not v for v in hits.values()), (name, hits)


def test_the_guards_are_not_vacuous():
    """
    The meta-test. A guard that always returns [] would pass every "permits"
    test above and protect nothing, and this is the one test that would fail.
    """
    assert wording.causal_hits('Low pay causes dissatisfaction.')
    assert wording.verdict_hits('Trust is poor.')
    assert wording.stress_hits('The team is overwhelmed.')
    assert wording.identity_hits('One respondent reported this.')
