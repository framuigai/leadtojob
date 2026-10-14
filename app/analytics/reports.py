"""
Report generation: Executive, HR & OD, Management. PDF and XLSX.

────────────────────────────────────────────────────────────────────────────────
REPORT OUTPUT PARITY
────────────────────────────────────────────────────────────────────────────────
The PDF and the XLSX of the same report are built from ONE context dict
(`build_context`) and take their title from ONE function (`report_title`). They
cannot disagree about what they are, because neither composes its own identity.

That is an inherited rule, and the reason for it is mundane: the two files are
sent together, opened side by side, and a client who finds "Executive Summary"
in one and "Exec Report" in the other stops trusting both.

────────────────────────────────────────────────────────────────────────────────
REPORTS ARE GENERATED FROM A CLOSED WAVE
────────────────────────────────────────────────────────────────────────────────
Enforced here, in `require_reportable()`, not merely suggested on a screen.

In-report suppression defends against subtraction WITHIN one document. It
cannot defend across time: two reports of the same group computed at n=5 and
n=6 give up respondent 6's entire profile by subtraction, and no amount of cell
suppression prevents that. The only defence is that a group's figures are
published once, from a wave that has stopped moving.

Every report also records the n it was computed at, on the page and in
`report_events`.
"""
import io
from datetime import date

from app import domains
from app.analytics import association, charts
from app.analytics import scoring as sc
from app.analytics import trend as trend_module

EXECUTIVE = 'executive'
HR_OD = 'hr_od'
MANAGEMENT = 'management'

REPORT_TYPES = (EXECUTIVE, HR_OD, MANAGEMENT)

# ONE source for every title. Both formats read it; neither composes its own.
_TITLES = {
    EXECUTIVE: 'Executive Report',
    HR_OD: 'HR and Organisational Development Report',
    MANAGEMENT: 'Management Report',
}

_AUDIENCES = {
    EXECUTIVE: 'Senior leadership and decision-makers',
    HR_OD: 'HR, organisational development and diagnostic teams',
    MANAGEMENT: 'The manager of this group',
}

PRODUCT_NAME = 'Job Satisfaction Analysis'


class ReportNotAvailable(Exception):
    """
    A report that must not be generated, with the reason in client-readable
    words.

    Raised rather than returned, because a caller that forgets to check a
    return value renders an empty report, and an empty report looks finished.
    """


def report_title(report_type):
    try:
        return _TITLES[report_type]
    except KeyError:
        raise ReportNotAvailable(f'Unknown report type {report_type!r}.')


def report_audience(report_type):
    return _AUDIENCES.get(report_type, '')


def report_filename(report_type, org_name, cycle_id, extension):
    """Built from one function, so the PDF and the XLSX cannot be named apart."""
    safe_org = ''.join(c if c.isalnum() or c in ' -_' else '' for c in org_name)
    safe_org = '_'.join(safe_org.split())[:40] or 'Organisation'
    slug = report_title(report_type).replace(' ', '_')
    return f'{safe_org}_{cycle_id}_{slug}.{extension}'


# ── Gate ───────────────────────────────────────────────────────────────────

def require_reportable(report_type, cycle, deployments, profiles,
                       scope_label=None):
    """
    Refuse to generate, with a reason, in the three cases that matter.

    Checked HERE rather than in a route or a template: a template-level check
    is one `{% if %}` away from being forgotten in the next report, and these
    are the guarantees the product rests on.
    """
    if not deployments:
        raise ReportNotAvailable(
            f'{report_title(report_type)}: this wave has not been deployed, so '
            'there is nothing to report.'
        )

    if any(not d.closed for d in deployments):
        raise ReportNotAvailable(
            f'{report_title(report_type)}: this wave is still open. Reports are '
            'generated once a wave has closed, so that a figure cannot change '
            'after someone has read and acted on it — and so that two reports '
            'of the same group taken at different times cannot be subtracted '
            'from one another to reveal an individual response.'
        )

    n = len(profiles)
    if n < cycle.min_reporting_n:
        where = f' for {scope_label}' if scope_label else ''
        raise ReportNotAvailable(
            f'{report_title(report_type)}{where}: {n} '
            f'{"response" if n == 1 else "responses"} were received and at '
            f'least {cycle.min_reporting_n} are needed before any result can '
            'be shown. This is the reporting threshold working as intended, '
            'not a fault: a figure resting on fewer people than this could be '
            'traced back to them. The responses still count towards the '
            'results for the wider organisation.'
        )


