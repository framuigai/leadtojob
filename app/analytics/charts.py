"""
Chart generation. matplotlib only, rendered to base64 PNG for the PDF.

No kaleido, no plotly export — the sibling products settled this and it holds:
matplotlib for PDF images, Plotly only for in-page HTML.

────────────────────────────────────────────────────────────────────────────────
COLOUR ENCODES THE RESPONSE ANCHOR, NEVER A JUDGEMENT OF THE SCORE
────────────────────────────────────────────────────────────────────────────────
Both sibling products colour a bar by its band — green for Strength, red for
Critical Gap. §10.5 forbids that here until pilot evidence exists, so the
palette below is bound to the FIVE ANSWER OPTIONS, which are facts about what
people selected, not to a verdict on the resulting mean.

A distribution bar therefore reads the same way regardless of the score: the
dark red segment is always "strongly disagree", whether the mean is 1.2 or 4.8.
Nothing in this module may take a score and return a colour.

Mean bars are drawn in ONE NEUTRAL COLOUR for the same reason. A reader who
wants to know whether 3.1 is good must look at the distribution and the
comparison beside it — which is precisely what §10.5 asks the report to show.
"""
import base64
import io

import matplotlib
matplotlib.use('Agg')                        # no display; required on Render
import matplotlib.pyplot as plt              # noqa: E402

from app import domains                      # noqa: E402

# The five answer options, on a diverging unfavourable-to-favourable ramp.
# Index 0 = "Strongly disagree" ... index 4 = "Strongly agree".
ANCHOR_COLOURS = ['#A33A3A', '#D18A6A', '#C9C4BC', '#7FA7C4', '#2E6B8A']

# Not-applicable: deliberately outside the ramp and visually quiet. It is not a
# score and must not read as one end of the scale.
NA_COLOUR = '#E8E6E2'

# One neutral colour for every mean bar. See the module docstring.
NEUTRAL_BAR = '#4A6274'
REFERENCE_LINE = '#8A8A8A'

TEXT = '#2B2F38'
GRID = '#E2E4E9'


def _b64(figure):
    buffer = io.BytesIO()
    figure.savefig(buffer, format='png', dpi=150, bbox_inches='tight',
                   facecolor='white')
    plt.close(figure)
    return base64.b64encode(buffer.getvalue()).decode('ascii')


def _style(axis):
    axis.spines['top'].set_visible(False)
    axis.spines['right'].set_visible(False)
    axis.spines['left'].set_color(GRID)
    axis.spines['bottom'].set_color(GRID)
    axis.tick_params(colors=TEXT, labelsize=8)
    axis.set_axisbelow(True)


def distribution_bar(distribution, title=None, anchor_type='agreement'):
    """
    A single stacked bar: the full 1-5 frequency, plus N/A as a separate
    segment.

    THE PRIMARY DISPLAY (§10.5). A 3.0 made of all-3s and a 3.0 made of
    half-1s and half-5s are different organisations, and this is the chart that
    tells them apart.
    """
    labels = domains.anchor_labels(anchor_type)
    counts = [distribution.counts.get(score, 0) for score in range(1, 6)]
    total = sum(counts) + distribution.not_applicable
    if not total:
        return None

    figure, axis = plt.subplots(figsize=(7.2, 1.15))
    left = 0
    for index, count in enumerate(counts):
        if not count:
            continue
        width = count / total * 100
        axis.barh([0], [width], left=left, color=ANCHOR_COLOURS[index],
                  height=0.55, label=labels[index])
        if width >= 7:
            axis.text(left + width / 2, 0, f'{width:.0f}%', ha='center',
                      va='center', fontsize=8,
                      color='white' if index in (0, 4) else TEXT)
        left += width

    if distribution.not_applicable:
        width = distribution.not_applicable / total * 100
        axis.barh([0], [width], left=left, color=NA_COLOUR, height=0.55,
                  label=domains.NOT_APPLICABLE_SHORT)
        if width >= 7:
            axis.text(left + width / 2, 0, f'{width:.0f}%', ha='center',
                      va='center', fontsize=8, color=TEXT)

    axis.set_xlim(0, 100)
    axis.set_yticks([])
    axis.set_xticks([])
    for spine in axis.spines.values():
        spine.set_visible(False)
    if title:
        axis.set_title(title, fontsize=9, color=TEXT, loc='left', pad=6)
    return _b64(figure)


