"""
Domains, subdomains, response anchors and the §11 diagnostic-to-intervention map.

SINGLE SOURCE OF TRUTH for the framework. Pure Python — no Flask, no DB — so it
stays importable from a script, a test or the scoring engine without an app
context.

────────────────────────────────────────────────────────────────────────────────
UNLIKE THE SIBLING PRODUCTS, THE FRAMEWORK HERE IS CLIENT-FACING.

GEM / TID / BIT / PIE and the twelve subdomain names are the product's own
vocabulary and appear in reports and on admin screens (spec §4, §18). Promo's
"MESS is proprietary, never client-facing" rule does NOT transfer.

What must not leak is different, and narrower:

  * no `item_code`, `subdomain_code` or `domain_code` on the ANONYMOUS SURVEY
    FORM — the respondent must not be able to infer which construct an item
    serves, and nothing on that page carries leadTo chrome (plan §8);
  * no band or verdict label anywhere (§10.5);
  * `Daunting` never rendered or narrated as pressure, overload or stress
    (§6.4).

tests/test_privacy_and_wording.py is what keeps those true.
────────────────────────────────────────────────────────────────────────────────
"""
import uuid

# Fixed UUID of the master template org created by seed_templates.py. Client
# orgs clone their survey items from here on first deployment and are then
# frozen independently forever.
MASTER_ORG_ID = uuid.UUID('00000000-0000-0000-0000-000000000001')


# ── The four domains (spec §4) ───────────────────────────────────────────────
#
# Codes are the canonical key. Never group, join or filter on a domain's label.

GEM = 'GEM'   # Overall Satisfaction        — THE OUTCOME DOMAIN
TID = 'TID'   # Task Satisfaction           — diagnostic
BIT = 'BIT'   # Relationships Satisfaction  — diagnostic
PIE = 'PIE'   # Reward Satisfaction         — diagnostic

DOMAINS = [GEM, TID, BIT, PIE]

DOMAIN_LABELS = {
    GEM: 'Overall Satisfaction',
    TID: 'Task Satisfaction',
    BIT: 'Relationships Satisfaction',
    PIE: 'Reward Satisfaction',
}

# GEM is the outcome; the other three are reported IN RELATION TO it (§1, §10.4).
# Held as constants because the distinction drives the whole analysis layer: a
# diagnostic domain is never presented as an outcome, and GEM is never
# presented as a driver of itself.
OUTCOME_DOMAIN = GEM
DIAGNOSTIC_DOMAINS = [TID, BIT, PIE]

# Verbatim from spec §1 / §5.1-§8.1. Shown as the section intro on the survey
# form, so the respondent knows what they are being asked about, and in report
# domain headers. Quote these — do not paraphrase them.
DOMAIN_DEFINITIONS = {
    GEM: (
        "the employee's overall evaluation of their job experience."
    ),
    TID: (
        "the employee's experience of autonomy, purpose and appropriate "
        "challenge in performing their work."
    ),
    BIT: (
        "the employee's experience of belonging, positive workplace "
        "interactions and trust."
    ),
    PIE: (
        "the employee's satisfaction with financial compensation in relation "
        "to peers, industry expectations and personal financial expectations."
    ),
}

# Spec §8.1 states this as a SCOPE LIMITATION, so it is printed, not implied:
# PIE measures financial compensation only. A reader who takes a low PIE score
# as "our rewards are bad" has over-read it, and the report must say so.
DOMAIN_SCOPE_NOTES = {
    PIE: (
        'This domain measures satisfaction with financial compensation. It does '
        'not comprehensively measure benefits, recognition, career '
        'opportunities, flexibility or other non-financial rewards.'
    ),
}


# ── The twelve subdomains (spec §5-§8) ───────────────────────────────────────
#
# SUBDOMAIN CODES REPEAT ACROSS DOMAINS. 'I' is in TID, BIT and PIE; 'T' is in
# TID and BIT; 'E' is in GEM and PIE. The key is therefore ALWAYS the PAIR
# (domain_code, subdomain_code), never the letter alone. Every dict below is
# keyed by the pair for exactly that reason, and anything that accepts a bare
# subdomain letter is a bug waiting for a wrong join.