# ── Shared context ─────────────────────────────────────────────────────────

def build_context(report_type, org, cycle, deployments, profiles,
                  branch=None, scope_label=None, segments=(),
                  previous=None, improvement_actions=(), narrative=None):
    """
    THE single context both formats render from.

    `previous` is an optional (profiles, assessed_domains, cycle_id) tuple for
    the trend section.
    """
    min_n = cycle.min_reporting_n
    n = len(profiles)

    outcome_rows = [
        ((domains.GEM, sub), sc.group_result(profiles, (domains.GEM, sub), min_n))
        for sub in domains.SUBDOMAINS[domains.GEM]
    ]
    domain_rows = [
        (code, sc.group_result(profiles, code, min_n))
        for code in domains.DIAGNOSTIC_DOMAINS
        if code in (cycle.assessed_domains or domains.DOMAINS)
    ]
    subdomain_rows = [
        (pair, sc.group_result(profiles, pair, min_n))
        for pair in association.DIAGNOSTIC_SUBDOMAINS
        if pair[0] in (cycle.assessed_domains or domains.DOMAINS)
    ]

    contrasts = [
        (code, association.tertile_contrast(
            profiles, code, association.DEFAULT_OUTCOME, min_n))
        for code, _ in domain_rows
    ]
    priority = association.priority_view(
        profiles, association.DEFAULT_OUTCOME, min_n)

    comparisons = [
        association.segment_comparison(
            profiles, (domains.GEM, 'G'), segment, min_n)
        for segment in segments
    ]

    trend = None
    if previous is not None:
        previous_profiles, previous_domains, previous_cycle_id = previous
        trend = trend_module.compare(
            previous_profiles, profiles, previous_domains,
            cycle.assessed_domains or domains.DOMAINS,
            previous_cycle_id, min_n)

    coverage_rows = []
    for pair, result in subdomain_rows:
        if isinstance(result, sc.GroupResult):
            coverage_rows.append(
                (domains.subdomain_label(*pair), result.distribution.na_pct))

    responses_received = sum(d.responses_received for d in deployments)
    expected = sum(d.expected_respondents for d in deployments)

    return {
        'report_type': report_type,
        'title': report_title(report_type),
        'audience': report_audience(report_type),
        'org': org,
        'cycle': cycle,
        'branch': branch,
        'scope_label': scope_label or (branch.name if branch else 'Whole organisation'),
        'generated_on': date.today().strftime('%d %B %Y'),
        'n': n,
        'responses_received': responses_received,
        'expected': expected,
        'response_rate': (responses_received / expected * 100) if expected else None,
        'min_reporting_n': min_n,
        'scoring_method': cycle.scoring_method,
        'scoring_method_label': (
            'the mean of the three subdomain scores'
            if cycle.scoring_method == 'mean_of_subdomains'
            else 'the mean of all valid domain items'),
        'outcome_rows': outcome_rows,
        'domain_rows': domain_rows,
        'subdomain_rows': subdomain_rows,
        'contrasts': contrasts,
        'priority': priority,
        'comparisons': comparisons,
        'trend': trend,
        'actions': list(improvement_actions),
        'action_counts': trend_module.actions_alongside(improvement_actions),
        'coverage_rows': coverage_rows,
        'narrative': narrative or {},
        'intervention_map': domains.INTERVENTION_MAP,
        'contrast_floor': association.contrast_floor(min_n),
        # Caveats, from the single source. Templates render these; they never
        # restate them, so a reworded report cannot soften one.
        'association_caveat': domains.ASSOCIATION_CAVEAT,
        'perception_caveat': domains.PERCEPTION_CAVEAT,
        'trend_caveat': domains.TREND_CAVEAT,
        'bands_note': domains.BANDS_DEFERRED_NOTE,
        'daunting_caveat': domains.DAUNTING_CAVEAT,
        'intervention_note': domains.INTERVENTION_MAP_NOTE,
    }