def anchor_legend(anchor_type='agreement'):
    """
    The key for every distribution bar in the report. Rendered once.

    Separate from the bars so a reader meets the scale in one place rather than
    inferring it from colour five times.
    """
    labels = domains.anchor_labels(anchor_type) + [
        domains.NOT_APPLICABLE_LABEL]
    colours = ANCHOR_COLOURS + [NA_COLOUR]

    figure, axis = plt.subplots(figsize=(7.2, 0.5))
    left = 0
    for colour, label in zip(colours, labels):
        axis.barh([0], [1], left=left, color=colour, height=0.5)
        left += 1
    axis.set_xlim(0, len(labels))
    axis.set_ylim(-0.6, 0.6)
    axis.set_yticks([])
    axis.set_xticks([i + 0.5 for i in range(len(labels))])
    axis.set_xticklabels(labels, fontsize=7, color=TEXT, rotation=0)
    for spine in axis.spines.values():
        spine.set_visible(False)
    axis.tick_params(length=0)
    return _b64(figure)


def score_bars(rows, reference=None, title=None, xlabel='Mean score (1-5)'):
    """
    Horizontal mean bars. `rows` is [(label, mean_or_None)].

    ONE COLOUR for every bar. A suppressed or unavailable row is drawn as an
    empty slot with its reason, never as a zero-length bar — a zero bar reads
    as the worst possible score rather than as an absent one.

    `reference` draws a vertical line (the organisation-wide figure), which is
    how a reader is meant to judge a score while bands are unvalidated:
    relative to something, not against an absolute label.
    """
    labels = [r[0] for r in rows]
    values = [r[1] for r in rows]
    height = max(1.4, 0.42 * len(rows) + 0.9)

    figure, axis = plt.subplots(figsize=(7.2, height))
    positions = range(len(rows))
    drawable = [(p, v) for p, v in zip(positions, values) if v is not None]
    if drawable:
        axis.barh([p for p, _ in drawable], [v for _, v in drawable],
                  color=NEUTRAL_BAR, height=0.5)
        for position, value in drawable:
            axis.text(value + 0.06, position, f'{value:.2f}', va='center',
                      fontsize=8, color=TEXT)

    for position, value in zip(positions, values):
        if value is None:
            axis.text(0.06, position, 'not reported', va='center', fontsize=8,
                      color=REFERENCE_LINE, style='italic')

    if reference is not None:
        axis.axvline(reference, color=REFERENCE_LINE, linestyle='--',
                     linewidth=1)
        axis.text(reference, len(rows) - 0.4, f' organisation {reference:.2f}',
                  fontsize=7, color=REFERENCE_LINE, va='bottom')

    axis.set_yticks(list(positions))
    axis.set_yticklabels(labels, fontsize=8.5, color=TEXT)
    axis.invert_yaxis()
    axis.set_xlim(1, 5)
    axis.set_xticks([1, 2, 3, 4, 5])
    axis.set_xlabel(xlabel, fontsize=8, color=TEXT)
    axis.grid(axis='x', color=GRID, linewidth=0.7)
    _style(axis)
    if title:
        axis.set_title(title, fontsize=9.5, color=TEXT, loc='left', pad=8)
    return _b64(figure)


def contrast_bars(contrasts, outcome_label, title=None):
    """
    §10.4's comparison, drawn: the lowest third against the highest third.

    TWO BARS PER MEASURE, not one bar of the difference. The difference alone
    loses the levels — a gap of 1.5 between 1.0 and 2.5 is a different
    organisation from a gap of 1.5 between 3.5 and 5.0, and the second is not a
    problem.
    """
    rows = [(label, contrast) for label, contrast in contrasts if contrast]
    if not rows:
        return None

    height = max(1.8, 0.62 * len(rows) + 1.1)
    figure, axis = plt.subplots(figsize=(7.2, height))

    positions = range(len(rows))
    offset = 0.21
    axis.barh([p - offset for p in positions],
              [c.lower_mean for _, c in rows], height=0.36,
              color=ANCHOR_COLOURS[1], label=f'Lowest third on {outcome_label}')
    axis.barh([p + offset for p in positions],
              [c.upper_mean for _, c in rows], height=0.36,
              color=ANCHOR_COLOURS[3], label=f'Highest third on {outcome_label}')

    for position, (_, contrast) in zip(positions, rows):
        axis.text(contrast.lower_mean + 0.06, position - offset,
                  f'{contrast.lower_mean:.2f}', va='center', fontsize=7.5,
                  color=TEXT)
        axis.text(contrast.upper_mean + 0.06, position + offset,
                  f'{contrast.upper_mean:.2f}', va='center', fontsize=7.5,
                  color=TEXT)

    axis.set_yticks(list(positions))
    axis.set_yticklabels([label for label, _ in rows], fontsize=8.5, color=TEXT)
    axis.invert_yaxis()
    axis.set_xlim(1, 5)
    axis.set_xticks([1, 2, 3, 4, 5])
    axis.set_xlabel('Mean score (1-5)', fontsize=8, color=TEXT)
    axis.grid(axis='x', color=GRID, linewidth=0.7)
    axis.legend(fontsize=7.5, loc='lower right', frameon=False)
    _style(axis)
    if title:
        axis.set_title(title, fontsize=9.5, color=TEXT, loc='left', pad=8)
    return _b64(figure)


