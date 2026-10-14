"""
L2 (org admin) and L3 (branch admin).

BRANCH ISOLATION COMES FROM ROUTE-LEVEL SCOPED QUERIES, NOT FROM RLS.
trainiq's CLAUDE.md is explicit and it applies unchanged here: the app connects
as the table owner and bypasses row-level security entirely. The Supabase
policies exist to shield the anon key and nothing more. Never write a
branch-aware RLS predicate, and never treat a policy as a substitute for
_scoped().

EVERY query in this module goes through _scoped() or is explicitly filtered by
current_user.org_id. A query that forgets is a cross-tenant data leak, and on
this product the data is employee feedback about an employer.
"""
import re
from datetime import datetime, timezone

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from app import db, domains
from app.audit import (
    BRANCH_CREATE, BRANCH_DEACTIVATE, BRANCH_REACTIVATE, CYCLE_ARCHIVE,
    CYCLE_CLOSE, CYCLE_CREATE, CYCLE_EDIT, CYCLE_OPEN, SEGMENT_CREATE,
    SEGMENT_EDIT, USER_CREATE, branch_payload, cycle_payload, record_audit,
    segment_payload, user_payload,
)
from app.auth.routes import require_capability, require_role_any
from app.models import (
    Branch, MeasurementCycle, Segment, SegmentOption, SurveyDeployment,
    SurveyItem, User,
)
from app.org import org_bp
from app.permissions import (
    BRANCH_ADMIN, MANAGE_BRANCH_ADMINS, MANAGE_BRANCHES, MANAGE_CYCLES,
    MANAGE_SEGMENTS, ORG_ADMIN, VIEW_TEMPLATES, can, is_branch_scoped,
    visible_branch_ids,
)

_ORG_TIERS = (ORG_ADMIN, BRANCH_ADMIN)

# Cycle ids appear in report filenames and in conversation, so they are
# constrained to something safe to put in a path and readable in an email.
_CYCLE_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{2,49}$')


def _scoped(query, model):
    """
    Restrict a query to what this user may see. USE THIS, ALWAYS.

    `visible_branch_ids` returns None for L1/L2 meaning "apply no branch
    filter" — callers must not read that as "no branches", which would show an
    org admin nothing. An L3 with no branch assigned gets an empty list and
    therefore sees nothing, which is the safe default.

    L3 also never sees org-wide (branch_id IS NULL) rows: those were created
    deliberately above the branch layer, and a branch admin seeing the whole
    organisation's wave would defeat the point of the tier.
    """
    query = query.filter(model.org_id == current_user.org_id)
    branch_ids = visible_branch_ids(current_user)
    if branch_ids is None:
        return query
    return query.filter(model.branch_id.in_(branch_ids))


def _my_branch_id():
    """The branch a new row created by this user belongs to. NULL = org-wide."""
    return current_user.branch_id if is_branch_scoped(current_user) else None


# ── Dashboard ───────────────────────────────────────────────────────────────

@org_bp.route('/')
@require_role_any(*_ORG_TIERS)
def dashboard():
    """
    WHAT IS DELIBERATELY NOT AT THE TOP OF THIS PAGE: a single
    "Organisational Satisfaction 3.4 / 5" headline.

    Specification §10.3 forbids the composite — the three Overall Satisfaction
    subdomains "should initially be reported separately. An overall GEM
    composite should only be introduced if validation supports its
    interpretation and the intended use" — and §10.5 forbids the band the
    number would imply. Both sibling products put a figure in this slot and it
    is the first thing a client asks for here.

    The visual slot and its styling are preserved. The metric is participation
    plus the three Overall Satisfaction subdomains side by side: the same
    amount of room, a more honest answer to the same question.
    """
    deployments = _scoped(db.session.query(SurveyDeployment), SurveyDeployment) \
        .filter(SurveyDeployment.archived_at.is_(None)).all()
    open_waves = [d for d in deployments if not d.closed and d.deployed_at]
    responses = sum(d.responses_received for d in deployments)
    expected = sum(d.expected_respondents for d in deployments)

    cycles = _scoped(db.session.query(MeasurementCycle), MeasurementCycle) \
        .filter(MeasurementCycle.archived_at.is_(None)) \
        .order_by(MeasurementCycle.created_at.desc()).limit(5).all()

    # The floor in force for this org, shown so the number on screen is never
    # unexplained. Cycles may raise it; none may lower it.
    thresholds = [c.min_reporting_n for c in cycles] or [5]

    return render_template(
        'org/dashboard.html',
        branch_scoped=is_branch_scoped(current_user),
        gem_subdomains=[(domains.GEM, s) for s in domains.SUBDOMAINS[domains.GEM]],
        stats={
            'responses': responses,
            'expected': expected,
            'rate': round(responses / expected * 100) if expected else None,
            'open_waves': len(open_waves),
            'min_n': min(thresholds),
        },
        cycles=cycles,
    )