def add_charts(context):
    """
    Render the PNGs. Separated from build_context so the XLSX path can skip
    matplotlib entirely — a spreadsheet does not need images, and generating
    them is the slowest part of a report.
    """
    context['chart_legend'] = charts.anchor_legend()
    context['chart_outcome'] = charts.score_bars(
        [(domains.subdomain_label(*pair),
          result.mean if isinstance(result, sc.GroupResult) else None)
         for pair, result in context['outcome_rows']],
        title='Overall Satisfaction, by subdomain')

    reference = None
    reportable = [r for _, r in context['domain_rows']
                  if isinstance(r, sc.GroupResult)]
    if reportable:
        reference = sum(r.mean for r in reportable) / len(reportable)

    context['chart_domains'] = charts.score_bars(
        [(domains.DOMAIN_LABELS[code],
          result.mean if isinstance(result, sc.GroupResult) else None)
         for code, result in context['domain_rows']],
        title='Diagnostic domains')

    context['chart_subdomains'] = charts.score_bars(
        [(domains.subdomain_label(*pair),
          result.mean if isinstance(result, sc.GroupResult) else None)
         for pair, result in context['subdomain_rows']],
        reference=reference, title='Diagnostic subdomains')

    context['chart_contrasts'] = charts.contrast_bars(
        [(domains.DOMAIN_LABELS[code], contrast)
         for code, contrast in context['contrasts']],
        outcome_label=domains.subdomain_label(*association.DEFAULT_OUTCOME),
        title='Lowest and highest thirds compared')

    context['chart_coverage'] = charts.coverage_bars(
        context['coverage_rows'], title='Not applicable, by subdomain')

    if context.get('trend'):
        context['chart_trend'] = charts.trend_bars(
            [(change.measure_label if hasattr(change, 'measure_label')
              else str(measure), change)
             for measure, change in context['trend'].changes],
            title='Change since the previous wave')

    context['chart_distributions'] = {
        pair: charts.distribution_bar(result.distribution)
        for pair, result in (context['outcome_rows'] + context['subdomain_rows'])
        if isinstance(result, sc.GroupResult)
    }
    return context


# ── PDF ────────────────────────────────────────────────────────────────────

_TEMPLATES = {
    EXECUTIVE: 'reports/executive.html',
    HR_OD: 'reports/hr_od.html',
    MANAGEMENT: 'reports/management.html',
}


def build_pdf(context):
    """Render to PDF bytes. Synchronous, inside the request, by design."""
    from flask import render_template
    from weasyprint import HTML

    html = render_template(_TEMPLATES[context['report_type']], **context)
    return HTML(string=html).write_pdf()


# ── XLSX ───────────────────────────────────────────────────────────────────

def _header(sheet, context, subtitle=None):
    from openpyxl.styles import Alignment, Font

    sheet['A1'] = context['title']
    sheet['A1'].font = Font(size=14, bold=True)
    sheet['A2'] = f"{context['org'].name} — {context['cycle'].cycle_name}"
    sheet['A2'].font = Font(size=11)
    sheet['A3'] = (
        f"Scope: {context['scope_label']} · Responses: {context['n']} · "
        f"Generated: {context['generated_on']} · "
        f"Reporting threshold: n ≥ {context['min_reporting_n']}"
    )
    sheet['A3'].font = Font(size=9, italic=True)
    sheet['A4'] = 'CONFIDENTIAL'
    sheet['A4'].font = Font(size=9, bold=True)
    row = 6
    if subtitle:
        sheet[f'A{row}'] = subtitle
        sheet[f'A{row}'].font = Font(size=11, bold=True)
        row += 1
    for column in 'ABCDEFG':
        sheet.column_dimensions[column].width = 22
    sheet.column_dimensions['A'].width = 42
    sheet['A3'].alignment = Alignment(wrap_text=False)
    return row + 1


