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
import hashlib
import re
import secrets
from datetime import datetime, timezone

import io

from flask import (
    abort, flash, redirect, render_template, request, send_file, url_for,
)
from flask_login import current_user
from sqlalchemy import func

from app import db, domains
from app import external_url
from app.audit import (
    ORG_SETTINGS_EDIT,
    BRANCH_CREATE, BRANCH_DEACTIVATE, BRANCH_REACTIVATE, CYCLE_ARCHIVE,
    CYCLE_CLOSE, CYCLE_CREATE, CYCLE_EDIT, CYCLE_OPEN, DEPLOYMENT_CLOSE,
    DEPLOYMENT_CREATE, SEGMENT_CREATE, SEGMENT_EDIT, TICKETS_ISSUE,
    USER_CREATE, ACTION_COMPLETE, ACTION_CREATE, ACTION_EDIT, ANALYSIS_RUN,
    REPORT_DOWNLOAD, action_payload, branch_payload, cycle_payload,
    deployment_payload, record_audit, segment_payload, user_payload,
)
from app import ai_service
# IMPORTED AS report_builder, NOT reports.
#
# This module defines a view called reports(), which shadows the module at
# import time — `report_builder.REPORT_TYPES` then resolves to an attribute of the
# view function and raises. The sibling product hit this exact shadowing and
# it is written down in CLAUDE.md; it was still walked into here, which is a
# fair measure of how invisible it is at the call site.
from app.analytics import association, scoring, trend
from app.analytics import reports as report_builder
from app.auth.routes import require_capability, require_role_any
from app.models import (
    AccessTicket, AiCostEvent, Analysis, Branch, ImprovementActions,
    MeasurementCycle, Org, ReportEvent, Segment, SegmentOption,
    SurveyDeployment, SurveyItem, User,
)
from app.org import org_bp
from app.provisioning import active_segments, latest_version
from app.permissions import (
    BRANCH_ADMIN, DEPLOY_ORG_WIDE, DEPLOY_SURVEYS, DEPLOY_TO_BRANCH,
    EDIT_DEPLOYMENT, ISSUE_TICKETS, MANAGE_BRANCH_ADMINS, MANAGE_BRANCHES,
    MANAGE_ACTIONS, MANAGE_CYCLES, MANAGE_ORG_SETTINGS, MANAGE_SEGMENTS,
    ORG_ADMIN, RUN_ANALYSIS,
    VIEW_REPORTS,
    VIEW_TEMPLATES, can, is_branch_scoped, visible_branch_ids,
)

_ORG_TIERS = (ORG_ADMIN, BRANCH_ADMIN)

# Cycle ids appear in report filenames and in conversation, so they are
# constrained to something safe to put in a path and readable in an email.
_CYCLE_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{2,49}$')

# One wording for the §9.2 minimum, used by the org settings screen and by
# cycle validation. Two screens enforcing the same rule in two different
# sentences is how a rule starts to look like a suggestion.
_PURPOSE_TOO_SHORT = (
    'Write a statement of at least 40 characters. Respondents read it before '
    'the first question: what this is for, how the results will be used, who '
    'sees what, and that answering honestly carries no consequence.'
)

# Shown when an L3 admin reaches cycle creation before L2 has written the
# statement. It names who can fix it, because the L3 admin cannot.
_PURPOSE_NOT_SET = (
    'Your organisation administrator has not yet written the statement '
    'respondents are shown before the first question. A cycle cannot be '
    'created without it (specification §9.2). Ask them to set it under '
    'Settings.'
)