# ── Branches ────────────────────────────────────────────────────────────────

@org_bp.route('/branches')
@require_capability(MANAGE_BRANCHES)
def branches_list():
    branches = (db.session.query(Branch)
                .filter_by(org_id=current_user.org_id)
                .order_by(Branch.is_active.desc(), Branch.name).all())
    admins = dict(
        db.session.query(User.branch_id, func.count(User.id))
        .filter_by(org_id=current_user.org_id, role=BRANCH_ADMIN)
        .group_by(User.branch_id).all()
    )
    return render_template('org/branches.html', branches=branches, admins=admins)


@org_bp.route('/branches/new', methods=['GET', 'POST'])
@require_capability(MANAGE_BRANCHES)
def branch_new():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        if not name:
            flash('A branch name is required.', 'error')
        elif db.session.query(Branch).filter_by(
                org_id=current_user.org_id, name=name).first():
            flash(f'{name} already exists.', 'error')
        else:
            branch = Branch(org_id=current_user.org_id, name=name, is_active=True)
            db.session.add(branch)
            db.session.flush()
            record_audit(BRANCH_CREATE, 'branch', branch.id,
                         f'Created branch {name}',
                         org_id=current_user.org_id, branch_id=branch.id,
                         payload=branch_payload(branch))
            db.session.commit()
            flash(f'Created {name}.', 'success')
            return redirect(url_for('org.branches_list'))
    return render_template('org/branch_form.html')


@org_bp.route('/branches/<uuid:branch_id>/toggle', methods=['POST'])
@require_capability(MANAGE_BRANCHES)
def branch_toggle(branch_id):
    """
    SOFT DELETE ONLY. A branch is never hard-deleted and a branch_id is never
    reassigned: historical cycles, deployments and anonymous sessions all point
    at this row, and the responses cannot be re-collected to repair the link.
    """
    branch = db.session.get(Branch, branch_id)
    if branch is None or branch.org_id != current_user.org_id:
        abort(404)

    branch.is_active = not branch.is_active
    action = BRANCH_REACTIVATE if branch.is_active else BRANCH_DEACTIVATE
    verb = 'Reactivated' if branch.is_active else 'Deactivated'
    record_audit(action, 'branch', branch.id, f'{verb} branch {branch.name}',
                 org_id=current_user.org_id, branch_id=branch.id,
                 payload=branch_payload(branch))
    db.session.commit()
    flash(f'{verb} {branch.name}. Its historical results are unchanged.',
          'success')
    return redirect(url_for('org.branches_list'))


@org_bp.route('/branches/<uuid:branch_id>/admins/new', methods=['GET', 'POST'])
@require_capability(MANAGE_BRANCH_ADMINS)
def branch_admin_new(branch_id):
    branch = db.session.get(Branch, branch_id)
    if branch is None or branch.org_id != current_user.org_id:
        abort(404)

    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        full_name = (request.form.get('full_name') or '').strip() or None

        if not email or '@' not in email:
            flash('A valid email address is required.', 'error')
        elif db.session.query(User).filter_by(email=email).first():
            flash(f'{email} already has an account. Email is unique across the '
                  'whole platform, not per organisation.', 'error')
        elif len(password) < 8:
            flash('Choose a password of at least 8 characters.', 'error')
        else:
            user = User(org_id=current_user.org_id, branch_id=branch.id,
                        email=email, role=BRANCH_ADMIN, full_name=full_name,
                        is_active=True)
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            record_audit(USER_CREATE, 'user', user.id,
                         f'Created branch admin {email} for {branch.name}',
                         org_id=current_user.org_id, branch_id=branch.id,
                         payload=user_payload(user))
            db.session.commit()
            flash(f'Created {email} for {branch.name}. Give them the password '
                  'directly; it is not emailed.', 'success')
            return redirect(url_for('org.branches_list'))

    return render_template('org/branch_admin_form.html', branch=branch)


