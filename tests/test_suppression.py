"""
SUPPRESSION, AND THE DIFFERENCING DEFENCE.

    a group of 4 suppresses, and its sibling suppresses too.

Every test here passes an EXPLICIT threshold rather than relying on the
configured default, which is 1 in development and testing. A suppression rule
that only holds at one particular configured value is a rule nobody has
tested — and the point of the floor is that it binds at whatever it is set to.

Pure Python: no app context, no database.
"""
import pytest

from app.analytics import scoring as sc


def group_of(n, score=4.0, domain='BIT', sub='T'):
    """n respondents who all gave the same score, so means are predictable."""
    answers = [
        sc.Answer(domain_code=domain, subdomain_code=sub,
                  item_code=f'{domain}-{sub}-0{i + 1}', score=int(score))
        for i in range(4)
    ]
    return [sc.build_profile(f'{domain}{sub}{i}', answers) for i in range(n)]


def cells_of(sizes, min_n=1):
    """A breakdown {key: GroupResult} with the given group sizes."""
    return {
        key: sc.group_result(group_of(n), ('BIT', 'T'), min_n=min_n)
        for key, n in sizes.items()
    }


# ── Primary suppression ─────────────────────────────────────────────────────

def test_a_group_below_the_threshold_is_suppressed():
    """THE NAMED ACCEPTANCE CRITERION, first half."""
    result = sc.group_result(group_of(4), ('BIT', 'T'), min_n=5)
    assert isinstance(result, sc.Suppressed)
    assert result.n == 4
    assert result.threshold == 5


def test_a_group_at_the_threshold_is_reported():
    """The boundary is inclusive: n >= min_n reports."""
    result = sc.group_result(group_of(5), ('BIT', 'T'), min_n=5)
    assert isinstance(result, sc.GroupResult)
    assert result.n == 5


def test_a_suppressed_cell_renders_as_a_label_never_as_a_blank():
    """
    A blank is read as zero, and zero on a satisfaction scale is a
    catastrophic finding rather than an absent one. The cell has to SAY it is
    suppressed and why.
    """
    result = sc.group_result(group_of(2), ('BIT', 'T'), min_n=5)
    assert result.label == 'Suppressed (n < 5)'
    assert not result                      # falsy, so `if result:` is safe
    assert result.n == 2                   # the n is still available internally


def test_nothing_suppresses_at_a_threshold_of_one():
    """
    The local-testing configuration. A single respondent produces a reportable
    figure, which is the whole point of setting it to 1 — and the survey form
    tells respondents so while it is this low.
    """
    result = sc.group_result(group_of(1), ('BIT', 'T'), min_n=1)
    assert isinstance(result, sc.GroupResult)
    assert result.n == 1


# ── Complementary suppression: the differencing defence ────────────────────

def test_suppressing_exactly_one_cell_achieves_nothing_so_a_sibling_goes_too():
    """
    THE NAMED ACCEPTANCE CRITERION, second half.

    Departments of 20, 20 and 4, threshold 5. Suppressing only the 4 is
    theatre: a reader with the total (44) subtracts the two visible cells and
    recovers it exactly. So the next-smallest is suppressed as well.
    """
    cells = cells_of({'ops': 20, 'sales': 20, 'finance': 4})
    out = sc.suppress_breakdown(cells, min_n=5)

    hidden = {k for k, v in out.items() if isinstance(v, sc.Suppressed)}
    assert 'finance' in hidden
    assert len(hidden) >= 2, out
    # The complementary one is marked as such, so a report can explain itself
    # rather than looking arbitrary.
    complementary = [v for v in out.values()
                     if isinstance(v, sc.Suppressed) and v.complementary]
    assert len(complementary) == 1


def test_the_smallest_visible_cell_is_the_one_sacrificed():
    """
    Hiding a large group to shield a small one costs the report far more
    information than it needs to.
    """
    cells = cells_of({'big': 50, 'medium': 9, 'tiny': 2})
    out = sc.suppress_breakdown(cells, min_n=5)

    assert isinstance(out['tiny'], sc.Suppressed)
    assert isinstance(out['medium'], sc.Suppressed)
    assert isinstance(out['big'], sc.GroupResult)


def test_two_tiny_cells_are_not_enough_if_they_total_below_the_threshold():
    """
    THE CASE A NAIVE RULE GETS WRONG.

    Cells of 30, 1 and 1 at threshold 5. Both 1s are suppressed by the primary
    rule, so "at least two are hidden" is already satisfied — and a rule that
    stopped there would leave a reader able to subtract 30 from the total and
    learn the combined score of exactly TWO people. At threshold 5 that is
    precisely what is forbidden.

    So suppression continues until the hidden cells total at least min_n
    between them.
    """
    cells = cells_of({'big': 30, 'a': 1, 'b': 1})
    out = sc.suppress_breakdown(cells, min_n=5)

    hidden = [v for v in out.values() if isinstance(v, sc.Suppressed)]
    assert len(hidden) >= 2
    assert sum(v.n for v in hidden) >= 5, [v.n for v in hidden]