def _table(sheet, start_row, headers, rows):
    from openpyxl.styles import Font

    for index, heading in enumerate(headers):
        cell = sheet.cell(row=start_row, column=index + 1, value=heading)
        cell.font = Font(bold=True)
    for offset, row in enumerate(rows, start=1):
        for index, value in enumerate(row):
            sheet.cell(row=start_row + offset, column=index + 1, value=value)
    return start_row + len(rows) + 2


def _cell_value(result):
    """
    A suppressed cell writes its LABEL, never a blank.

    A blank in a spreadsheet is read as zero by a reader and by every formula
    that touches the column. "Suppressed (n < 5)" cannot be averaged by
    accident, which is the point.
    """
    if isinstance(result, sc.GroupResult):
        return round(result.mean, 2)
    return getattr(result, 'label', 'Not reported')


def build_xlsx(context):
    """
    The same figures as the PDF, in a workbook.

    Same title, same wording, same caveats — built from the same context, which
    is what makes parity structural rather than a thing to remember.
    """
    from openpyxl import Workbook

    workbook = Workbook()

    overview = workbook.active
    overview.title = 'Overview'
    row = _header(overview, context, 'Participation and method')
    row = _table(overview, row,
                 ['Measure', 'Value'],
                 [['Responses', context['n']],
                  ['Invited', context['expected']],
                  ['Response rate (%)',
                   round(context['response_rate'], 1)
                   if context['response_rate'] is not None else 'n/a'],
                  ['Minimum reporting threshold', context['min_reporting_n']],
                  ['Domain scoring method', context['scoring_method_label']],
                  ['Domains measured',
                   ', '.join(domains.DOMAIN_LABELS[d]
                             for d in (context['cycle'].assessed_domains or []))]])
    row = _table(overview, row, ['Limitations'],
                 [[context['bands_note']],
                  [context['association_caveat']],
                  [context['perception_caveat']],
                  ['No individual response appears in this report, and no '
                   'finding here may be used in a decision about an individual '
                   'employee.']])

    scores = workbook.create_sheet('Scores')
    row = _header(scores, context, 'Overall Satisfaction (reported separately)')
    row = _table(
        scores, row,
        ['Subdomain', 'Score', 'n', '% agree', '% neither', '% disagree',
         '% not applicable'],
        [[domains.subdomain_label(*pair), _cell_value(result),
          result.n if isinstance(result, sc.GroupResult) else '',
          round(result.distribution.favourable_pct or 0, 1)
          if isinstance(result, sc.GroupResult) else '',
          round(result.distribution.neutral_pct or 0, 1)
          if isinstance(result, sc.GroupResult) else '',
          round(result.distribution.unfavourable_pct or 0, 1)
          if isinstance(result, sc.GroupResult) else '',
          round(result.distribution.na_pct or 0, 1)
          if isinstance(result, sc.GroupResult) else '']
         for pair, result in context['outcome_rows']])

    row = _table(
        scores, row,
        ['Diagnostic subdomain', 'Score', 'n', '% agree', '% disagree',
         '% not applicable'],
        [[domains.subdomain_label(*pair), _cell_value(result),
          result.n if isinstance(result, sc.GroupResult) else '',
          round(result.distribution.favourable_pct or 0, 1)
          if isinstance(result, sc.GroupResult) else '',
          round(result.distribution.unfavourable_pct or 0, 1)
          if isinstance(result, sc.GroupResult) else '',
          round(result.distribution.na_pct or 0, 1)
          if isinstance(result, sc.GroupResult) else '']
         for pair, result in context['subdomain_rows']])

    assoc_sheet = workbook.create_sheet('Associations')
    row = _header(assoc_sheet, context,
                  'Diagnostic domains in relation to Overall Satisfaction')
    contrast_rows = []
    for code, contrast in context['contrasts']:
        if contrast:
            contrast_rows.append([
                domains.DOMAIN_LABELS[code],
                round(contrast.lower_mean, 2), round(contrast.upper_mean, 2),
                round(contrast.difference, 2),
                contrast.lower_n, contrast.upper_n])
        else:
            contrast_rows.append([domains.DOMAIN_LABELS[code],
                                  contrast.reason, '', '', '', ''])
    row = _table(assoc_sheet, row,
                 ['Domain', 'Lowest third', 'Highest third', 'Difference',
                  'n (lowest)', 'n (highest)'],
                 contrast_rows)
    row = _table(assoc_sheet, row, ['Note'], [[context['association_caveat']]])

    if context.get('priority'):
        row = _table(
            assoc_sheet, row,
            ['Subdomain', 'Score', 'Difference between thirds', 'Reading order'],
            [[entry.label, round(entry.score, 2),
              round(entry.contrast.difference, 2), entry.quadrant_label]
             for entry in context['priority'].entries])

    if context['comparisons']:
        groups = workbook.create_sheet('Groups')
        row = _header(groups, context, 'Comparisons across organisational groups')
        for comparison in context['comparisons']:
            row = _table(
                groups, row,
                [comparison.segment_label, 'Score', 'n', 'Difference from whole'],
                [[label, _cell_value(result),
                  result.n if isinstance(result, sc.GroupResult) else '',
                  round(difference, 2) if difference is not None else '']
                 for _code, label, result, difference in comparison.cells])
            row = _table(groups, row, ['Note'],
                         [['Groups appear in their defined order. This is not a '
                           'ranking.'], [comparison.caveat]])

    if context.get('trend'):
        trend_sheet = workbook.create_sheet('Change')
        row = _header(trend_sheet, context,
                      f"Change since {context['trend'].previous_cycle_id}")
        trend_rows = []
        for measure, change in context['trend'].changes:
            if getattr(change, 'comparable', False):
                trend_rows.append([change.measure_label,
                                   round(change.previous.mean, 2),
                                   round(change.current.mean, 2),
                                   round(change.difference, 2),
                                   change.previous_n, change.current_n])
            else:
                label = (change.measure_label
                         if hasattr(change, 'measure_label') else str(measure))
                trend_rows.append([label, change.reason, '', '', '', ''])
        row = _table(trend_sheet, row,
                     ['Measure', 'Previous wave', 'This wave', 'Change',
                      'n (previous)', 'n (this)'], trend_rows)
        row = _table(trend_sheet, row, ['Note'], [[context['trend_caveat']]])
        if context['trend'].composition_note:
            row = _table(trend_sheet, row, ['Composition'],
                         [[context['trend'].composition_note]])

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ── Narrative payload ──────────────────────────────────────────────────────