# ── Measurement cycles ──────────────────────────────────────────────────────

@org_bp.route('/cycles')
@require_role_any(*_ORG_TIERS)
def cycles_list():
    cycles = _scoped(db.session.query(MeasurementCycle), MeasurementCycle) \
        .filter(MeasurementCycle.archived_at.is_(None)) \
        .order_by(MeasurementCycle.created_at.desc()).all()
    waves = dict(
        db.session.query(SurveyDeployment.cycle_id, func.count(SurveyDeployment.id))
        .filter(SurveyDeployment.org_id == current_user.org_id)
        .group_by(SurveyDeployment.cycle_id).all()
    )
    return render_template('org/cycles.html', cycles=cycles, waves=waves)


@org_bp.route('/cycles/new', methods=['GET', 'POST'])
@require_capability(MANAGE_CYCLES)
def cycle_new():
    """
    Create a measurement wave.

    The cycle is a WAVE, not a person and not a position — the sibling
    products' cycles are both. "Employee Satisfaction Survey 2026 H1".
    """
    previous = _scoped(db.session.query(MeasurementCycle), MeasurementCycle) \
        .filter(MeasurementCycle.archived_at.is_(None)) \
        .order_by(MeasurementCycle.created_at.desc()).all()

    if request.method == 'POST':
        form = _cycle_form_values(request.form)
        error = _validate_cycle(form, creating=True)
        if error:
            flash(error, 'error')
            return render_template('org/cycle_form.html', cycle=None,
                                   form=form, previous=previous,
                                   all_domains=domains.DOMAINS)

        cycle = MeasurementCycle(
            id=form['id'],
            org_id=current_user.org_id,
            branch_id=_my_branch_id(),
            cycle_name=form['cycle_name'],
            purpose_statement=form['purpose_statement'],
            assessed_domains=form['assessed_domains'],
            previous_cycle_id=form['previous_cycle_id'],
            status='draft',
            min_reporting_n=form['min_reporting_n'],
            scoring_method=form['scoring_method'],
            retention_months=form['retention_months'],
            is_pilot=form['is_pilot'],
            created_by=current_user.id,
        )
        db.session.add(cycle)
        db.session.flush()
        record_audit(CYCLE_CREATE, 'cycle', cycle.id,
                     f'Created cycle {cycle.cycle_name}',
                     org_id=current_user.org_id, branch_id=cycle.branch_id,
                     payload=cycle_payload(cycle))
        db.session.commit()
        flash(f'Created {cycle.cycle_name}. It is a draft until you open it.',
              'success')
        return redirect(url_for('org.cycle_detail', cycle_id=cycle.id))

    return render_template('org/cycle_form.html', cycle=None,
                           form=_cycle_defaults(), previous=previous,
                           all_domains=domains.DOMAINS)


@org_bp.route('/cycles/<cycle_id>')
@require_role_any(*_ORG_TIERS)
def cycle_detail(cycle_id):
    cycle = _cycle_or_404(cycle_id)
    waves = (db.session.query(SurveyDeployment)
             .filter_by(cycle_id=cycle.id, org_id=current_user.org_id)
             .order_by(SurveyDeployment.deployed_at.desc().nullslast()).all())
    return render_template('org/cycle_detail.html', cycle=cycle, waves=waves,
                           responses=sum(w.responses_received for w in waves))