SUBDOMAINS = {
    GEM: ['G', 'E', 'M'],
    TID: ['T', 'I', 'D'],
    BIT: ['B', 'I', 'T'],
    PIE: ['P', 'I', 'E'],
}

SUBDOMAIN_LABELS = {
    (GEM, 'G'): 'General Job Satisfaction',
    (GEM, 'E'): 'Emotional Experience',
    (GEM, 'M'): 'Match with Overall Expectations',
    (TID, 'T'): 'Autonomy and Control',
    (TID, 'I'): 'Purpose and Impact',
    (TID, 'D'): 'Appropriate Challenge and Development',
    (BIT, 'B'): 'Belonging',
    (BIT, 'I'): 'Positive Interactions',
    (BIT, 'T'): 'Trust',
    (PIE, 'P'): 'Peer Comparison',
    (PIE, 'I'): 'Industry Comparison',
    (PIE, 'E'): 'Expectations',
}

# Verbatim definitions from spec §5.2-§8.4.
SUBDOMAIN_DEFINITIONS = {
    (GEM, 'G'): (
        "The employee's considered evaluation of the job as a whole, including "
        "whether they regard it as a satisfactory working arrangement for "
        "themselves."
    ),
    (GEM, 'E'): (
        'The feelings and affective reactions that employees experience in '
        'connection with their work.'
    ),
    (GEM, 'M'): (
        "The extent to which the employee's actual job experience aligns with "
        "their broader expectations of the role and working arrangement."
    ),
    (TID, 'T'): (
        'The degree to which employees experience appropriate discretion and '
        'control over how they perform their work.'
    ),
    (TID, 'I'): (
        'The extent to which employees understand the significance of their '
        'work and perceive it as contributing to meaningful outcomes.'
    ),
    (TID, 'D'): (
        'The extent to which work provides a suitable level of challenge, '
        'opportunities to apply capabilities and opportunities to develop '
        'competence.'
    ),
    (BIT, 'B'): (
        'The extent to which employees feel accepted, included and recognized '
        'as legitimate members of their workplace community.'
    ),
    (BIT, 'I'): (
        'The quality of day-to-day interpersonal interactions among employees '
        'and the people with whom they work.'
    ),
    (BIT, 'T'): (
        'The degree to which employees perceive honesty, reliability and '
        'psychological safety in their workplace relationships.'
    ),
    (PIE, 'P'): (
        "The employee's perceived satisfaction with their financial "
        "compensation in relation to people they consider comparable to "
        "themselves."
    ),
    (PIE, 'I'): (
        "The employee's perceived satisfaction with financial compensation in "
        "relation to what they believe is typical for comparable work in their "
        "industry."
    ),
    (PIE, 'E'): (
        "The employee's satisfaction with their financial compensation in "
        "relation to their personal financial compensation expectations."
    ),
}

# ── The 'Daunting' hazard (spec §6.4) ────────────────────────────────────────
#
# 'Daunting' is a MNEMONIC LETTER, not a meaning. The subdomain measures
# APPROPRIATE CHALLENGE AND DEVELOPMENT. The spec's wording is unusually
# direct: it "must not be interpreted as excessive pressure, unmanageable
# workload or persistent stress."
#
# A LOW score on it can mean UNDER-STRETCHED just as easily as OVERWHELMED, and
# nothing in the data distinguishes those two. So the client-facing label never
# contains the word (see SUBDOMAIN_LABELS above: 'Appropriate Challenge and
# Development'), the AI system prompt forbids the reading, and
# test_privacy_and_wording.py asserts the subdomain never co-occurs with
# stress/overload vocabulary in a rendered report.
DAUNTING = (TID, 'D')

DAUNTING_CAVEAT = (
    'This subdomain measures whether work offers a suitable level of challenge '
    'and opportunity to develop. A lower score may indicate work that is not '
    'sufficiently stretching, or work whose demands exceed current capability. '
    'The item-level results indicate which, and the distinction should be '
    'investigated rather than assumed.'
)