def _scoped(query, model):
    """
    Restrict a query to what this user may see. USE THIS, ALWAYS.

    `visible_branch_ids` returns None for L1/L2 meaning "apply no branch
    filter" — callers must not read that as "no branches", which would show an
    org admin nothing. An L3 with no branch assigned gets an empty list and
    therefore sees nothing, which is the safe default.

    L3 also never sees org-wide (branch_id IS NULL) rows: those were created
    deliberately above the branch layer, and a branch admin seeing the whole
    organisation's deployment would defeat the point of the tier.
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
    open_deployments = [d for d in deployments if not d.closed and d.deployed_at]
    responses = sum(d.responses_received for d in deployments)
    expected = sum(d.expected_respondents for d in deployments)

    cycles = _scoped(db.session.query(MeasurementCycle), MeasurementCycle) \
        .filter(MeasurementCycle.archived_at.is_(None)) \
        .order_by(MeasurementCycle.created_at.desc()).all()

    # ONE ROW PER CYCLE, carrying what an admin can act on. The same shape the
    # sibling product's board uses, and the same state function the Reports
    # screen uses — two screens disagreeing about whether a cycle is ready is
    # a support call, and this product has already had one.
    by_cycle = {}
    for deployment in deployments:
        by_cycle.setdefault(deployment.cycle_id, []).append(deployment)

    rows = []
    for cycle in cycles:
        cycle_deployments = by_cycle.get(cycle.id, [])
        cycle_responses = sum(d.responses_received for d in cycle_deployments)
        cycle_expected = sum(d.expected_respondents for d in cycle_deployments)
        rows.append({
            'cycle': cycle,
            'deployments': cycle_deployments,
            'open': sum(1 for d in cycle_deployments
                        if not d.closed and d.deployed_at),
            'responses': cycle_responses,
            'expected': cycle_expected,
            'rate': (round(cycle_responses / cycle_expected * 100)
                     if cycle_expected else None),
            'state': _report_state(cycle, cycle_deployments, cycle_responses),
        })

    return render_template(
        'org/dashboard.html',
        branch_scoped=is_branch_scoped(current_user),
        purpose_reminder=_purpose_reminder(),
        rows=rows,
        stats={
            'cycles': len(cycles),
            'responses': responses,
            'expected': expected,
            'rate': round(responses / expected * 100) if expected else None,
            'open_deployments': len(open_deployments),
            'ready': sum(1 for r in rows if r['state']['key'] == 'ready'),
        },
    )


# ── Org settings ──────────────────────────────────────────


def _purpose_reminder():
    """
    True when this org still owes §9.2 a statement AND this user can write it.

    Rendered as a banner rather than stored as a 'seen it' flag: a flag is
    dismissed once and the statement is still missing, whereas a condition that
    clears itself when the work is done cannot be dismissed into silence. It
    therefore also covers the L2 admin's FIRST login without a first-login
    column, because on a new org the condition is true by construction.
    """
    if not can(current_user, MANAGE_ORG_SETTINGS) or current_user.org_id is None:
        return False
    org = db.session.get(Org, current_user.org_id)
    return org is not None and not (org.default_purpose_statement or '').strip()


@org_bp.route('/settings', methods=['GET', 'POST'])
@require_capability(MANAGE_ORG_SETTINGS)
def settings():
    """
    The organisation's standing answer to §9.2.

    Written once by L2 and inherited by every new cycle, including the branch
    cycles an L3 admin creates. L3 does not reach this screen — the statement
    is the ORGANISATION speaking to its staff, and one branch should not write
    it for the others — but L3 may edit its own cycle's copy afterwards, which
    changes nothing for anyone else.
    """
    org = db.session.get(Org, current_user.org_id) or abort(404)

    if request.method == 'POST':
        text_value = (request.form.get('default_purpose_statement') or '').strip()
        if len(text_value) < 40:
            flash(_PURPOSE_TOO_SHORT, 'error')
            return render_template('org/settings.html', org=org,
                                   value=text_value)

        was_set = bool((org.default_purpose_statement or '').strip())
        org.default_purpose_statement = text_value
        # PAYLOAD CARRIES THE FACT, NOT THE TEXT. audit_events outlives the
        # retention purge and holds administrative identifiers only.
        record_audit(ORG_SETTINGS_EDIT, 'org', org.id,
                     ('Replaced' if was_set else 'Set')
                     + ' the default purpose statement',
                     org_id=org.id,
                     payload={'field': 'default_purpose_statement',
                              'replaced_existing': was_set,
                              'length': len(text_value)})
        db.session.commit()
        flash('Saved. New cycles will start with this statement; existing '
              'cycles are unchanged.', 'success')
        return redirect(url_for('org.settings'))

    return render_template('org/settings.html', org=org,
                           value=org.default_purpose_statement or '')


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
    deployments = dict(
        db.session.query(SurveyDeployment.cycle_id, func.count(SurveyDeployment.id))
        .filter(SurveyDeployment.org_id == current_user.org_id)
        .group_by(SurveyDeployment.cycle_id).all()
    )
    return render_template('org/cycles.html', cycles=cycles, deployments=deployments)


@org_bp.route('/cycles/new', methods=['GET', 'POST'])
@require_capability(MANAGE_CYCLES)
def cycle_new():
    """
    Create a measurement cycle.

    The cycle is a MEASUREMENT ROUND, not a person and not a position — the sibling
    products' cycles are both. "Employee Satisfaction Survey 2026 H1".
    """
    previous = _scoped(db.session.query(MeasurementCycle), MeasurementCycle) \
        .filter(MeasurementCycle.archived_at.is_(None)) \
        .order_by(MeasurementCycle.created_at.desc()).all()

    if request.method == 'POST':
        form = _cycle_form_values(request.form)
        # Generated, not typed. See _next_cycle_id().
        form['id'] = _next_cycle_id()
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
        flash(f'Created {cycle.cycle_name} ({cycle.id}). It is a draft until '
              'you open it — open it to review what respondents will be told '
              'and start collecting.', 'success')
        return redirect(url_for('org.cycle_detail', cycle_id=cycle.id))

    return render_template('org/cycle_form.html', cycle=None,
                           form=_cycle_defaults(), previous=previous,
                           all_domains=domains.DOMAINS)


@org_bp.route('/cycles/<cycle_id>')
@require_role_any(*_ORG_TIERS)
def cycle_detail(cycle_id):
    cycle = _cycle_or_404(cycle_id)
    deployments = (db.session.query(SurveyDeployment)
             .filter_by(cycle_id=cycle.id, org_id=current_user.org_id)
             .order_by(SurveyDeployment.deployed_at.desc().nullslast()).all())
    return render_template('org/cycle_detail.html', cycle=cycle, deployments=deployments,
                           responses=sum(w.responses_received for w in deployments))


@org_bp.route('/cycles/<cycle_id>/edit', methods=['GET', 'POST'])
@require_capability(MANAGE_CYCLES)
def cycle_edit(cycle_id):
    """
    Edit a cycle.

    TWO FIELDS FREEZE ONCE A RESPONSE EXISTS: `assessed_domains` and
    `scoring_method`.

    Narrowing the domains mid-cycle makes the two halves of one cycle
    incomparable and breaks the trend section, which would rather refuse than
    plot a missing domain as a drop. Changing the scoring method silently
    changes every figure already reported from this cycle. Neither failure is
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

    Reopening a closed cycle is deliberately absent. The retention clock starts
    at close (§13.8), a closed cycle's figures have been reported, and letting
    new responses arrive afterwards changes a number somebody has already acted
    on. Run a new cycle and link it with `previous_cycle_id` instead — which is
    also what makes it a trend rather than a revision.
    """
    cycle = _cycle_or_404(cycle_id)
    target = (request.form.get('status') or '').strip()
    now = datetime.now(timezone.utc)

    if target == 'open' and cycle.status == 'draft':
        if not _org_segments_ready():
            flash('Define at least one reporting segment with options, or '
                  'deactivate them all, before opening a cycle. Segments freeze '
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
    deployments = (db.session.query(SurveyDeployment)
             .filter_by(cycle_id=cycle.id).count())
    cycle.archived_at = datetime.now(timezone.utc)
    cycle.archived_by = current_user.id
    record_audit(CYCLE_ARCHIVE, 'cycle', cycle.id,
                 f'Archived cycle {cycle.cycle_name}',
                 org_id=current_user.org_id, branch_id=cycle.branch_id,
                 payload=cycle_payload(cycle, deployment_count=deployments))
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
    be fixed once the cycle is running.
    """
    segments = (db.session.query(Segment)
                .filter_by(org_id=current_user.org_id, is_active=True).all())
    return all(any(o.is_active for o in s.options) for s in segments)