def narrative_payload(context):
    """
    The figures handed to the interpretation service.

    ALREADY SUPPRESSED. A group below the threshold is ABSENT here, not present
    with a flag — the model cannot narrate what it was not given, which is a
    stronger guarantee than asking it not to. Nothing identifying is included
    because nothing identifying exists.
    """
    def _score(result):
        if not isinstance(result, sc.GroupResult):
            return None
        return {
            'score': round(result.mean, 2),
            'n': result.n,
            'pct_agree': round(result.distribution.favourable_pct or 0, 1),
            'pct_disagree': round(result.distribution.unfavourable_pct or 0, 1),
            'pct_not_applicable': round(result.distribution.na_pct or 0, 1),
            'spread': round(result.dispersion.sd, 2),
        }

    payload = {
        'organisation': context['org'].name,
        'wave': context['cycle'].cycle_name,
        'scope': context['scope_label'],
        'responses': context['n'],
        'response_rate_pct': (round(context['response_rate'], 1)
                              if context['response_rate'] is not None else None),
        'minimum_reporting_threshold': context['min_reporting_n'],
        'domain_scoring_method': context['scoring_method_label'],
        'overall_satisfaction_subdomains': {
            domains.subdomain_label(*pair): _score(result)
            for pair, result in context['outcome_rows']
        },
        'diagnostic_domains': {
            domains.DOMAIN_LABELS[code]: _score(result)
            for code, result in context['domain_rows']
        },
        'diagnostic_subdomains': {
            domains.subdomain_label(*pair): _score(result)
            for pair, result in context['subdomain_rows']
        },
        'comparison_of_lowest_and_highest_thirds': {
            domains.DOMAIN_LABELS[code]: (
                {'lowest_third': round(c.lower_mean, 2),
                 'highest_third': round(c.upper_mean, 2),
                 'difference': round(c.difference, 2),
                 'ranked_by': c.outcome_label}
                if c else {'not_computed': c.reason})
            for code, c in context['contrasts']
        },
        'reading_order': (
            [{'subdomain': entry.label, 'score': round(entry.score, 2),
              'difference_between_thirds': round(entry.contrast.difference, 2),
              'reading_order': entry.quadrant_label}
             for entry in context['priority'].entries]
            if context['priority'] else None),
        # §11's own words, so the model can only name response categories the
        # specification actually lists for that subdomain.
        'available_response_categories': {
            domains.subdomain_label(d, s): entry['categories']
            for (d, s), entry in domains.INTERVENTION_MAP.items()
        },
    }

    # COURSES THE MODEL MAY NAME, filtered before it sees them.
    #
    # The three Reward subdomains carry no course and are absent here entirely,
    # carrying their §8.5 guidance instead. A model cannot recommend a course
    # it was never shown, which is a stronger guarantee than instructing it not
    # to — and the instruction is in the prompt as well.
    if context.get('recommendations'):
        payload['available_courses'] = {
            rec['subdomain']: [
                {'code': course.intervention_code, 'title': course.title,
                 'programme': course.source_programme}
                for course in rec['courses']
            ] or None
            for rec in context['recommendations']
        }
        payload['reward_findings_are_not_addressed_by_training'] = {
            domains.subdomain_label(d, s): guidance
            for (d, s), guidance in __import__(
                'app.catalogue', fromlist=['REWARD_GUIDANCE']
            ).REWARD_GUIDANCE.items()
        }

    if context.get('trend'):
        payload['change_since_previous_wave'] = {
            (change.measure_label if hasattr(change, 'measure_label')
             else str(measure)): (
                {'previous': round(change.previous.mean, 2),
                 'current': round(change.current.mean, 2),
                 'change': round(change.difference, 2),
                 'previous_n': change.previous_n, 'current_n': change.current_n}
                if getattr(change, 'comparable', False)
                else {'not_comparable': change.reason})
            for measure, change in context['trend'].changes
        }
        payload['composition_note'] = context['trend'].composition_note

    if context['comparisons']:
        payload['group_comparisons'] = {
            comparison.segment_label: [
                {'group': label, 'score': round(result.mean, 2), 'n': result.n,
                 'difference_from_whole': (round(difference, 2)
                                           if difference is not None else None)}
                for _code, label, result, difference in comparison.cells
                if isinstance(result, sc.GroupResult)
            ]
            for comparison in context['comparisons']
        }

    return payload