@org_bp.route('/cycles/<cycle_id>/edit', methods=['GET', 'POST'])
@require_capability(MANAGE_CYCLES)
def cycle_edit(cycle_id):
    """
    Edit a cycle.

    TWO FIELDS FREEZE ONCE A RESPONSE EXISTS: `assessed_domains` and
    `scoring_method`.

    Narrowing the domains mid-wave makes the two halves of one wave
    incomparable and breaks the trend section, which would rather refuse than
    plot a missing domain as a drop. Changing the scoring method silently
    changes every figure already reported from this wave. Neither failure is
    visible in the data afterwards, which is why they are blocked here rather
    than warned about.
    """
    cycle = _cycle_or_404(cycle_id)
    locked = _cycle_has_responses(cycle)
    previous = _scoped(db.session.query(MeasurementCycle), MeasurementCycle) \
        .filter(MeasurementCycle.id != cycle.id) \
        .filter(MeasurementCycle.archived_at.is_(None)).all()

    if request.method == 'POST':
        form = _cycle_form_values(request.form, existing=cycle)
        if locked:
            form['assessed_domains'] = list(cycle.assessed_domains or [])
            form['scoring_method'] = cycle.scoring_method
        error = _validate_cycle(form, creating=False)
        if error:
            flash(error, 'error')
            return render_template('org/cycle_form.html', cycle=cycle,
                                   form=form, previous=previous,
                                   all_domains=domains.DOMAINS, locked=locked)

        cycle.cycle_name = form['cycle_name']
        cycle.purpose_statement = form['purpose_statement']
        cycle.previous_cycle_id = form['previous_cycle_id']
        cycle.min_reporting_n = form['min_reporting_n']
        cycle.retention_months = form['retention_months']
        cycle.is_pilot = form['is_pilot']
        if not locked:
            cycle.assessed_domains = form['assessed_domains']
            cycle.scoring_method = form['scoring_method']

        record_audit(CYCLE_EDIT, 'cycle', cycle.id,
                     f'Edited cycle {cycle.cycle_name}',
                     org_id=current_user.org_id, branch_id=cycle.branch_id,
                     payload=cycle_payload(cycle))
        db.session.commit()
        flash('Saved.', 'success')
        return redirect(url_for('org.cycle_detail', cycle_id=cycle.id))

    return render_template('org/cycle_form.html', cycle=cycle,
                           form=_cycle_form_values(None, existing=cycle),
                           previous=previous, all_domains=domains.DOMAINS,
                           locked=locked)


@org_bp.route('/cycles/<cycle_id>/status', methods=['POST'])
@require_capability(MANAGE_CYCLES)
def cycle_status(cycle_id):
    """
    draft -> open -> closed. One direction only.

    Reopening a closed wave is deliberately absent. The retention clock starts
    at close (§13.8), a closed wave's figures have been reported, and letting
    new responses arrive afterwards changes a number somebody has already acted
    on. Run a new wave and link it with `previous_cycle_id` instead — which is
    also what makes it a trend rather than a revision.
    """
    cycle = _cycle_or_404(cycle_id)
    target = (request.form.get('status') or '').strip()
    now = datetime.now(timezone.utc)

    if target == 'open' and cycle.status == 'draft':
        if not _org_segments_ready():
            flash('Define at least one reporting segment with options, or '
                  'deactivate them all, before opening a wave. Segments freeze '
                  'on the first response.', 'error')
            return redirect(url_for('org.cycle_detail', cycle_id=cycle.id))
        cycle.status = 'open'
        cycle.opened_at = now
        action, message = CYCLE_OPEN, 'is open'
    elif target == 'closed' and cycle.status == 'open':
        cycle.status = 'closed'
        cycle.closed_at = now
        action, message = CYCLE_CLOSE, 'is closed — the retention clock starts now'
    else:
        flash(f'A {cycle.status} cycle cannot move to {target or "that state"}.',
              'error')
        return redirect(url_for('org.cycle_detail', cycle_id=cycle.id))

    record_audit(action, 'cycle', cycle.id,
                 f'{cycle.cycle_name} {message}',
                 org_id=current_user.org_id, branch_id=cycle.branch_id,
                 payload=cycle_payload(cycle))
    db.session.commit()
    flash(f'{cycle.cycle_name} {message}.', 'success')
    return redirect(url_for('org.cycle_detail', cycle_id=cycle.id))