def test_suppression_stops_once_the_hidden_total_is_safe():
    """
    The complement — without it, a rule that suppressed everything would pass
    every test above while making the report useless.
    """
    cells = cells_of({'a': 40, 'b': 30, 'c': 20, 'd': 4})
    out = sc.suppress_breakdown(cells, min_n=5)

    visible = [k for k, v in out.items() if isinstance(v, sc.GroupResult)]
    hidden = [v for v in out.values() if isinstance(v, sc.Suppressed)]
    assert len(hidden) == 2                   # d, plus the smallest sibling (c)
    assert sorted(visible) == ['a', 'b']
    assert sum(v.n for v in hidden) >= 5


def test_a_breakdown_with_nothing_too_small_is_left_entirely_alone():
    cells = cells_of({'a': 10, 'b': 12, 'c': 8})
    out = sc.suppress_breakdown(cells, min_n=5)
    assert all(isinstance(v, sc.GroupResult) for v in out.values())


def test_complementary_suppression_does_nothing_at_a_threshold_of_one():
    """
    With nothing suppressed, nothing can be recovered by subtraction. Returned
    early rather than falling through the loop, which would otherwise start
    hiding perfectly reportable cells chasing a combined-n rule that cannot be
    satisfied when the threshold is 1.
    """
    cells = cells_of({'a': 1, 'b': 1, 'c': 30})
    out = sc.suppress_breakdown(cells, min_n=1)
    assert all(isinstance(v, sc.GroupResult) for v in out.values())


def test_a_single_cell_breakdown_cannot_leak_by_subtraction():
    """
    One group and a total that equals it. There is no sibling to sacrifice, and
    hiding the only cell would be the same as not reporting at all — which is
    what the primary rule already decides.
    """
    cells = cells_of({'only': 3})
    out = sc.suppress_breakdown(cells, min_n=5)
    assert isinstance(out['only'], sc.Suppressed)


def test_every_cell_too_small_suppresses_the_whole_breakdown():
    cells = cells_of({'a': 2, 'b': 3, 'c': 1})
    out = sc.suppress_breakdown(cells, min_n=5)
    assert all(isinstance(v, sc.Suppressed) for v in out.values())


# ── The segment breakdown ──────────────────────────────────────────────────

def test_a_segment_breakdown_groups_by_the_declared_code():
    profiles = []
    for code, n in (('FIN', 6), ('OPS', 7)):
        for p in group_of(n):
            p.segment_values = {'department': code}
            profiles.append(p)

    out = sc.segment_breakdown(profiles, ('BIT', 'T'), 'department', min_n=5)
    assert set(out) == {'FIN', 'OPS'}
    assert out['FIN'].n == 6
    assert out['OPS'].n == 7


def test_respondents_who_declined_to_declare_become_their_own_group():
    """
    Reported under the key None, rather than folded into another group or
    dropped. Folding them in would attribute their answers to a department
    they did not claim; dropping them would quietly shrink the total.
    """
    profiles = []
    for p in group_of(6):
        p.segment_values = {'department': 'FIN'}
        profiles.append(p)
    for p in group_of(5):
        p.segment_values = {}               # prefer not to say
        profiles.append(p)

    out = sc.segment_breakdown(profiles, ('BIT', 'T'), 'department', min_n=5)
    assert None in out
    assert out[None].n == 5


def test_a_segment_breakdown_applies_complementary_suppression(monkeypatch):
    """
    The breakdown path must not bypass the differencing defence — it is the
    path a real report actually calls.
    """
    profiles = []
    for code, n in (('OPS', 20), ('SALES', 20), ('FIN', 3)):
        for p in group_of(n):
            p.segment_values = {'department': code}
            profiles.append(p)

    out = sc.segment_breakdown(profiles, ('BIT', 'T'), 'department', min_n=5)
    hidden = [v for v in out.values() if isinstance(v, sc.Suppressed)]
    assert len(hidden) >= 2
    assert sum(v.n for v in hidden) >= 5


# ── The guard that cannot be satisfied ─────────────────────────────────────

def test_a_threshold_below_one_is_refused_outright():
    with pytest.raises(sc.ScoringError, match='at least 1'):
        sc.group_result(group_of(3), ('BIT', 'T'), min_n=0)