def _cycle_defaults():
    """
    WHAT A NEW CYCLE IS, BEFORE ANYONE TYPES ANYTHING.

    The creation form asks for three things — name, domains, previous cycle —
    and everything else comes from here. That is not a shortcut around the
    specification; it is where the specification's requirements are met.

      - `purpose_statement` is the ORG's standing §9.2 statement, written once
        under Settings. Still copied onto the cycle, so the landing page keeps
        rendering the cycle's own text and the column stays NOT NULL.
      - `min_reporting_n` is the platform floor. It can only ever be RAISED
        (the floor check in _validate_cycle, and LOWER_MIN_N is held by no
        role), so defaulting it cannot weaken suppression.
      - `scoring_method`: §10.2 requires DOCUMENTING which method is used, not
        choosing it per cycle. Every report prints it. Nobody filling a
        creation form has an opinion about mean-of-items.
      - `retention_months`: §13.8 requires a bounded, defined period, not a
        decision re-made every cycle.

    All four remain editable on the EDIT screen, which is where an admin who
    does have an opinion goes.
    """
    from flask import current_app
    org = (db.session.get(Org, current_user.org_id)
           if current_user.org_id else None)
    return {
        'id': '',
        'cycle_name': '',
        'purpose_statement': (org.default_purpose_statement or '') if org else '',
        'assessed_domains': list(domains.DOMAINS),
        'previous_cycle_id': None,
        'min_reporting_n': current_app.config['MIN_REPORTING_N'],
        'scoring_method': 'mean_of_subdomains',
        'retention_months': 24,
        'is_pilot': False,
    }