@org_bp.route('/cycles/<cycle_id>/archive', methods=['POST'])
@require_capability(MANAGE_CYCLES)
def cycle_archive(cycle_id):
    """
    Soft delete. A cycle that has held any deployment is ARCHIVED, never hard
    deleted, so its reports and cost events keep their parent.
    """
    cycle = _cycle_or_404(cycle_id)
    waves = (db.session.query(SurveyDeployment)
             .filter_by(cycle_id=cycle.id).count())
    cycle.archived_at = datetime.now(timezone.utc)
    cycle.archived_by = current_user.id
    record_audit(CYCLE_ARCHIVE, 'cycle', cycle.id,
                 f'Archived cycle {cycle.cycle_name}',
                 org_id=current_user.org_id, branch_id=cycle.branch_id,
                 payload=cycle_payload(cycle, deployment_count=waves))
    db.session.commit()
    flash(f'Archived {cycle.cycle_name}.', 'success')
    return redirect(url_for('org.cycles_list'))


def _cycle_or_404(cycle_id):
    cycle = db.session.get(MeasurementCycle, cycle_id)
    if cycle is None or cycle.org_id != current_user.org_id:
        abort(404)
    # A branch admin reaches only its own branch's cycles — never an org-wide
    # one, and never a sibling's.
    branch_ids = visible_branch_ids(current_user)
    if branch_ids is not None and cycle.branch_id not in branch_ids:
        abort(404)
    return cycle


def _cycle_has_responses(cycle):
    return bool(
        db.session.query(SurveyDeployment)
        .filter_by(cycle_id=cycle.id)
        .filter(SurveyDeployment.responses_received > 0).count()
    )


def _org_segments_ready():
    """
    True when the org has no active segments, or every active one has options.

    An active segment with no options is a dropdown with nothing in it on the
    survey form — and because segments freeze on the first response, it cannot
    be fixed once the wave is running.
    """
    segments = (db.session.query(Segment)
                .filter_by(org_id=current_user.org_id, is_active=True).all())
    return all(any(o.is_active for o in s.options) for s in segments)


def _cycle_defaults():
    from flask import current_app
    return {
        'id': '', 'cycle_name': '', 'purpose_statement': '',
        'assessed_domains': list(domains.DOMAINS),
        'previous_cycle_id': None,
        'min_reporting_n': current_app.config['MIN_REPORTING_N'],
        'scoring_method': 'mean_of_subdomains',
        'retention_months': 24,
        'is_pilot': False,
    }


def _cycle_form_values(form, existing=None):
    if form is None:
        return {
            'id': existing.id,
            'cycle_name': existing.cycle_name,
            'purpose_statement': existing.purpose_statement,
            'assessed_domains': list(existing.assessed_domains or []),
            'previous_cycle_id': existing.previous_cycle_id,
            'min_reporting_n': existing.min_reporting_n,
            'scoring_method': existing.scoring_method,
            'retention_months': existing.retention_months,
            'is_pilot': existing.is_pilot,
        }

    def _int(name, fallback):
        try:
            return int((form.get(name) or '').strip())
        except ValueError:
            return fallback

    return {
        'id': existing.id if existing else (form.get('id') or '').strip(),
        'cycle_name': (form.get('cycle_name') or '').strip(),
        'purpose_statement': (form.get('purpose_statement') or '').strip(),
        'assessed_domains': form.getlist('assessed_domains') or [],
        'previous_cycle_id': (form.get('previous_cycle_id') or '').strip() or None,
        'min_reporting_n': _int('min_reporting_n', 5),
        'scoring_method': (form.get('scoring_method') or 'mean_of_subdomains').strip(),
        'retention_months': _int('retention_months', 24),
        'is_pilot': form.get('is_pilot') == 'on',
    }