ITEMS_PER_SUBDOMAIN = 4                                              # §4
SUBDOMAINS_PER_DOMAIN = 3                                            # §4
ITEMS_PER_DOMAIN = ITEMS_PER_SUBDOMAIN * SUBDOMAINS_PER_DOMAIN       # 12
ITEMS_TOTAL = ITEMS_PER_DOMAIN * len(DOMAINS)                        # 48


def subdomain_label(domain_code: str, subdomain_code: str, default: str = '') -> str:
    """Client-facing name of a (domain, subdomain) cell. Always pass BOTH."""
    return SUBDOMAIN_LABELS.get((domain_code, subdomain_code), default)


def subdomain_definition(domain_code: str, subdomain_code: str, default: str = '') -> str:
    return SUBDOMAIN_DEFINITIONS.get((domain_code, subdomain_code), default)


def subdomain_pairs():
    """Every (domain_code, subdomain_code) pair, in presentation order."""
    return [(d, s) for d in DOMAINS for s in SUBDOMAINS[d]]


def item_code(domain_code: str, subdomain_code: str, ordinal: int) -> str:
    """
    The stored, fully-qualified item key: 'GEM-G-01' through 'PIE-E-04'.

    Fully qualified because subdomain letters repeat across domains — 'I-01'
    alone would name three different items. Codes are STORED and are the join
    key for everything downstream, so this format is fixed forever.
    """
    return f'{domain_code}-{subdomain_code}-{ordinal:02d}'


def all_item_codes() -> list:
    return [
        item_code(d, s, n)
        for d in DOMAINS
        for s in SUBDOMAINS[d]
        for n in range(1, ITEMS_PER_SUBDOMAIN + 1)
    ]


def parse_item_code(code: str) -> tuple:
    """'GEM-G-01' -> ('GEM', 'G', 1). Raises on anything malformed."""
    domain, subdomain, ordinal = code.split('-')
    if domain not in DOMAINS or subdomain not in SUBDOMAINS[domain]:
        raise ValueError(f'Unknown domain/subdomain pair in item code: {code!r}')
    return domain, subdomain, int(ordinal)


# ── Response scale (spec §9.1) ───────────────────────────────────────────────
#
# SINGLE SOURCE OF TRUTH. The form and the report charts both import this.
# trainiq's confidence scale drifted between two private copies and reports
# mislabelled answers by one step for a whole release.

AGREEMENT = 'agreement'

ANCHOR_LABELS = {
    AGREEMENT: [
        'Strongly disagree',
        'Disagree',
        'Neither agree nor disagree',
        'Agree',
        'Strongly agree',
    ],
}

ANCHOR_TYPES = list(ANCHOR_LABELS)
DEFAULT_ANCHOR = AGREEMENT

# ── 'Not applicable' is NOT a neutral score (spec §9.1, explicit) ────────────
#
# It is a first-class boolean on survey_responses, excluded from BOTH numerator
# and denominator at every level of aggregation, and counted and reported as
# COVERAGE. Never encode it as 0, 3 or 6 — a sentinel reaches an AVG()
# eventually and corrupts a result silently, with nothing to show for it.
#
# A high N/A rate is itself a finding, not noise: 30% N/A on a Peer Comparison
# item means people do not know what their peers earn, which is a fact about
# pay transparency worth naming.
NOT_APPLICABLE_LABEL = 'Not applicable / Unable to assess'
NOT_APPLICABLE_SHORT = 'N/A'


def anchor_labels(anchor_type: str) -> list:
    return list(ANCHOR_LABELS.get(anchor_type, ANCHOR_LABELS[DEFAULT_ANCHOR]))


def anchor_label_map(anchor_type: str) -> dict:
    """{1: 'Strongly disagree', ... 5: 'Strongly agree'}"""
    return {i + 1: label for i, label in enumerate(anchor_labels(anchor_type))}