def trend_bars(changes, title=None):
    """
    Previous wave against this wave.

    A measure that is NOT COMPARABLE is omitted entirely rather than drawn at
    zero. A domain absent from one wave plotted as 0 reads as a collapse that
    did not happen — the organisation asked fewer questions, it did not get
    worse. See app/analytics/trend.py.
    """
    rows = [(label, change) for label, change in changes
            if change and getattr(change, 'comparable', False)]
    if not rows:
        return None

    height = max(1.8, 0.62 * len(rows) + 1.1)
    figure, axis = plt.subplots(figsize=(7.2, height))

    positions = range(len(rows))
    offset = 0.21
    axis.barh([p - offset for p in positions],
              [c.previous.mean for _, c in rows], height=0.36,
              color=GRID, label='Previous wave')
    axis.barh([p + offset for p in positions],
              [c.current.mean for _, c in rows], height=0.36,
              color=NEUTRAL_BAR, label='This wave')

    for position, (_, change) in zip(positions, rows):
        axis.text(change.current.mean + 0.06, position + offset,
                  f'{change.difference:+.2f}', va='center', fontsize=7.5,
                  color=TEXT)

    axis.set_yticks(list(positions))
    axis.set_yticklabels([label for label, _ in rows], fontsize=8.5, color=TEXT)
    axis.invert_yaxis()
    axis.set_xlim(1, 5)
    axis.set_xticks([1, 2, 3, 4, 5])
    axis.set_xlabel('Mean score (1-5)', fontsize=8, color=TEXT)
    axis.grid(axis='x', color=GRID, linewidth=0.7)
    axis.legend(fontsize=7.5, loc='lower right', frameon=False)
    _style(axis)
    if title:
        axis.set_title(title, fontsize=9.5, color=TEXT, loc='left', pad=8)
    return _b64(figure)


def coverage_bars(rows, title=None):
    """
    Percentage of responses marked "not applicable", per measure.

    A FINDING IN ITS OWN RIGHT, not a data-quality footnote: 30% N/A on Peer
    Comparison means people do not know what their colleagues earn, which is a
    fact about pay transparency worth naming.
    """
    rows = [r for r in rows if r[1] is not None]
    if not rows:
        return None

    height = max(1.4, 0.4 * len(rows) + 0.9)
    figure, axis = plt.subplots(figsize=(7.2, height))
    positions = range(len(rows))
    axis.barh(list(positions), [r[1] for r in rows], color=NA_COLOUR,
              edgecolor=REFERENCE_LINE, linewidth=0.6, height=0.5)
    for position, row in zip(positions, rows):
        axis.text(row[1] + 0.6, position, f'{row[1]:.0f}%', va='center',
                  fontsize=8, color=TEXT)

    axis.set_yticks(list(positions))
    axis.set_yticklabels([r[0] for r in rows], fontsize=8.5, color=TEXT)
    axis.invert_yaxis()
    axis.set_xlim(0, max(100, max(r[1] for r in rows) * 1.15))
    axis.set_xlabel('Responses marked not applicable (%)', fontsize=8,
                    color=TEXT)
    axis.grid(axis='x', color=GRID, linewidth=0.7)
    _style(axis)
    if title:
        axis.set_title(title, fontsize=9.5, color=TEXT, loc='left', pad=8)
    return _b64(figure)