def _validate_cycle(form, creating):
    from flask import current_app

    if creating:
        if not _CYCLE_ID_RE.match(form['id'] or ''):
            return ('The cycle reference must be 3-50 characters of letters, '
                    'numbers, hyphens or underscores — it appears in report '
                    'filenames. For example JS-2026-H1.')
        if db.session.get(MeasurementCycle, form['id']) is not None:
            return f'{form["id"]} is already in use.'
    if not form['cycle_name']:
        return 'A cycle name is required.'

    # NOT NULL in the schema, and for a reason worth repeating at the point of
    # entry: specification §9.2 requires respondents be told the purpose, how
    # results will be used, and that there will be no retaliation, BEFORE the
    # first question. This text is what the survey landing page renders. A wave
    # that opens with nothing to say is the wave whose answers are not worth
    # having.
    if len(form['purpose_statement']) < 40:
        return ('Write a purpose statement of at least 40 characters. '
                'Respondents read it before the first question: what this is '
                'for, how the results will be used, who sees what, and that '
                'answering honestly carries no consequence.')

    if not form['assessed_domains']:
        return 'Select at least one domain.'
    unknown = [d for d in form['assessed_domains'] if d not in domains.DOMAINS]
    if unknown:
        return f'Unknown domain: {", ".join(unknown)}.'
    if domains.OUTCOME_DOMAIN not in form['assessed_domains']:
        # §1 and §10.4: the diagnostic domains are reported IN RELATION TO the
        # outcome domain. Without it there is nothing to relate them to, and
        # the product degenerates into three unanchored scores.
        return (f'{domains.DOMAIN_LABELS[domains.OUTCOME_DOMAIN]} is the '
                'outcome measure that the other domains are reported in '
                'relation to. A wave without it cannot produce a diagnostic.')

    floor = current_app.config['MIN_REPORTING_N']
    if form['min_reporting_n'] < floor:
        # The platform floor, never lowered. permissions.LOWER_MIN_N exists and
        # is held by no role precisely so this cannot be waived from a screen.
        return (f'The minimum reporting threshold cannot go below {floor}. '
                'It may be raised.')
    if not 3 <= form['retention_months'] <= 120:
        return 'Retention must be between 3 and 120 months.'
    if form['scoring_method'] not in ('mean_of_subdomains', 'mean_of_items'):
        return 'Unknown scoring method.'
    return None


# ── Segments ────────────────────────────────────────────────────────────────

@org_bp.route('/segments')
@require_role_any(*_ORG_TIERS)
def segments_list():
    """
    L3 may VIEW segments but not define them (the capability gate is on the
    write routes).

    If two branches could each add their own options, "Finance", "finance" and
    "Fin." become three groups and none of them reaches the reporting
    threshold. The data is not wrong — it is simply gone, and nothing in the
    system would report that it had been lost.
    """
    segments = (db.session.query(Segment)
                .filter_by(org_id=current_user.org_id)
                .order_by(Segment.sort_order, Segment.label).all())
    return render_template('org/segments.html', segments=segments,
                           can_manage=can(current_user, MANAGE_SEGMENTS))


@org_bp.route('/segments/<uuid:segment_id>', methods=['GET', 'POST'])
@require_capability(MANAGE_SEGMENTS)
def segment_detail(segment_id):
    segment = _segment_or_404(segment_id)

    if request.method == 'POST':
        if segment.frozen_at:
            # Not a permission failure — a correctness one. Renaming an option
            # after responses exist silently re-groups answers given under the
            # old meaning, and the response rows themselves are unchanged, so
            # nothing in the data would ever reveal it.
            flash('This segment is frozen: a wave has already collected '
                  'responses against its options. Renaming one now would '
                  'silently re-group answers that were given under the old '
                  'meaning.', 'error')
            return redirect(url_for('org.segment_detail', segment_id=segment.id))

        action = request.form.get('action')
        if action == 'add_option':
            code = (request.form.get('option_code') or '').strip().upper()
            label = (request.form.get('option_label') or '').strip()
            if not code or not label:
                flash('An option needs both a code and a label.', 'error')
            elif db.session.query(SegmentOption).filter_by(
                    segment_id=segment.id, option_code=code).first():
                flash(f'{code} already exists in this segment.', 'error')
            else:
                db.session.add(SegmentOption(
                    segment_id=segment.id, option_code=code, option_label=label,
                    sort_order=len(segment.options) + 1, is_active=True))
                record_audit(SEGMENT_EDIT, 'segment', segment.id,
                             f'Added option {code} to {segment.label}',
                             org_id=current_user.org_id,
                             payload=segment_payload(segment))
                db.session.commit()
                flash(f'Added {label}.', 'success')
        elif action == 'toggle_active':
            segment.is_active = not segment.is_active
            record_audit(SEGMENT_EDIT, 'segment', segment.id,
                         f'{"Activated" if segment.is_active else "Deactivated"} '
                         f'{segment.label}',
                         org_id=current_user.org_id,
                         payload=segment_payload(segment))
            db.session.commit()
            flash('Saved.', 'success')
        elif action == 'rename':
            label = (request.form.get('label') or '').strip()
            if label:
                segment.label = label
                record_audit(SEGMENT_EDIT, 'segment', segment.id,
                             f'Renamed segment to {label}',
                             org_id=current_user.org_id,
                             payload=segment_payload(segment))
                db.session.commit()
                flash('Saved.', 'success')
        return redirect(url_for('org.segment_detail', segment_id=segment.id))

    return render_template('org/segment_detail.html', segment=segment)


