"""
Change between waves — spec §3.2.7 and §16.

A trend section appears once a cycle has a `previous_cycle_id` and both waves
measured the domain in question.

────────────────────────────────────────────────────────────────────────────────
THE RULE THAT MATTERS: A DOMAIN ABSENT FROM EITHER WAVE IS REFUSED, NOT PLOTTED
────────────────────────────────────────────────────────────────────────────────
A pulse wave may legitimately run Overall Satisfaction plus one diagnostic
domain. Comparing that against a previous full wave gives three domains with a
figure last time and nothing this time.

Plotted naively, a missing domain renders as zero, or as a line stopping, or as
an arrow pointing down — and "Reward Satisfaction fell from 3.4 to 0" is a
catastrophic finding that did not happen. The organisation did not get worse;
it asked fewer questions.

So a domain not measured in BOTH waves produces NotComparable with the reason,
and the report prints the reason.

────────────────────────────────────────────────────────────────────────────────
AND IMPROVEMENT IS NEVER ATTRIBUTED
────────────────────────────────────────────────────────────────────────────────
§16: "Improvement after an intervention does not by itself prove that the
intervention caused the change. Organizations should consider other
explanations, timing, sample composition and the strength of the evidence."

Every trend output carries domains.TREND_CAVEAT, and composition is reported
beside the change rather than left implicit: a wave answered by 40 people and a
wave answered by 120 are not the same population measured twice, and the
difference between them is not necessarily a change in anybody's opinion.
"""
from dataclasses import dataclass, field

from app import domains
from app.analytics import scoring as sc


@dataclass(frozen=True)
class NotComparable:
    """
    Why two waves cannot be compared on this measure. Rendered as the reason.

    A distinct type rather than a None or a zero — see the module docstring.
    """
    reason: str

    def __bool__(self):
        return False


@dataclass(frozen=True)
class Change:
    """
    One measure, two waves.

    `composition` carries both response counts, printed beside the change
    rather than buried: a shift of 0.3 between a wave of 40 and a wave of 120
    may be a change of opinion, or may be a different set of people answering,
    and the report is not in a position to say which.
    """
    measure: object
    previous: object        # sc.GroupResult or sc.Suppressed
    current: object
    previous_n: int
    current_n: int
    caveat: str = field(default=domains.TREND_CAVEAT)

    @property
    def comparable(self):
        return (isinstance(self.previous, sc.GroupResult)
                and isinstance(self.current, sc.GroupResult))

    @property
    def difference(self):
        if not self.comparable:
            return None
        return self.current.mean - self.previous.mean

    @property
    def favourable_shift(self):
        """
        Change in the percentage agreeing, not only in the mean.

        §16 asks for changes in scores; §10.5 makes the DISTRIBUTION the
        primary display. A mean that holds steady while agreement splits into
        two camps is a real change that the mean alone conceals.
        """
        if not self.comparable:
            return None
        before = self.previous.distribution.favourable_pct
        after = self.current.distribution.favourable_pct
        if before is None or after is None:
            return None
        return after - before

    @property
    def measure_label(self):
        if isinstance(self.measure, tuple):
            return domains.subdomain_label(*self.measure)
        return domains.DOMAIN_LABELS.get(self.measure, self.measure)

    def sentence(self):
        if not self.comparable:
            return f'{self.measure_label}: not comparable between these waves.'
        return (
            f'{self.measure_label} moved from {self.previous.mean:.2f} to '
            f'{self.current.mean:.2f} ({self.difference:+.2f}). '
            f'Agreement moved {self.favourable_shift:+.0f} percentage points. '
            f'Previous wave n={self.previous_n}; this wave n={self.current_n}.'
        )


@dataclass(frozen=True)
class TrendReport:
    previous_cycle_id: str
    changes: list                 # [(measure, Change or NotComparable)]
    previous_n: int
    current_n: int
    caveat: str = field(default=domains.TREND_CAVEAT)

    @property
    def composition_note(self):
        """
        Said plainly whenever the two waves differ materially in size.

        20% is a judgement, not a specification requirement, and it is stated
        as a prompt to think rather than as a threshold with meaning.
        """
        if not self.previous_n or not self.current_n:
            return None
        larger = max(self.previous_n, self.current_n)
        smaller = min(self.previous_n, self.current_n)
        if larger and (larger - smaller) / larger >= 0.2:
            return (
                f'These two waves were answered by noticeably different '
                f'numbers of people ({self.previous_n} then {self.current_n}). '
                'A different set of respondents is not the same population '
                'measured twice, so part of any change may reflect who '
                'answered rather than a change in opinion.'
            )
        return None


def compare(previous_profiles, current_profiles, previous_domains,
            current_domains, previous_cycle_id, min_reporting_n=1,
            measures=None):
    """
    Build the trend section.

    `previous_domains` / `current_domains` are the `assessed_domains` of each
    wave — the snapshot taken at deploy time, so narrowing a cycle afterwards
    cannot retroactively change what is comparable.
    """
    if measures is None:
        measures = (
            [(domains.GEM, s) for s in domains.SUBDOMAINS[domains.GEM]]
            + list(domains.DIAGNOSTIC_DOMAINS)
        )

    previous_set = set(previous_domains or [])
    current_set = set(current_domains or [])

    changes = []
    for measure in measures:
        domain_code = measure[0] if isinstance(measure, tuple) else measure

        if domain_code not in previous_set or domain_code not in current_set:
            absent = 'the previous' if domain_code not in previous_set else 'this'
            changes.append((measure, NotComparable(
                f'{domains.DOMAIN_LABELS.get(domain_code, domain_code)} was not '
                f'measured in {absent} wave, so there is nothing to compare. '
                'It is shown as not comparable rather than as a change, '
                'because a domain that was not asked about is not a domain '
                'that fell.'
            )))
            continue

        previous = sc.group_result(previous_profiles, measure, min_reporting_n)
        current = sc.group_result(current_profiles, measure, min_reporting_n)

        if not isinstance(previous, sc.GroupResult) or not isinstance(current, sc.GroupResult):
            which = ('the previous wave' if not isinstance(previous, sc.GroupResult)
                     else 'this wave')
            changes.append((measure, NotComparable(
                f'Too few responses in {which} to report this measure, so the '
                'two cannot be compared.'
            )))
            continue

        changes.append((measure, Change(
            measure=measure, previous=previous, current=current,
            previous_n=previous.n, current_n=current.n,
        )))

    return TrendReport(
        previous_cycle_id=previous_cycle_id,
        changes=changes,
        previous_n=len(previous_profiles),
        current_n=len(current_profiles),
    )


def actions_alongside(improvement_actions):
    """
    §16's "completion of agreed improvement actions", reported BESIDE the
    change and never joined to it.

    The report prints both and refuses to connect them. An action completed and
    a score that rose are two facts; asserting the first produced the second is
    exactly what §16's caveat forbids, and it is the most natural sentence in
    the world to write.
    """
    counts = {'proposed': 0, 'agreed': 0, 'in_progress': 0,
              'completed': 0, 'dropped': 0}
    for action in improvement_actions:
        if action.status in counts:
            counts[action.status] += 1
    counts['total'] = sum(v for k, v in counts.items() if k != 'total')
    return counts