NARRATIVE_SECTIONS = {
    EXECUTIVE: {
        'executive_summary':
            'Four to six sentences for senior leadership: what this wave '
            'measured, the participation, and the two or three things most '
            'worth their attention. Quantitative and specific.',
        'key_findings':
            'Two to three paragraphs across Task, Relationships and Reward '
            'Satisfaction, naming figures and comparisons.',
        'areas_to_investigate':
            'One paragraph on where to look first and what further evidence '
            'would be needed, drawn only from the reading order supplied.',
        'limitations':
            'One paragraph: what this wave does not establish. Include that '
            'associations are not causes, that score bands are not validated '
            'for this instrument, and anything notable about coverage or '
            'group sizes.',
    },
    HR_OD: {
        'executive_summary':
            'Four to six sentences for an HR and OD audience, more technical '
            'than the executive version.',
        'score_commentary':
            'Two to three paragraphs on the domain and subdomain figures and '
            'their response distributions.',
        'association_commentary':
            'Two paragraphs on the comparison between the lowest and highest '
            'thirds. Describe association only.',
        'group_commentary':
            'One paragraph on differences between organisational groups, '
            'describing the spread rather than ranking the groups.',
        'coverage_commentary':
            'One paragraph on what the not-applicable rates indicate, treating '
            'a high rate as a finding rather than as missing data.',
        'intervention_pathways':
            'One or two paragraphs on possible response categories, drawn ONLY '
            'from the available response categories supplied for each '
            'subdomain.',
        'limitations':
            'One paragraph on what this wave does not establish.',
    },
    MANAGEMENT: {
        'summary':
            'Three to four sentences addressed to this group\'s manager about '
            'their own group\'s results.',
        'strengths':
            'One paragraph on what this group reports most positively, with '
            'figures.',
        'attention':
            'One paragraph on what this group reports least positively, with '
            'figures, and without diagnosing a cause.',
        'practical_actions':
            'One paragraph of practical actions a line manager can take '
            'themselves. Nothing requiring a change to pay, grading or '
            'organisational policy, which are not theirs to change.',
        'discussion_questions':
            'Three or four questions a manager could put to their team to '
            'understand these results better, as one string separated by '
            'semicolons.',
        'limitations':
            'One short paragraph: group size, that no individual response is '
            'visible, and that these findings must not be used in any decision '
            'about an individual.',
    },
}