@org_bp.route('/segments/new', methods=['GET', 'POST'])
@require_capability(MANAGE_SEGMENTS)
def segment_new():
    if request.method == 'POST':
        key = (request.form.get('segment_key') or '').strip().lower()
        label = (request.form.get('label') or '').strip()
        if not key or not label:
            flash('A segment needs both a key and a label.', 'error')
        elif not re.match(r'^[a-z][a-z0-9_]{1,49}$', key):
            flash('The key must be lower-case letters, numbers and '
                  'underscores — it is stored on every response.', 'error')
        elif db.session.query(Segment).filter_by(
                org_id=current_user.org_id, segment_key=key).first():
            flash(f'{key} already exists.', 'error')
        else:
            segment = Segment(org_id=current_user.org_id, segment_key=key,
                              label=label, collect_at_survey=True,
                              is_active=False, sort_order=99)
            db.session.add(segment)
            db.session.flush()
            record_audit(SEGMENT_CREATE, 'segment', segment.id,
                         f'Created segment {label}',
                         org_id=current_user.org_id,
                         payload=segment_payload(segment))
            db.session.commit()
            flash(f'Created {label}. Add its options, then activate it.',
                  'success')
            return redirect(url_for('org.segment_detail', segment_id=segment.id))
    return render_template('org/segment_form.html')


def _segment_or_404(segment_id):
    segment = db.session.get(Segment, segment_id)
    if segment is None or segment.org_id != current_user.org_id:
        abort(404)
    return segment


# ── Instrument (read-only for both tiers) ───────────────────────────────────

@org_bp.route('/instrument')
@require_capability(VIEW_TEMPLATES)
def instrument():
    """
    The org's own frozen copy of the 48 items. READ ONLY for L2 and L3 —
    editing is L1's.
    """
    items = (db.session.query(SurveyItem)
             .filter_by(org_id=current_user.org_id, version=1)
             .order_by(SurveyItem.sort_order).all())
    grouped = []
    for domain_code in domains.DOMAINS:
        in_domain = [i for i in items if i.domain_code == domain_code]
        if in_domain:
            grouped.append((domain_code, in_domain))
    return render_template('org/instrument.html', grouped=grouped,
                           total=len(items))


# ── Placeholders ────────────────────────────────────────────────────────────

@org_bp.route('/deployments')
@require_role_any(*_ORG_TIERS)
def deployments_list():
    return render_template(
        'org/placeholder.html',
        heading='Deployments',
        phase='Phase 5',
        detail='One wave, one anonymous link, all assessed domains. Open-link '
               'or ticketed delivery, each with its own honest statement about '
               'what can and cannot be guaranteed.',
        branch_scoped=is_branch_scoped(current_user),
    )


@org_bp.route('/reports')
@require_role_any(*_ORG_TIERS)
def reports():
    """
    Grouped by cycle, with a "needs: ..." line on anything not yet generatable.

    Three states, not two — trainiq's compute_impact_missing pattern with one
    addition:

        not deployed          -> deploy the wave
        awaiting responses    -> wait, or chase participation
        suppressed            -> n is below the reporting threshold, and no
                                 amount of waiting fixes it

    They need different actions, which is why they are not collapsed into
    "unavailable". The third is specific to this product and is not a failure:
    it is the system working.
    """
    return render_template(
        'org/placeholder.html',
        heading='Reports',
        phase='Phases 8-9',
        detail='Executive, HR & OD, and Management reports, grouped by cycle. '
               'A report whose group falls below the reporting threshold is '
               'not generated, and says why.',
        branch_scoped=is_branch_scoped(current_user),
    )