# ── Bands: DELIBERATELY NOT VALIDATED (spec §10.5) ───────────────────────────
#
# §10.5 is a prohibition, not a preference: "Universal score bands should not be
# treated as validated merely because the scale ranges from 1 to 5. Any labels
# such as 'low,' 'moderate' or 'high' should be developed through pilot
# evidence, psychometric evaluation and practical testing."
#
# So while this is False — v1, and until the §15 pilot produces evidence:
#
#   * reports show score + distribution + comparison to the org mean and to the
#     previous cycle, and NO word like Low / Moderate / High / Critical / Gap;
#   * colour encodes the RESPONSE CATEGORY (the 1-5 anchors, on a diverging
#     unfavourable-to-favourable ramp), never a judgement of the score. This
#     keeps the visual language of the sibling products while refusing the one
#     thing the spec forbids;
#   * the eye is directed at RELATIVE comparison — this group vs org, this cycle
#     vs last — because relative statements are defensible before validation
#     and absolute ones are not.
#
BANDS_DEFERRED_NOTE = (
    'Score bands have not been validated for this instrument. Results are '
    'presented as scores and response distributions, with comparisons, rather '
    'than as labels such as low, moderate or high.'
)


# ── The §10.4 caveat — ONE constant, imported everywhere ─────────────────────
#
# Rendered beneath every association output without exception. Held here rather
# than written into each template so a NEW chart cannot omit it by forgetting
# it: there is one string, and the test asserts its presence on every
# association figure.
ASSOCIATION_CAVEAT = (
    'Such patterns indicate associations and areas for investigation. They do '
    'not, by themselves, establish that one factor caused another.'
)

# Spec §8.5 and §17.8, for the reward domain especially: a perception is a
# diagnostic signal, not a market fact.
PERCEPTION_CAVEAT = (
    'Employee perceptions are important diagnostic signals, but they do not '
    'establish that compensation is objectively fair, unfair, above market or '
    'below market. Where such conclusions are needed, the organization should '
    'use relevant compensation records, role comparisons and reliable market '
    'benchmarks.'
)

# Spec §16, for the trend section.
TREND_CAVEAT = (
    'Improvement after an intervention does not by itself prove that the '
    'intervention caused the change. Consider other explanations, timing, '
    'sample composition and the strength of the evidence.'
)


# A cycle may be reported while it is still collecting — trainiq's
# `is_provisional` band, adopted here for consistency across the three
# products. The figures are real but not settled, and the note says so on the
# cover of every PDF and in every spreadsheet header, because a PDF outlives
# the screen that explained it.
PROVISIONAL_NOTE = (
    'This cycle is still collecting responses. The figures are correct as at '
    'the date above and will change as more people answer, so this report is '
    'an interim read rather than the record of the cycle. Do not circulate it '
    'as a final result, and do not compare two interim reads of the same '
    'group: the difference between them is not a change of opinion.'
)


# ── Intervention types ───────────────────────────────────────────────────────
#
# A DERIVED CLASSIFICATION, and labelled as one. §11 gives response categories
# as PROSE ("Delegation; process redesign; manager development"); this
# vocabulary is our reading of that prose, not text the spec supplies. The
# assignment per subdomain is data (INTERVENTION_MAP below), reviewable beside
# the quoted §11 wording, and signed off before phase 10 — never inferred by a
# regex over prose.
#
# It is load-bearing because it is the filter that decides whether a finding can
# produce a TRAINING recommendation. §11 is explicit that "Training is one
# intervention category among several", and §8.5 that training is not a
# substitute for addressing a substantiated compensation problem. That has to be
# structural, not a line in a prompt.
TRAINING             = 'training'
WORK_DESIGN          = 'work_design'
MANAGEMENT_PRACTICE  = 'management_practice'
POLICY_OR_STRUCTURAL = 'policy_or_structural'
FURTHER_DIAGNOSTIC   = 'further_diagnostic'
COMMUNICATION        = 'communication'

INTERVENTION_TYPES = [
    TRAINING, WORK_DESIGN, MANAGEMENT_PRACTICE,
    POLICY_OR_STRUCTURAL, FURTHER_DIAGNOSTIC, COMMUNICATION,
]