def _next_cycle_id():
    """
    Generate the cycle reference instead of asking for it.

    It is a human-readable PK that lands in report filenames, so it stays
    readable — JS-2026-1, JS-2026-2 — but there is nothing an admin knows that
    this does not. The probe loops rather than counting rows because the PK is
    GLOBAL across orgs while a count would be per-org, and because an archived
    cycle still holds its id. It is also belt-and-braces only: a collision
    still raises on the unique PK rather than overwriting anything, and
    _validate_cycle re-checks the generated value against the same regex an
    admin's input used to face.
    """
    year = datetime.now(timezone.utc).year
    n = 1
    while db.session.get(MeasurementCycle, f'JS-{year}-{n}') is not None:
        n += 1
    return f'JS-{year}-{n}'


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

    common = {
        'id': existing.id if existing else (form.get('id') or '').strip(),
        'cycle_name': (form.get('cycle_name') or '').strip(),
        'assessed_domains': form.getlist('assessed_domains') or [],
        'previous_cycle_id': (form.get('previous_cycle_id') or '').strip() or None,
    }

    if existing is None:
        # CREATING. The form posts three fields and these four are not among
        # them, so they come from _cycle_defaults() and the posted body is
        # ignored for them entirely — a hand-crafted POST cannot set a
        # retention of 120 or a scoring method through a screen that does not
        # offer either. (min_reporting_n is still floor-checked in
        # _validate_cycle regardless, since that one must never be weakened.)
        defaults = _cycle_defaults()
        return common | {
            'purpose_statement': defaults['purpose_statement'],
            'min_reporting_n': defaults['min_reporting_n'],
            'scoring_method': defaults['scoring_method'],
            'retention_months': defaults['retention_months'],
            'is_pilot': defaults['is_pilot'],
        }

    # EDITING. Every field is on the screen, so every field is read from the
    # post — including `is_pilot`, whose ABSENCE means unchecked and must not
    # fall back to the stored value or the box could never be cleared.
    return common | {
        'purpose_statement': (form.get('purpose_statement') or '').strip(),
        'min_reporting_n': _int('min_reporting_n', existing.min_reporting_n),
        'scoring_method': (form.get('scoring_method')
                           or existing.scoring_method).strip(),
        'retention_months': _int('retention_months', existing.retention_months),
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
    # first question. This text is what the survey landing page renders. A cycle
    # that opens with nothing to say is the cycle whose answers are not worth
    # having.
    if len(form['purpose_statement']) < 40:
        # ON CREATE this text came from the ORG DEFAULT, not from a box on this
        # screen, so the error has to point at the screen that can fix it —
        # and for an L3 admin, at the person who can. §9.2 is enforced in the
        # same place it always was; only the place it is WRITTEN moved.
        return _PURPOSE_NOT_SET if creating else _PURPOSE_TOO_SHORT

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
                'relation to. A cycle without it cannot produce a diagnostic.')

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
            flash('This segment is frozen: a cycle has already collected '
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
    version = latest_version(current_user.org_id)
    items = (db.session.query(SurveyItem)
             .filter_by(org_id=current_user.org_id, version=version)
             .order_by(SurveyItem.sort_order).all())
    grouped = []
    for domain_code in domains.DOMAINS:
        in_domain = [i for i in items if i.domain_code == domain_code]
        if in_domain:
            grouped.append((domain_code, in_domain))
    return render_template('org/instrument.html', grouped=grouped,
                           version=version, total=len(items))


# ── Placeholders ────────────────────────────────────────────────────────────

@org_bp.route('/deployments')
@require_role_any(*_ORG_TIERS)
def deployments_list():
    """Every deployment this user may see, newest first."""
    deployments = _scoped(db.session.query(SurveyDeployment), SurveyDeployment) \
        .filter(SurveyDeployment.archived_at.is_(None)) \
        .order_by(SurveyDeployment.deployed_at.desc().nullslast()).all()
    cycles = {c.id: c for c in
              db.session.query(MeasurementCycle)
              .filter_by(org_id=current_user.org_id).all()}
    return render_template('org/deployments.html', deployments=deployments,
                           cycles=cycles,
                           branch_scoped=is_branch_scoped(current_user))


@org_bp.route('/cycles/<cycle_id>/deploy', methods=['GET', 'POST'])
@require_capability(DEPLOY_SURVEYS)
def deploy(cycle_id):
    """
    Deploy: ONE cycle, ONE audience, ONE anonymous link.

    The link is minted here and is the only way a respondent ever reaches the
    survey. `secrets.token_urlsafe(32)` gives ~256 bits — this is a bearer
    credential for a whole workforce's feedback, and an enumerable slug would
    let anyone walk the space and read (or stuff) any client's open deployment.

    WHO MAY TARGET WHAT is the one deliberately non-cumulative rule in
    permissions.py: L2 deploys org-wide, L3 deploys to its own branch, and L2
    does not originate a branch-targeted deployment. Two tiers targeting one branch
    produce two concurrent deployments over one population, with no way to tell which
    is authoritative and a response-rate denominator that means nothing.
    """
    cycle = _cycle_or_404(cycle_id)

    if cycle.status != 'open':
        flash(f'{cycle.cycle_name} is {cycle.status}. Open the cycle before '
              'deploying it — the link would otherwise be live before '
              'respondents have been told anything.', 'error')
        return redirect(url_for('org.cycle_detail', cycle_id=cycle.id))

    branch_id = _my_branch_id()
    if branch_id is None and not can(current_user, DEPLOY_ORG_WIDE):
        abort(403)
    if branch_id is not None and not can(current_user, DEPLOY_TO_BRANCH):
        abort(403)

    existing = (db.session.query(SurveyDeployment)
                .filter_by(cycle_id=cycle.id, org_id=current_user.org_id,
                           branch_id=branch_id, closed=False)
                .filter(SurveyDeployment.archived_at.is_(None)).first())
    if existing is not None:
        # A nicer error than the database's. uq_open_deployment is the real
        # guard — two admins pressing Deploy at once is a race this check
        # cannot win.
        flash('This cycle is already deployed for that audience.', 'error')
        return redirect(url_for('org.deployment_detail',
                                deployment_id=existing.id))

    if request.method == 'POST':
        mode = (request.form.get('delivery_mode') or 'open_link').strip()
        if mode not in ('open_link', 'ticketed'):
            abort(400)
        try:
            expected = int((request.form.get('expected_respondents') or '').strip())
        except ValueError:
            expected = 0
        if expected < 1:
            flash('Enter the number of people invited. It is the denominator '
                  'of the response rate, and a rate without one is a '
                  'percentage of nothing.', 'error')
            return render_template('org/deploy_form.html', cycle=cycle,
                                   branch_id=branch_id)

        deployment = SurveyDeployment(
            org_id=current_user.org_id,
            branch_id=branch_id,
            cycle_id=cycle.id,
            assessed_domains=list(cycle.assessed_domains or []),
            public_slug=secrets.token_urlsafe(32),
            delivery_mode=mode,
            expected_respondents=expected,
            deployed_at=datetime.now(timezone.utc),
            closes_at=cycle.closes_at,
            created_by=current_user.id,
        )
        db.session.add(deployment)
        db.session.flush()
        record_audit(DEPLOYMENT_CREATE, 'deployment', deployment.id,
                     f'Deployed {cycle.cycle_name} '
                     f'({mode}, {expected} invited)',
                     org_id=current_user.org_id, branch_id=branch_id,
                     payload=deployment_payload(deployment))
        db.session.commit()
        flash('The deployment is live. Copy the link below — it is the only way in, '
              'and it is not recoverable from anywhere else.', 'success')
        return redirect(url_for('org.deployment_detail',
                                deployment_id=deployment.id))

    return render_template('org/deploy_form.html', cycle=cycle,
                           branch_id=branch_id)


@org_bp.route('/deployments/<uuid:deployment_id>')
@require_role_any(*_ORG_TIERS)
def deployment_detail(deployment_id):
    deployment = _deployment_or_404(deployment_id)
    cycle = db.session.get(MeasurementCycle, deployment.cycle_id)
    tickets = unused = None
    if deployment.delivery_mode == 'ticketed':
        tickets = db.session.query(AccessTicket).filter_by(
            deployment_id=deployment.id).count()
        unused = db.session.query(AccessTicket).filter_by(
            deployment_id=deployment.id, used_at=None).count()

    # Built from SURVEY_BASE_URL, never from the request Host header. An admin
    # on *.onrender.com must still copy a link that says jobs.leadto.co.ke —
    # see external_url() for why that is more than cosmetic here.
    link = external_url('survey.landing', slug=deployment.public_slug)

    return render_template('org/deployment_detail.html', deployment=deployment,
                           cycle=cycle, link=link, tickets=tickets,
                           unused=unused)


@org_bp.route('/deployments/<uuid:deployment_id>/tickets', methods=['POST'])
@require_capability(ISSUE_TICKETS)
def issue_tickets(deployment_id):
    """
    Mint N opaque tickets and show them ONCE.

    The app stores only sha256(ticket) and never who received which — see
    models.AccessTicket. The plaintext exists on this page and on the slips
    handed out, and nowhere else: this screen cannot be reloaded to recover
    them, by design.

    Worth saying out loud to whoever distributes them: the mapping of ticket to
    person can still be created OUTSIDE this system, in a spreadsheet. Nothing
    in the code can prevent that, and employees know it. Hand them out shuffled,
    in bulk, and do not write down who got which — otherwise the integrity
    guarantee is bought with the candour the instrument depends on.
    """
    deployment = _deployment_or_404(deployment_id)
    if deployment.delivery_mode != 'ticketed':
        flash('This cycle uses an open link; it has no tickets.', 'error')
        return redirect(url_for('org.deployment_detail',
                                deployment_id=deployment.id))
    if deployment.closed:
        flash('This cycle is closed.', 'error')
        return redirect(url_for('org.deployment_detail',
                                deployment_id=deployment.id))

    try:
        count = int((request.form.get('count') or '').strip())
    except ValueError:
        count = 0
    if not 1 <= count <= 2000:
        flash('Issue between 1 and 2000 tickets at a time.', 'error')
        return redirect(url_for('org.deployment_detail',
                                deployment_id=deployment.id))

    plaintext = []
    for _ in range(count):
        ticket = secrets.token_urlsafe(9)
        plaintext.append(ticket)
        db.session.add(AccessTicket(
            deployment_id=deployment.id, org_id=deployment.org_id,
            ticket_hash=hashlib.sha256(ticket.encode()).hexdigest(),
        ))
    record_audit(TICKETS_ISSUE, 'deployment', deployment.id,
                 f'Issued {count} access tickets',
                 org_id=deployment.org_id, branch_id=deployment.branch_id,
                 payload={'count': count})
    db.session.commit()

    return render_template('org/tickets.html', deployment=deployment,
                           tickets=plaintext)


@org_bp.route('/deployments/<uuid:deployment_id>/close', methods=['POST'])
@require_capability(EDIT_DEPLOYMENT)
def deployment_close(deployment_id):
    """
    Close a cycle. The link dies immediately.

    Deliberately one-way, like the cycle status. A reopened cycle accepts
    responses after its figures have been reported, and the whole point of the
    retention clock is that it starts somewhere definite.
    """
    deployment = _deployment_or_404(deployment_id)
    if deployment.closed:
        flash('Already closed.', 'error')
        return redirect(url_for('org.deployment_detail',
                                deployment_id=deployment.id))

    deployment.closed = True
    record_audit(DEPLOYMENT_CLOSE, 'deployment', deployment.id,
                 f'Closed the cycle ({deployment.responses_received} responses)',
                 org_id=deployment.org_id, branch_id=deployment.branch_id,
                 payload=deployment_payload(deployment))
    db.session.commit()
    flash('Closed. The survey link no longer works.', 'success')
    return redirect(url_for('org.deployment_detail',
                            deployment_id=deployment.id))


def _deployment_or_404(deployment_id):
    deployment = db.session.get(SurveyDeployment, deployment_id)
    if deployment is None or deployment.org_id != current_user.org_id:
        abort(404)
    branch_ids = visible_branch_ids(current_user)
    if branch_ids is not None and deployment.branch_id not in branch_ids:
        abort(404)
    return deployment


@org_bp.route('/reports')
@require_role_any(*_ORG_TIERS)
def reports():
    """
    Grouped by cycle, with a "needs: ..." line on anything not yet generatable.

    THREE STATES, NOT TWO — trainiq's compute_impact_missing pattern with one
    addition:

        not deployed          -> deploy the cycle
        awaiting responses    -> wait, or chase participation
        below the threshold   -> n is under the reporting threshold, and no
                                 amount of waiting fixes it

    A cycle that is still collecting is READY, and its reports are marked
    provisional. Waiting for a close was this product's own invention and
    neither sibling does it.

    They need different actions, which is why they are not collapsed into
    "unavailable". The third is specific to this product and is NOT A FAILURE:
    it is the system working as designed, and the wording says so rather than
    reading as an error the admin should try to clear.
    """
    cycles = _scoped(db.session.query(MeasurementCycle), MeasurementCycle) \
        .filter(MeasurementCycle.archived_at.is_(None)) \
        .order_by(MeasurementCycle.created_at.desc()).all()

    rows = []
    for cycle in cycles:
        deployments = _scoped(db.session.query(SurveyDeployment), SurveyDeployment) \
            .filter(SurveyDeployment.cycle_id == cycle.id) \
            .filter(SurveyDeployment.archived_at.is_(None)).all()
        responses = sum(w.responses_received for w in deployments)
        rows.append({
            'cycle': cycle,
            'deployments': deployments,
            'responses': responses,
            'expected': sum(w.expected_respondents for w in deployments),
            'state': _report_state(cycle, deployments, responses),
        })

    return render_template('org/reports.html', rows=rows,
                           branch_scoped=is_branch_scoped(current_user))


def _report_state(cycle, deployments, responses):
    """
    What this cycle is waiting for, as a state and a sentence.

    The sentence is the point. "Unavailable" tells an admin nothing; "needs 3
    more responses" and "deploy the cycle" are different jobs, and the third
    state is not a job at all.

    `provisional` is set on a ready cycle that is still collecting. It is a
    qualification on the report, not a state of its own: the reports are
    generatable either way.
    """
    if not deployments:
        return {'key': 'not_deployed', 'provisional': False,
                'needs': 'not deployed yet — deploy the cycle to collect responses'}

    if responses < cycle.min_reporting_n:
        shortfall = cycle.min_reporting_n - responses
        return {'key': 'below_threshold', 'provisional': False,
                'needs': (f'{responses} of the {cycle.min_reporting_n} responses '
                          f'needed before any result can be shown — '
                          f'{shortfall} more')}

    # STILL COLLECTING IS NOT A BLOCK. It was, and it made this product
    # disagree with its two siblings, which gate on responses and never on
    # deployment state. An open cycle is reportable and the report is stamped
    # PROVISIONAL on its cover — the honest treatment of a figure that will
    # move, rather than withholding numbers the client can already see on the
    # analysis screen.
    return {'key': 'ready', 'needs': None,
            'provisional': any(not w.closed for w in deployments)}


@org_bp.route('/cycles/<cycle_id>/analysis')
@require_capability(VIEW_REPORTS)
def analysis(cycle_id):
    """
    The on-screen diagnostic view: §10.4, before any PDF exists.

    Everything here is computed deterministically in app/analytics/. Nothing is
    narrated, nothing is banded, and every association output carries §10.4's
    caveat from the result object itself rather than from this template.
    """
    cycle = _cycle_or_404(cycle_id)
    deployments = _scoped(db.session.query(SurveyDeployment), SurveyDeployment) \
        .filter(SurveyDeployment.cycle_id == cycle.id) \
        .filter(SurveyDeployment.archived_at.is_(None)).all()

    profiles = scoring.load_profiles(
        db.session, [w.id for w in deployments], method=cycle.scoring_method)

    min_n = cycle.min_reporting_n
    outcome_results = [
        ((domains.GEM, sub), scoring.group_result(profiles, (domains.GEM, sub), min_n))
        for sub in domains.SUBDOMAINS[domains.GEM]
    ]
    diagnostic_results = [
        (code, scoring.group_result(profiles, code, min_n))
        for code in domains.DIAGNOSTIC_DOMAINS
    ]

    contrasts = association.domain_contrasts(
        profiles, outcome=association.DEFAULT_OUTCOME, min_reporting_n=min_n)
    priority = association.priority_view(
        profiles, outcome=association.DEFAULT_OUTCOME, min_reporting_n=min_n)

    comparisons = []
    for segment in active_segments(current_user.org_id):
        comparisons.append(association.segment_comparison(
            profiles, (domains.GEM, 'G'), segment, min_reporting_n=min_n))

    return render_template(
        'org/analysis.html', cycle=cycle, deployments=deployments,
        respondents=len(profiles),
        outcome_results=outcome_results,
        diagnostic_results=diagnostic_results,
        contrasts=contrasts, priority=priority, comparisons=comparisons,
        contrast_floor=association.contrast_floor(min_n),
        Suppressed=scoring.Suppressed,
        GroupResult=scoring.GroupResult,
        Contrast=association.Contrast,
    )

# ── Report generation (phases 8-9) ─────────────────────────────────────────

def _report_inputs(cycle, scope_branch=None):
    """
    Everything a report needs, assembled once.

    `scope_branch` narrows to one branch for a Management Report. The profiles
    are filtered on the SESSION's branch_id — the branch recorded when the
    response was given — not on anything declared later.
    """
    deployments = _scoped(db.session.query(SurveyDeployment), SurveyDeployment) \
        .filter(SurveyDeployment.cycle_id == cycle.id) \
        .filter(SurveyDeployment.archived_at.is_(None)).all()
    if scope_branch is not None:
        deployments = [w for w in deployments if w.branch_id == scope_branch.id]

    profiles = scoring.load_profiles(
        db.session, [w.id for w in deployments], method=cycle.scoring_method)

    previous = None
    if cycle.previous_cycle_id:
        earlier = db.session.get(MeasurementCycle, cycle.previous_cycle_id)
        if earlier is not None and earlier.org_id == cycle.org_id:
            earlier_deployments = (db.session.query(SurveyDeployment)
                             .filter_by(cycle_id=earlier.id, org_id=cycle.org_id)
                             .filter(SurveyDeployment.archived_at.is_(None)).all())
            if scope_branch is not None:
                earlier_deployments = [w for w in earlier_deployments
                                 if w.branch_id == scope_branch.id]
            earlier_profiles = scoring.load_profiles(
                db.session, [w.id for w in earlier_deployments],
                method=earlier.scoring_method)
            if earlier_profiles:
                previous = (earlier_profiles,
                            earlier.assessed_domains or domains.DOMAINS,
                            earlier.id)

    actions = (db.session.query(ImprovementActions)
               .filter_by(org_id=cycle.org_id, cycle_id=cycle.id).all())

    return deployments, profiles, previous, actions


def _build_report(cycle, report_type, scope_branch=None, with_charts=True):
    """Gate, then build the shared context. Raises ReportNotAvailable."""
    deployments, profiles, previous, actions = _report_inputs(cycle, scope_branch)
    scope_label = scope_branch.name if scope_branch else 'Whole organisation'

    report_builder.require_reportable(report_type, cycle, deployments, profiles, scope_label)

    analysis_row = (db.session.query(Analysis)
                    .filter_by(org_id=cycle.org_id, cycle_id=cycle.id,
                               report_type=report_type,
                               scope_key=(str(scope_branch.id)
                                          if scope_branch else None))
                    .order_by(Analysis.generated_at.desc()).first())

    context = report_builder.build_context(
        report_type=report_type,
        org=db.session.get(Org, cycle.org_id),
        cycle=cycle, deployments=deployments, profiles=profiles,
        branch=scope_branch, scope_label=scope_label,
        segments=active_segments(cycle.org_id),
        previous=previous, improvement_actions=actions,
        narrative=(analysis_row.payload if analysis_row else None),
    )
    # The courses. Attached through the SHARED helper rather than inline, so
    # this builder and the L1 board's assemble() cannot disagree about what a
    # report recommends - they did, and the client-facing one was the one
    # missing them.
    report_builder.attach_catalogue(db.session, context)

    if with_charts:
        report_builder.add_charts(context)
    return context


def _scope_branch_or_none(cycle):
    """
    The branch a Management Report covers.

    A branch admin always gets their own branch — they have no other. An org
    admin picks one by query parameter.
    """
    if is_branch_scoped(current_user):
        return current_user.branch
    branch_id = (request.args.get('branch') or '').strip()
    if not branch_id:
        return None
    branch = db.session.get(Branch, branch_id)
    if branch is None or branch.org_id != cycle.org_id:
        abort(404)
    return branch


@org_bp.route('/cycles/<cycle_id>/report/<report_type>')
@require_capability(VIEW_REPORTS)
def report_preview(cycle_id, report_type):
    """On-screen preview, and the page the download buttons live on."""
    if report_type not in report_builder.REPORT_TYPES:
        abort(404)
    cycle = _cycle_or_404(cycle_id)
    scope_branch = (_scope_branch_or_none(cycle)
                    if report_type == report_builder.MANAGEMENT else None)

    try:
        context = _build_report(cycle, report_type, scope_branch,
                                with_charts=False)
    except report_builder.ReportNotAvailable as exc:
        return render_template('org/report_unavailable.html', cycle=cycle,
                               report_type=report_type,
                               title=report_builder.report_title(report_type),
                               reason=str(exc)), 200

    return render_template('org/report_preview.html', cycle=cycle,
                           context=context, report_type=report_type,
                           branch=scope_branch,
                           can_interpret=ai_service.api_key_present())


@org_bp.route('/cycles/<cycle_id>/report/<report_type>/<fmt>')
@require_capability(VIEW_REPORTS)
def report_download(cycle_id, report_type, fmt):
    """
    PDF or XLSX. Both built from the same context, so they cannot disagree.

    Every download writes a ReportEvent: §13.4 requires role-based access to
    results, and this is the evidence that it held.
    """
    if report_type not in report_builder.REPORT_TYPES or fmt not in ('pdf', 'xlsx'):
        abort(404)
    cycle = _cycle_or_404(cycle_id)
    scope_branch = (_scope_branch_or_none(cycle)
                    if report_type == report_builder.MANAGEMENT else None)

    try:
        context = _build_report(cycle, report_type, scope_branch,
                                with_charts=(fmt == 'pdf'))
    except report_builder.ReportNotAvailable as exc:
        flash(str(exc), 'error')
        return redirect(url_for('org.report_preview', cycle_id=cycle.id,
                                report_type=report_type))

    org = context['org']
    if fmt == 'pdf':
        payload = report_builder.build_pdf(context)
        mimetype = 'application/pdf'
    else:
        payload = report_builder.build_xlsx(context)
        mimetype = ('application/vnd.openxmlformats-officedocument'
                    '.spreadsheetml.sheet')

    db.session.add(ReportEvent(
        org_id=cycle.org_id, cycle_id=cycle.id,
        report_type=report_type, export_format=fmt,
        scope_key=(str(scope_branch.id) if scope_branch else None),
        generated_by=current_user.id,
    ))
    record_audit(REPORT_DOWNLOAD, 'cycle', cycle.id,
                 f'Downloaded the {report_builder.report_title(report_type)} '
                 f'({fmt.upper()}) for {context["scope_label"]}, n={context["n"]}',
                 org_id=cycle.org_id,
                 branch_id=(scope_branch.id if scope_branch else None),
                 payload={'report_type': report_type, 'format': fmt,
                          'n': context['n'],
                          'min_reporting_n': context['min_reporting_n']})
    db.session.commit()

    filename = report_builder.report_filename(report_type, org.name, cycle.id, fmt)
    return send_file(io.BytesIO(payload), mimetype=mimetype,
                     as_attachment=True, download_name=filename)


@org_bp.route('/cycles/<cycle_id>/report/<report_type>/interpret',
              methods=['POST'])
@require_capability(RUN_ANALYSIS)
def report_interpret(cycle_id, report_type):
    """
    MANUAL TRIGGER ONLY. One human, one button, one report.

    Never auto-dispatched when a response threshold is crossed: a cycle that
    fills up overnight must not spend money, or write a characterisation of a
    workforce's morale, while nobody is watching.
    """
    if report_type not in report_builder.REPORT_TYPES:
        abort(404)
    cycle = _cycle_or_404(cycle_id)
    scope_branch = (_scope_branch_or_none(cycle)
                    if report_type == report_builder.MANAGEMENT else None)

    try:
        context = _build_report(cycle, report_type, scope_branch,
                                with_charts=False)
    except report_builder.ReportNotAvailable as exc:
        flash(str(exc), 'error')
        return redirect(url_for('org.report_preview', cycle_id=cycle.id,
                                report_type=report_type))

    if context['provisional']:
        # The ONE thing an open cycle does not get. A narrative is a confident
        # sentence about a finding that may reverse next week, and it is the
        # text that gets quoted in a board paper long after the n it rested on
        # has been forgotten. Figures may be read early; prose may not.
        flash('This cycle is still collecting, so its figures will move. An '
              'interpretation is written once, read as settled and quoted '
              'later, so it is generated only after the cycle closes. The '
              'reports themselves are available now, marked provisional.',
              'error')
        return redirect(url_for('org.report_preview', cycle_id=cycle.id,
                                report_type=report_type))

    try:
        payload, usage = ai_service.generate(
            report_builder.narrative_payload(context),
            report_builder.NARRATIVE_SECTIONS[report_type],
            context_note=(
                f'This is the {report_builder.report_title(report_type)}. '
                f'Audience: {report_builder.report_audience(report_type)}.'),
        )
    except ai_service.NarrativeError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('org.report_preview', cycle_id=cycle.id,
                                report_type=report_type))

    scope_key = str(scope_branch.id) if scope_branch else None
    db.session.add(Analysis(
        org_id=cycle.org_id, cycle_id=cycle.id,
        report_type=report_type, scope_key=scope_key,
        payload=payload, model=ai_service.MODEL,
        generated_by=current_user.id,
    ))
    db.session.add(AiCostEvent(
        org_id=cycle.org_id, cycle_id=cycle.id, report_type=report_type,
        model=ai_service.MODEL,
        input_tokens=usage['input_tokens'],
        output_tokens=usage['output_tokens'],
        cost_usd=ai_service.cost_usd(usage['input_tokens'],
                                     usage['output_tokens']),
    ))
    record_audit(ANALYSIS_RUN, 'cycle', cycle.id,
                 f'Generated the interpretation for the '
                 f'{report_builder.report_title(report_type)}',
                 org_id=cycle.org_id,
                 branch_id=(scope_branch.id if scope_branch else None),
                 payload={'report_type': report_type,
                          'sections': sorted(payload.keys())})
    db.session.commit()

    flash('Interpretation generated.', 'success')
    return redirect(url_for('org.report_preview', cycle_id=cycle.id,
                            report_type=report_type))

