"""
The framework's invariants. Pure Python — no app context, no database.

These are cheap and they catch the class of error that is otherwise invisible
until a report is wrong: a subdomain silently carrying three items, a duplicate
item code, a label that drifted out of the single source of truth.
"""
import pytest

from app import domains


# ── Shape (spec §4) ─────────────────────────────────────────────────────────

def test_four_domains_twelve_subdomains_forty_eight_items():
    assert domains.DOMAINS == ['GEM', 'TID', 'BIT', 'PIE']
    assert len(domains.subdomain_pairs()) == 12
    assert domains.ITEMS_PER_SUBDOMAIN == 4
    assert domains.ITEMS_PER_DOMAIN == 12
    assert domains.ITEMS_TOTAL == 48


def test_every_domain_has_exactly_three_subdomains():
    for code in domains.DOMAINS:
        assert len(domains.SUBDOMAINS[code]) == 3, code


def test_item_codes_are_unique_and_well_formed():
    codes = domains.all_item_codes()
    assert len(codes) == 48
    assert len(set(codes)) == 48, 'duplicate item code'
    for code in codes:
        domain, sub, ordinal = domains.parse_item_code(code)
        assert domain in domains.DOMAINS
        assert sub in domains.SUBDOMAINS[domain]
        assert 1 <= ordinal <= 4


def test_item_code_is_fully_qualified():
    """
    Subdomain letters repeat across domains, so a code must carry its domain.

    'I' alone is Purpose and Impact, Positive Interactions AND Industry
    Comparison. A code of 'I-01' would join three different items together and
    the resulting number would look entirely plausible.
    """
    assert domains.item_code('TID', 'I', 1) == 'TID-I-01'
    assert domains.item_code('BIT', 'I', 1) == 'BIT-I-01'
    assert domains.item_code('PIE', 'I', 1) == 'PIE-I-01'
    assert len({domains.item_code(d, 'I', 1) for d in ('TID', 'BIT', 'PIE')}) == 3


def test_parse_item_code_rejects_an_unknown_pair():
    # 'GEM-T-01' is well-shaped but wrong: T is not a GEM subdomain.
    with pytest.raises(ValueError):
        domains.parse_item_code('GEM-T-01')


def test_subdomain_lookups_are_keyed_on_the_pair_not_the_letter():
    assert domains.subdomain_label('TID', 'T') == 'Autonomy and Control'
    assert domains.subdomain_label('BIT', 'T') == 'Trust'
    assert domains.subdomain_label('GEM', 'E') == 'Emotional Experience'
    assert domains.subdomain_label('PIE', 'E') == 'Expectations'


def test_every_pair_has_a_label_and_a_definition():
    for pair in domains.subdomain_pairs():
        assert domains.SUBDOMAIN_LABELS.get(pair), pair
        assert domains.SUBDOMAIN_DEFINITIONS.get(pair), pair


# ── GEM is the outcome (spec §1, §10.3, §10.4) ──────────────────────────────

def test_gem_is_the_outcome_and_is_not_also_a_diagnostic():
    assert domains.OUTCOME_DOMAIN == 'GEM'
    assert domains.DIAGNOSTIC_DOMAINS == ['TID', 'BIT', 'PIE']
    assert domains.OUTCOME_DOMAIN not in domains.DIAGNOSTIC_DOMAINS


# ── The response scale (spec §9.1) ──────────────────────────────────────────

def test_agreement_scale_is_five_points_in_order():
    labels = domains.anchor_labels('agreement')
    assert labels == [
        'Strongly disagree',
        'Disagree',
        'Neither agree nor disagree',
        'Agree',
        'Strongly agree',
    ]
    assert domains.anchor_label_map('agreement')[1] == 'Strongly disagree'
    assert domains.anchor_label_map('agreement')[5] == 'Strongly agree'


def test_not_applicable_is_not_part_of_the_numeric_scale():
    """
    Spec §9.1: N/A responses "should not be treated as neutral scores".

    So it is not a sixth anchor and not a value in the 1-5 map. It is a
    first-class boolean on the response row, excluded from numerator and
    denominator alike.
    """
    labels = domains.anchor_labels('agreement')
    assert len(labels) == 5
    assert domains.NOT_APPLICABLE_LABEL not in labels
    assert domains.NOT_APPLICABLE_LABEL not in domains.anchor_label_map('agreement').values()


# ── Bands (spec §10.5) ──────────────────────────────────────────────────────

def test_the_deferred_bands_note_says_there_are_no_bands():
    """
    This is a specification requirement, not a preference, so it is pinned.

    There is no band vocabulary in this product and no switch to turn one on:
    §10.5 requires pilot evidence first, and a colour on a dashboard is not
    that evidence.
    """
    assert 'not been validated' in domains.BANDS_DEFERRED_NOTE
    assert not hasattr(domains, 'BANDS_VALIDATED')


# ── 'Daunting' (spec §6.4) ──────────────────────────────────────────────────

def test_daunting_is_never_the_client_facing_label():
    """
    §6.4: "It must not be interpreted as excessive pressure, unmanageable
    workload or persistent stress."

    The letter is a mnemonic. The label a client reads is the construct.
    """
    label = domains.subdomain_label(*domains.DAUNTING)
    assert label == 'Appropriate Challenge and Development'
    for forbidden in ('daunting', 'stress', 'overload', 'pressure', 'burnout'):
        assert forbidden not in label.lower()


def test_daunting_caveat_states_both_readings():
    """A low score can mean under-stretched as easily as overwhelmed."""
    caveat = domains.DAUNTING_CAVEAT.lower()
    assert 'stretching' in caveat
    assert 'exceed' in caveat


# ── The §11 map ─────────────────────────────────────────────────────────────

def test_intervention_map_covers_every_subdomain():
    for pair in domains.subdomain_pairs():
        entry = domains.intervention_entry(*pair)
        assert entry, f'§11 row missing for {pair}'
        assert entry['finding']
        assert entry['areas']
        assert entry['categories']
        assert entry['types']


def test_every_assigned_type_is_in_the_vocabulary():
    for pair in domains.subdomain_pairs():
        for t in domains.intervention_entry(*pair)['types']:
            assert t in domains.INTERVENTION_TYPES, (pair, t)
            assert t in domains.INTERVENTION_TYPE_LABELS


def test_no_reward_subdomain_can_produce_a_training_recommendation():
    """
    THE LOAD-BEARING ONE. Spec §8.5:

        "Training should not be treated as a substitute for addressing
         substantiated compensation inequities or structural reward problems."

    app/catalogue.py filters candidate interventions against these type lists
    before anything reaches a report or the model, so this assertion is what
    makes §8.5 structural rather than a polite instruction in a prompt.
    """
    for sub in domains.SUBDOMAINS['PIE']:
        assert not domains.allows_training('PIE', sub), f'PIE-{sub}'


def test_training_is_available_where_the_spec_does_allow_it():
    """
    The complement of the test above — without it, a change that removed
    `training` from every row would leave both tests green and quietly disable
    every capability recommendation in the product.
    """
    assert domains.allows_training('TID', 'D')      # "capability development"
    assert domains.allows_training('BIT', 'I')      # "manager development"


def test_the_association_caveat_is_the_specs_own_sentence():
    """
    §10.4, verbatim. It is rendered beneath every association output from this
    one constant, so a new chart cannot omit it by forgetting it.
    """
    assert domains.ASSOCIATION_CAVEAT == (
        'Such patterns indicate associations and areas for investigation. They '
        'do not, by themselves, establish that one factor caused another.'
    )