INTERVENTION_TYPE_LABELS = {
    TRAINING:             'Capability development',
    WORK_DESIGN:          'Work or role design',
    MANAGEMENT_PRACTICE:  'Management practice',
    POLICY_OR_STRUCTURAL: 'Policy or structural action',
    FURTHER_DIAGNOSTIC:   'Further diagnostic work',
    COMMUNICATION:        'Communication',
}


# ── The §11 diagnostic-to-intervention map ───────────────────────────────────
#
# Spec §11's table, transcribed VERBATIM and keyed by the (domain, subdomain)
# pair. Reference data, not prose in a template — every report that needs it
# renders from here, so the twelve rows cannot drift between four reports.
#
# `finding`, `areas` and `categories` are the spec's own words. `types` is the
# derived classification (see above) and is what app/catalogue.py filters the
# intervention catalogue against.
#
# Note what §11 does and does not say. These are "possible investigative
# directions, not automatic conclusions. More than one issue may contribute to
# the same pattern, and the most appropriate response depends on the supporting
# evidence." Nothing in this table is a recommendation.

INTERVENTION_MAP = {
    (GEM, 'G'): {
        'finding': 'Low General Job Satisfaction',
        'areas': 'Overall role experience and workplace conditions',
        'categories': 'Further diagnostic conversations; work or role redesign; '
                      'management action',
        'types': [FURTHER_DIAGNOSTIC, WORK_DESIGN, MANAGEMENT_PRACTICE],
    },
    (GEM, 'E'): {
        'finding': 'Low Emotional Experience',
        'areas': 'Sources of positive or negative work-related feelings',
        'categories': 'Workload review; supervisory support; interpersonal or '
                      'operational improvements',
        'types': [WORK_DESIGN, MANAGEMENT_PRACTICE, FURTHER_DIAGNOSTIC],
    },
    (GEM, 'M'): {
        'finding': 'Low Match with Overall Expectations',
        'areas': 'Differences between expectations and actual experience',
        'categories': 'Role clarification; communication; review of commitments '
                      'and employee experience',
        'types': [COMMUNICATION, WORK_DESIGN, POLICY_OR_STRUCTURAL],
    },
    (TID, 'T'): {
        'finding': 'Low Autonomy',
        'areas': 'Decision rights and unnecessary restrictions',
        'categories': 'Delegation; process redesign; manager development',
        'types': [MANAGEMENT_PRACTICE, WORK_DESIGN, TRAINING],
    },
    (TID, 'I'): {
        'finding': 'Low Purpose and Impact',
        'areas': 'Visibility of contribution and organizational priorities',
        'categories': 'Role alignment; goal communication; job enrichment',
        'types': [COMMUNICATION, WORK_DESIGN],
    },
    (TID, 'D'): {
        'finding': 'Low Appropriate Challenge',
        'areas': 'Workload, capability fit and development opportunities',
        'categories': 'Work redesign; resourcing; capability development',
        'types': [WORK_DESIGN, POLICY_OR_STRUCTURAL, TRAINING],
    },
    (BIT, 'B'): {
        'finding': 'Low Belonging',
        'areas': 'Inclusion and team integration',
        'categories': 'Team practices; leadership action; inclusion initiatives',
        'types': [MANAGEMENT_PRACTICE, TRAINING],
    },
    (BIT, 'I'): {
        'finding': 'Low Positive Interactions',
        'areas': 'Communication and conflict patterns',
        'categories': 'Team agreements; conflict resolution; manager development',
        'types': [MANAGEMENT_PRACTICE, TRAINING, COMMUNICATION],
    },
    (BIT, 'T'): {
        'finding': 'Low Trust',
        'areas': 'Reliability, fairness and psychological safety',
        'categories': 'Accountability; leadership action; transparent processes',
        'types': [MANAGEMENT_PRACTICE, POLICY_OR_STRUCTURAL, COMMUNICATION],
    },
    # ── The three PIE rows carry NO `training` type, deliberately. ───────────
    # §8.5: "Training should not be treated as a substitute for addressing
    # substantiated compensation inequities or structural reward problems."
    # Because app/catalogue.py filters candidate interventions against these
    # lists, a compensation finding CANNOT produce a training recommendation —
    # it is prevented by the data, not by asking a model nicely.
    (PIE, 'P'): {
        'finding': 'Low Peer Comparison Satisfaction',
        'areas': 'Internal compensation comparisons',
        'categories': 'Role and pay-equity review',
        'types': [POLICY_OR_STRUCTURAL, FURTHER_DIAGNOSTIC],
    },
    (PIE, 'I'): {
        'finding': 'Low Industry Comparison Satisfaction',
        'areas': 'Perceived external competitiveness',
        'categories': 'Relevant market benchmarking',
        'types': [FURTHER_DIAGNOSTIC, POLICY_OR_STRUCTURAL],
    },
    (PIE, 'E'): {
        'finding': 'Low Compensation Expectations Satisfaction',
        'areas': 'Financial expectations and perceived reward alignment',
        'categories': 'Compensation review; transparent communication; policy '
                      'or structural action',
        'types': [POLICY_OR_STRUCTURAL, COMMUNICATION],
    },
}