# ── Assembly: shared by BOTH blueprints ────────────────────────────────────
#
# Takes explicit arguments and reads no request context, so the L1 blueprint can
# call it too. L1 HAS NO org_id — every query in app/org/ is scoped by
# current_user.org_id, which L1 can never satisfy — so a report path that lives
# only in the org blueprint is a report path L1 cannot reach, however the
# permission table reads. That is the mistake the sibling product made with its
# candidate purge.

def assemble(db_session, org, cycle, report_type, deployments,
             scope_branch=None, segments=(), narrative=None, with_charts=False):
    """
    Gate, then build the context. Returns the context dict.

    Raises ReportNotAvailable with a client-readable reason — the caller shows
    it. Raising rather than returning a flag is deliberate: a caller that
    forgets to check a return value renders an empty report, and an empty
    report looks finished.
    """
    from app.analytics import scoring as scoring_module
    from app.models import ImprovementActions, MeasurementCycle, SurveyDeployment

    if scope_branch is not None:
        deployments = [d for d in deployments if d.branch_id == scope_branch.id]

    profiles = scoring_module.load_profiles(
        db_session, [d.id for d in deployments], method=cycle.scoring_method)

    scope_label = scope_branch.name if scope_branch else 'Whole organisation'
    require_reportable(report_type, cycle, deployments, profiles, scope_label)

    previous = None
    if cycle.previous_cycle_id:
        earlier = db_session.get(MeasurementCycle, cycle.previous_cycle_id)
        if earlier is not None and earlier.org_id == cycle.org_id:
            earlier_waves = (db_session.query(SurveyDeployment)
                             .filter_by(cycle_id=earlier.id, org_id=cycle.org_id)
                             .filter(SurveyDeployment.archived_at.is_(None)).all())
            if scope_branch is not None:
                earlier_waves = [w for w in earlier_waves
                                 if w.branch_id == scope_branch.id]
            earlier_profiles = scoring_module.load_profiles(
                db_session, [w.id for w in earlier_waves],
                method=earlier.scoring_method)
            if earlier_profiles:
                previous = (earlier_profiles,
                            earlier.assessed_domains or domains.DOMAINS,
                            earlier.id)

    actions = (db_session.query(ImprovementActions)
               .filter_by(org_id=cycle.org_id, cycle_id=cycle.id).all())

    context = build_context(
        report_type=report_type, org=org, cycle=cycle, deployments=deployments,
        profiles=profiles, branch=scope_branch, scope_label=scope_label,
        segments=segments, previous=previous, improvement_actions=actions,
        narrative=narrative)

    # Catalogue recommendations are attached HERE rather than in
    # build_context(), which takes no database session — keeping the context
    # builder usable from a test with hand-made profiles and no catalogue.
    #
    # for_priority() follows the order the engine produced. Two places deciding
    # what matters most is two places to disagree.
    from app import catalogue as catalogue_module
    context['recommendations'] = catalogue_module.for_priority(
        db_session, context.get('priority'))
    context['catalogue_by_cell'] = {
        (c['domain_code'], c['subdomain_code']): c
        for c in catalogue_module.coverage(db_session)
    }

    if with_charts:
        add_charts(context)
    return context
