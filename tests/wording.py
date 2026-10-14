"""
NEGATION-AWARE WORDING GUARDS.

A bare substring scan cannot police this product's language, because the
reports deliberately print the negated forms of the very things they must not
assert:

    "This wave shows associations between measures, not causes."
    "It does not show that Trust causes higher or lower satisfaction."

Both sentences are REQUIRED by §10.4 and both contain the word "causes". A
naive `assert 'causes' not in text` flags the safeguard as the violation —
which is worse than having no test, because it trains whoever hits it to delete
the safeguard.

So these guards split the text into sentences and ask whether each causal or
verdict word is NEGATED in its own sentence.

THE GUARDS ARE THEMSELVES TESTED AGAINST REAL VIOLATIONS (tests/test_wording.py)
so they cannot quietly become vacuous. A guard that returns an empty list for
everything passes every test it guards and protects nothing; this is the same
discipline the sibling product applies to its verdict scan, and the reason it
exists there is that the naive version was written first.
"""
import re

# Verbs that assert one thing produced another. §10.4, §17.8.
#
# INFLECTIONS MATTER. The first draft of this list had 'results in' and
# 'explains' but not 'resulted in' or 'explain', and the meta-test in
# tests/test_wording.py caught both — which is the entire reason that test
# asserts the guard catches real violations rather than only that it
# permits the safeguards.
CAUSAL_TERMS = [
    'causes', 'caused', 'causing', 'cause of',
    'drives', 'driven by', 'driving',
    'because of', 'due to', 'as a result of',
    'results in', 'resulted in', 'resulting in', 'result of',
    'leads to', 'lead to', 'led to', 'leading to',
    'produces', 'produce', 'produced by', 'producing',
    'explains', 'explain', 'explained by', 'explaining',
    'brought about', 'gives rise to', 'contributes to', 'contributed to',
    'triggers', 'triggered by',
    'responsible for', 'stems from', 'owing to',
    'the reason for', 'the root cause',
]

# Band and verdict vocabulary. §10.5 — no label while bands are unvalidated.
# Matched as WHOLE WORDS: "Lower priority" is a reading order and is fine,
# "low" as a grade is not.
VERDICT_TERMS = [
    'critical', 'failing', 'poor', 'weak', 'excellent', 'healthy',
    'concerning', 'alarming', 'severe', 'unacceptable', 'satisfactory grade',
    'red flag', 'warning sign', 'critical gap', 'moderate gap', 'minor gap',
]

# §6.4. The challenge subdomain must never be read as pressure or strain.
STRESS_TERMS = [
    'stress', 'stressed', 'stressful', 'burnout', 'burn-out', 'overload',
    'overloaded', 'overwhelmed', 'overwhelming', 'unmanageable workload',
    'excessive pressure', 'under pressure', 'strain',
]

# Tokens that turn a sentence into a denial of the thing it names.
_NEGATORS = [
    'not', "n't", 'never', 'cannot', 'nothing', 'no ', 'nor ',
    'without', 'rather than', 'instead of', 'does not', 'do not',
    'is not', 'are not', 'avoid', 'must not', 'should not',
]


def sentences(text):
    """Split on sentence boundaries, keeping it crude but predictable."""
    collapsed = re.sub(r'\s+', ' ', text or '')
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', collapsed) if s.strip()]


def _negated(sentence):
    low = sentence.lower()
    return any(negator in low for negator in _NEGATORS)


def _hits(text, terms, whole_word=False):
    """
    Sentences that use one of `terms` WITHOUT negating it.

    Returns (term, sentence) pairs so a failure names the offending sentence
    rather than only reporting that something, somewhere, was wrong.
    """
    found = []
    for sentence in sentences(text):
        low = sentence.lower()
        for term in terms:
            if whole_word:
                present = re.search(rf'\b{re.escape(term)}\b', low) is not None
            else:
                present = term in low
            if present and not _negated(sentence):
                found.append((term, sentence))
    return found


def causal_hits(text):
    """Unnegated causal claims. §10.4, §17.8."""
    return _hits(text, CAUSAL_TERMS)


def verdict_hits(text):
    """Unnegated band or verdict labels. §10.5."""
    return _hits(text, VERDICT_TERMS, whole_word=True)


def stress_hits(text):
    """Unnegated stress/overload vocabulary. §6.4."""
    return _hits(text, STRESS_TERMS, whole_word=False)


def identity_hits(text):
    """
    Language that points at an individual respondent. §12.3, §13.9.

    Not negation-aware: there is no legitimate negated form of "the person who
    answered". A report that needs to say nobody is identified says it in the
    terms the templates already use.
    """
    patterns = [
        r'\bthe (?:person|employee|individual|respondent) who\b',
        r'\bone respondent\b',
        r'\ba respondent (?:said|wrote|reported|told)\b',
        r'\bwho (?:gave|submitted|wrote) (?:this|that|the) (?:answer|response)\b',
        r'\bspeak to the (?:person|employee|individual)\b',
    ]
    found = []
    for sentence in sentences(text):
        for pattern in patterns:
            if re.search(pattern, sentence, re.IGNORECASE):
                found.append((pattern, sentence))
    return found


def all_hits(text):
    """Every guard at once, for a whole-document sweep."""
    return {
        'causal': causal_hits(text),
        'verdict': verdict_hits(text),
        'stress': stress_hits(text),
        'identity': identity_hits(text),
    }