INTERVENTION_MAP_NOTE = (
    'These are possible investigative directions, not automatic conclusions. '
    'More than one issue may contribute to the same pattern, and the most '
    'appropriate response depends on the supporting evidence.'
)


def intervention_entry(domain_code: str, subdomain_code: str) -> dict:
    """The §11 row for a (domain, subdomain) cell. Empty dict if unmapped."""
    return INTERVENTION_MAP.get((domain_code, subdomain_code), {})


# ── Where training may NEVER be recommended ─────────────────────────────────
#
# §8.5 is the one EXPLICIT prohibition in the specification:
#
#     "Training should not be treated as a substitute for addressing
#      substantiated compensation inequities or structural reward problems."
#
# It names one domain. Nothing else in the specification excludes training
# anywhere else — §11 closes with "Training is one intervention category among
# several. The system should recommend training only where a capability or
# behaviour gap is supported by the diagnostic evidence", which is a rule about
# EVIDENCE, not a whitelist of cells.
#
# AN EARLIER VERSION OF THIS MODULE GOT THAT WRONG. It derived permission from
# the `types` list below — our reading of §11's prose — and so treated §11's
# example categories as exhaustive. When the real curriculum arrived, that
# filter would have silently discarded 14 of the 26 supplied courses, including
# every course for Trust, Purpose and Impact, and Emotional Experience. The
# curriculum author's professional judgement about which of their courses serve
# which finding is not ours to overrule; the specification's one prohibition is.
#
# So `types` stays, as the §11 categories a report DISPLAYS alongside any
# course, and permission is this list and nothing else.
TRAINING_EXCLUDED_DOMAINS = [PIE]


def allows_training(domain_code: str, subdomain_code: str = None) -> bool:
    """
    Whether a capability-development recommendation may be offered for this
    cell.

    False for every Reward Satisfaction subdomain (§8.5), true elsewhere.
    app/catalogue.py consults this BEFORE a catalogue row reaches a report or
    the model, so the prohibition is structural rather than a line in a prompt.

    The supplied curriculum reached the same conclusion independently: it maps
    no course at all to the three Reward subdomains, offering instead a note
    that each is addressed by a pay-equity review, market benchmarking or a
    compensation review.
    """
    return domain_code not in TRAINING_EXCLUDED_DOMAINS


# ── Segment dimensions (spec §3.2.4) ────────────────────────────────────────
#
# "Patterns across departments, functions, locations and other appropriate
# organizational groups" is a core objective, so segmentation is a first-class
# table (app/models.py: Segment / SegmentOption), not a hardcoded enum. These
# are the suggested STARTING keys an org admin can accept or replace — a
# default, never a limit.
#
# A respondent self-declares these at the start of the form. The platform never
# ingests a staff list (§13.2 data minimisation), so there is nothing to look a
# respondent up IN — by construction, not by policy.
SUGGESTED_SEGMENT_KEYS = [
    ('department', 'Department'),
    ('function', 'Function'),
    ('location', 'Location'),
    ('tenure_band', 'Length of service'),
]