# ── Improvement actions — §16's follow-up loop ─────────────────────────────
#
# §16 asks the system to measure organisational value, including "completion of
# agreed improvement actions", and §12.3 ends the Management Report with
# "follow-up commitments and progress monitoring". Neither can be rendered from
# survey rows: both need state that persists BETWEEN cycles.
#
# Without this the product is a score generator, which §16's closing sentence
# says it is not: "The system's value lies in enabling a more disciplined
# process of diagnosis, decision-making and follow-up — not merely generating
# scores."

@org_bp.route('/cycles/<cycle_id>/actions')
@require_capability(MANAGE_ACTIONS)
def actions_list(cycle_id):
    cycle = _cycle_or_404(cycle_id)
    actions = (db.session.query(ImprovementActions)
               .filter_by(org_id=current_user.org_id, cycle_id=cycle.id)
               .order_by(ImprovementActions.created_at.desc()).all())
    return render_template(
        'org/actions.html', cycle=cycle, actions=actions,
        subdomain_pairs=domains.subdomain_pairs(),
        intervention_types=domains.INTERVENTION_TYPES,
        type_labels=domains.INTERVENTION_TYPE_LABELS,
        intervention_map=domains.INTERVENTION_MAP,
        counts=trend.actions_alongside(actions))


@org_bp.route('/cycles/<cycle_id>/actions/new', methods=['POST'])
@require_capability(MANAGE_ACTIONS)
def action_create(cycle_id):
    """
    An action is ENTERED BY A HUMAN, never generated.

    §11 and §17.1 are diagnose-before-prescribing: the system proposes RESPONSE
    CATEGORIES and a person commits to an action. There is deliberately no
    button that turns a finding into a commitment automatically.
    """
    cycle = _cycle_or_404(cycle_id)

    title = (request.form.get('title') or '').strip()
    owner = (request.form.get('owner_label') or '').strip()
    # The form posts one field ("GEM|G") rather than two, so a half-filled
    # pair — a domain with no subdomain, or worse a subdomain with no domain —
    # cannot be constructed. Subdomain letters repeat across domains, so a
    # subdomain without its domain names three different constructs.
    pair = (request.form.get('subdomain_pair') or '').strip()
    domain_code = subdomain_code = None
    if pair:
        parts = pair.split('|')
        if len(parts) != 2 or (parts[0], parts[1]) not in domains.INTERVENTION_MAP:
            abort(400)
        domain_code, subdomain_code = parts
    intervention_type = (request.form.get('intervention_type') or '').strip() or None

    if not title:
        flash('An action needs a title.', 'error')
        return redirect(url_for('org.actions_list', cycle_id=cycle.id))

    # AN ACTION IS OWNED BY A ROLE OR A TEAM, NEVER BY A RESPONDENT.
    #
    # owner_label is free text by design — "Head of Finance", "OD team" — and
    # that is exactly why it is checked. "Follow up with whoever scored Trust a
    # 1" is the natural thing for a well-meaning manager to type, and it is the
    # re-identification §13.5-§13.6 exists to prevent. Caught here rather than
    # only by the privacy test, because the person typing it should be told.
    for field_name, value in (('title', title), ('owner', owner)):
        lowered = value.lower()
        if any(phrase in lowered for phrase in (
                'who scored', 'who rated', 'who said', 'who answered',
                'the person who', 'respondent', 'whoever gave')):
            flash(
                f'That {field_name} points at an individual respondent. '
                'Responses are anonymous and cannot be traced to anyone — an '
                'action is owned by a role or a team, such as "Head of '
                'Finance" or "the OD team".', 'error')
            return redirect(url_for('org.actions_list', cycle_id=cycle.id))

    if intervention_type and intervention_type not in domains.INTERVENTION_TYPES:
        abort(400)
    if domain_code and domain_code not in domains.DOMAINS:
        abort(400)

    # §8.5, enforced at the point of entry as well as in the catalogue filter:
    # training is not a response to a compensation finding.
    if (domain_code == domains.PIE and subdomain_code
            and intervention_type == domains.TRAINING):
        flash(
            'Training is not recorded as the response to a Reward '
            'Satisfaction finding. The specification is explicit that it is '
            'not a substitute for addressing a substantiated compensation '
            'problem — a pay-equity review, market benchmarking or a '
            'compensation review is the response category here.', 'error')
        return redirect(url_for('org.actions_list', cycle_id=cycle.id))

    action = ImprovementActions(
        org_id=current_user.org_id,
        branch_id=_my_branch_id(),
        cycle_id=cycle.id,
        domain_code=domain_code,
        subdomain_code=subdomain_code,
        title=title,
        description=(request.form.get('description') or '').strip() or None,
        intervention_type=intervention_type,
        owner_label=owner or None,
        status='proposed',
        created_by=current_user.id,
    )
    db.session.add(action)
    db.session.flush()
    record_audit(ACTION_CREATE, 'action', action.id,
                 f'Recorded improvement action: {title}',
                 org_id=current_user.org_id, branch_id=action.branch_id,
                 payload=action_payload(action))
    db.session.commit()
    flash('Action recorded.', 'success')
    return redirect(url_for('org.actions_list', cycle_id=cycle.id))


@org_bp.route('/actions/<uuid:action_id>/status', methods=['POST'])
@require_capability(MANAGE_ACTIONS)
def action_status(action_id):
    """
    Move an action along. `evidence_note` is what changed, in the owner's own
    words — §16's "employee perceptions of changes implemented" needs something
    to point at besides a status flag.
    """
    action = db.session.get(ImprovementActions, action_id)
    if action is None or action.org_id != current_user.org_id:
        abort(404)
    branch_ids = visible_branch_ids(current_user)
    if branch_ids is not None and action.branch_id not in branch_ids:
        abort(404)

    status = (request.form.get('status') or '').strip()
    if status not in ('proposed', 'agreed', 'in_progress', 'completed', 'dropped'):
        abort(400)

    now = datetime.now(timezone.utc)
    action.status = status
    if status == 'agreed' and action.agreed_at is None:
        action.agreed_at = now
    if status == 'completed':
        action.completed_at = now
        note = (request.form.get('evidence_note') or '').strip()
        if note:
            action.evidence_note = note

    record_audit(ACTION_COMPLETE if status == 'completed' else ACTION_EDIT,
                 'action', action.id,
                 f'Improvement action moved to {status}: {action.title}',
                 org_id=current_user.org_id, branch_id=action.branch_id,
                 payload=action_payload(action))
    db.session.commit()
    flash('Saved.', 'success')
    return redirect(url_for('org.actions_list', cycle_id=action.cycle_id))
