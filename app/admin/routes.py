"""
L1 master admin — cross-org.

Every route here is gated on a CAPABILITY, never on a role comparison. L1 has
no org_id, which has a consequence worth stating once: any capability L1 alone
holds — the catalogue, the validation report, the retention purge, pilot
feedback — must live in THIS blueprint, because every query in app/org/ is
scoped by `current_user.org_id` and L1 can never satisfy one. The sibling
product learned this by putting a candidate purge in the wrong blueprint and
finding it unreachable by the only role allowed to use it.
"""
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from flask import (
    abort, flash, redirect, render_template, request, send_file, url_for,
)
from flask_login import current_user
from sqlalchemy import func

from app import db
from app import ai_service
from app.admin import admin_bp
# Imported as report_builder, not reports: app/org/routes.py defines a view
# called reports() and the two blueprints are read side by side. Keeping one
# name for the module everywhere is cheaper than remembering which file is
# safe.
from app import catalogue as catalogue_module
from app.analytics import reports as report_builder
from app.audit import (
    ORG_CREATE, ORG_EDIT, REPORT_DOWNLOAD, USER_CREATE, USER_DEACTIVATE,
    USER_REACTIVATE, record_audit, user_payload,
)
from app.auth.routes import require_capability
from app.domains import DOMAINS, MASTER_ORG_ID, SUBDOMAINS, subdomain_label
from app.models import (
    AiCostEvent, Analysis, AuditEvent, Branch, InterventionCatalogue,
    MeasurementCycle, Org, ReportEvent, SurveyDeployment, SurveyItem, User,
)
from app.permissions import (
    CREATABLE_ROLES, EDIT_TEMPLATES, MANAGE_CATALOGUE, MANAGE_ORGS, ORG_ADMIN,
    VIEW_AUDIT_LOG, VIEW_COSTS, VIEW_VALIDATION_REPORT,
)
from app.provisioning import (
    ProvisioningError, active_segments, clone_instrument_to_org,
    create_default_segments, default_active_domains,
)

# Days since the last wave was deployed, for the status-board traffic light.
# Same thresholds as the sibling boards so one operator reads all three the
# same way.
_AMBER_AFTER_DAYS = 60
_RED_AFTER_DAYS = 90


def _traffic_light(last_deployed):
    """green < 60 days since the last wave, amber 60-90, red > 90 or never."""
    if last_deployed is None:
        return 'red'
    # Rows written before a timezone-aware default was in place can come back
    # naive; comparing naive to aware raises, and it would raise on a dashboard,
    # which is the worst place for it.
    if last_deployed.tzinfo is None:
        last_deployed = last_deployed.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - last_deployed
    if age < timedelta(days=_AMBER_AFTER_DAYS):
        return 'green'
    if age < timedelta(days=_RED_AFTER_DAYS):
        return 'amber'
    return 'red'


@admin_bp.route('/')
@require_capability(MANAGE_ORGS)
def dashboard():
    """
    Client Organisations — the status board.

    The master template org is EXCLUDED. It is a real row in `orgs` because the
    48 master items need an owner and the cloning path joins on org_id, but it
    is infrastructure, not a client: it has no admin, no cycle and no
    respondent, and it never will. Listing it counts a fixture as a client,
    reports "1 organisation" on a platform with none, and offers a View button
    into a tenant that is not one.
    """
    orgs = (db.session.query(Org)
            .filter(Org.id != MASTER_ORG_ID)
            .order_by(Org.name).all())

    # Per-org aggregates in two grouped queries rather than N+1 per card.
    cycles = dict(
        db.session.query(MeasurementCycle.org_id, func.count(MeasurementCycle.id))
        .filter(MeasurementCycle.archived_at.is_(None))
        .group_by(MeasurementCycle.org_id).all()
    )
    deployment_rows = (
        db.session.query(
            SurveyDeployment.org_id,
            func.count(SurveyDeployment.id),
            func.count(SurveyDeployment.id).filter(
                SurveyDeployment.closed.is_(True)
            ),
            func.sum(SurveyDeployment.responses_received),
            func.max(SurveyDeployment.deployed_at),
        )
        .filter(SurveyDeployment.archived_at.is_(None))
        .group_by(SurveyDeployment.org_id).all()
    )
    deployments = {
        r[0]: {'total': r[1], 'closed': r[2], 'responses': r[3] or 0, 'last': r[4]}
        for r in deployment_rows
    }

    rows = []
    for org in orgs:
        dep = deployments.get(
            org.id, {'total': 0, 'closed': 0, 'responses': 0, 'last': None}
        )
        rows.append({
            'org': org,
            'cycles': cycles.get(org.id, 0),
            'waves': dep['total'],
            'closed': dep['closed'],
            'responses': dep['responses'],
            'last_deployed': dep['last'],
            'light': _traffic_light(dep['last']),
        })

    stats = {
        'organisations': len(rows),
        'active': sum(1 for r in rows if r['light'] == 'green'),
        'needs_attention': sum(1 for r in rows if r['light'] == 'amber'),
        'inactive': sum(1 for r in rows if r['light'] == 'red'),
        'waves': sum(r['waves'] for r in rows),
        'responses': sum(r['responses'] for r in rows),
    }

    return render_template('admin/dashboard.html', rows=rows, stats=stats)


# ── Organisations ───────────────────────────────────────────────────────────

@admin_bp.route('/orgs/new', methods=['GET', 'POST'])
@require_capability(MANAGE_ORGS)
def orgs_new():
    """
    Step 1 of the wizard: create the org as a RESUMABLE DRAFT.

    `setup_complete=False` from this moment, rather than the wizard holding its
    state in the session. An admin who closes the tab after naming the
    organisation loses nothing, and the status board offers "Resume setup"
    instead of leaving a half-built tenant that looks finished.

    The instrument is cloned HERE, in the same transaction as the org row. An
    org without its 48 items is an org whose survey link renders a form with no
    questions, and that failure surfaces only when a respondent opens it.
    """
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        if not name:
            flash('An organisation name is required.', 'error')
            return render_template('admin/org_form.html')

        org = Org(
            name=name,
            industry=(request.form.get('industry') or '').strip() or None,
            active_domains=default_active_domains(),
            setup_complete=False,
            is_active=True,
        )
        db.session.add(org)
        db.session.flush()

        try:
            cloned = clone_instrument_to_org(org.id)
        except ProvisioningError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
            return render_template('admin/org_form.html')

        create_default_segments(org.id)
        record_audit(ORG_CREATE, 'org', org.id,
                     f'Created organisation {org.name}',
                     org_id=org.id,
                     payload={'name': org.name, 'industry': org.industry,
                              'items_cloned': cloned})
        db.session.commit()
        flash(f'{org.name} created with its own copy of the {cloned}-item '
              'instrument.', 'success')
        return redirect(url_for('admin.org_detail', org_id=org.id))

    return render_template('admin/org_form.html')


@admin_bp.route('/orgs/<uuid:org_id>')
@require_capability(MANAGE_ORGS)
def org_detail(org_id):
    org = _client_org_or_404(org_id)
    admins = (db.session.query(User).filter_by(org_id=org.id)
              .order_by(User.role, User.email).all())
    branches = (db.session.query(Branch).filter_by(org_id=org.id)
                .order_by(Branch.name).all())
    items = db.session.query(SurveyItem).filter_by(org_id=org.id).count()
    cycles = (db.session.query(MeasurementCycle)
              .filter_by(org_id=org.id)
              .filter(MeasurementCycle.archived_at.is_(None))
              .order_by(MeasurementCycle.created_at.desc()).all())
    return render_template('admin/org_detail.html', org=org, admins=admins,
                           branches=branches, item_count=items, cycles=cycles)


@admin_bp.route('/orgs/<uuid:org_id>/complete', methods=['POST'])
@require_capability(MANAGE_ORGS)
def org_complete(org_id):
    """
    Mark setup finished. REFUSES while the org has no administrator.

    An org with no admin is unreachable by the client: nobody can sign in to
    create a cycle, so it would sit on the board looking provisioned and
    deliver nothing. Checked here rather than expressed as a capability —
    a capability that depends on data state is untestable and produces
    lockouts.
    """
    org = _client_org_or_404(org_id)
    if not db.session.query(User).filter_by(org_id=org.id, role=ORG_ADMIN).count():
        flash('Add an organisation administrator before completing setup — '
              'otherwise nobody at the client can sign in.', 'error')
        return redirect(url_for('admin.org_detail', org_id=org.id))

    org.setup_complete = True
    record_audit(ORG_EDIT, 'org', org.id, f'Completed setup for {org.name}',
                 org_id=org.id)
    db.session.commit()
    flash(f'{org.name} is ready to use.', 'success')
    return redirect(url_for('admin.org_detail', org_id=org.id))


@admin_bp.route('/orgs/<uuid:org_id>/admins/new', methods=['GET', 'POST'])
@require_capability(MANAGE_ORGS)
def org_admin_new(org_id):
    """Create the client's L2 organisation administrator."""
    org = _client_org_or_404(org_id)

    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        full_name = (request.form.get('full_name') or '').strip() or None

        error = _validate_new_user(email, password, ORG_ADMIN)
        if error:
            flash(error, 'error')
            return render_template('admin/user_form.html', org=org,
                                   role=ORG_ADMIN)

        user = User(org_id=org.id, branch_id=None, email=email,
                    role=ORG_ADMIN, full_name=full_name, is_active=True)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        record_audit(USER_CREATE, 'user', user.id,
                     f'Created organisation admin {email} for {org.name}',
                     org_id=org.id, payload=user_payload(user))
        db.session.commit()
        flash(f'Created {email}. Give them the password directly; it is not '
              'emailed and is not recoverable from here.', 'success')
        return redirect(url_for('admin.org_detail', org_id=org.id))

    return render_template('admin/user_form.html', org=org, role=ORG_ADMIN)


def _client_org_or_404(org_id):
    """
    Fetch a CLIENT org. The master template org is never addressable here.

    It has no admin, no cycle and no respondent; offering its detail page
    invites someone to treat infrastructure as a tenant and, worse, to edit the
    instrument every client clones from through a screen that was not built for
    it. The template editor is the one place that touches it.
    """
    if org_id == MASTER_ORG_ID:
        abort(404)
    org = db.session.get(Org, org_id)
    if org is None:
        abort(404)
    return org


def _validate_new_user(email, password, role):
    """Shared validation. Returns an error string, or None when acceptable."""
    if not email or '@' not in email:
        return 'A valid email address is required.'
    if db.session.query(User).filter_by(email=email).first():
        return (f'{email} already has an account. Email is unique across the '
                'whole platform, not per organisation — one person cannot hold '
                'two tiers, because the role they get would depend on which '
                'row was found first.')
    if len(password) < 8:
        return 'Choose a password of at least 8 characters.'
    if role not in CREATABLE_ROLES.get(current_user.role, []):
        return f'Your role cannot create a {role}.'
    return None


# ── Users ───────────────────────────────────────────────────────────────────

@admin_bp.route('/users')
@require_capability(MANAGE_ORGS)
def users_list():
    """
    Every administrator on the platform.

    RESPONDENTS ARE NOT USERS and never appear here. There is no row to show:
    an employee answering the survey reaches it through the anonymous link and
    the system holds no record of who they are.
    """
    users = (db.session.query(User)
             .order_by(User.role, User.email).all())
    orgs = {o.id: o for o in db.session.query(Org).all()}
    branches = {b.id: b for b in db.session.query(Branch).all()}
    return render_template('admin/users.html', users=users, orgs=orgs,
                           branches=branches)


@admin_bp.route('/users/<uuid:user_id>/toggle', methods=['POST'])
@require_capability(MANAGE_ORGS)
def user_toggle(user_id):
    """
    Deactivate or reactivate an account.

    Deactivation is immediate for a signed-in user too: the user_loader in
    app/__init__.py returns None for an inactive account, so the next request
    ends their session rather than letting it run until they next sign in.
    """
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if user.id == current_user.id:
        # Not a permission question — a usability one. An L1 who deactivates
        # themselves is locked out of the only screen that could undo it.
        flash('You cannot deactivate your own account.', 'error')
        return redirect(url_for('admin.users_list'))

    user.is_active = not user.is_active
    action = USER_REACTIVATE if user.is_active else USER_DEACTIVATE
    verb = 'Reactivated' if user.is_active else 'Deactivated'
    record_audit(action, 'user', user.id, f'{verb} {user.email}',
                 org_id=user.org_id, payload=user_payload(user))
    db.session.commit()
    flash(f'{verb} {user.email}.', 'success')
    return redirect(url_for('admin.users_list'))


# ── Templates (the instrument) ──────────────────────────────────────────────

@admin_bp.route('/templates')
@require_capability(EDIT_TEMPLATES)
def templates_list():
    """The master instrument, by domain, plus every org's cloned copy."""
    counts = dict(
        db.session.query(SurveyItem.domain_code, func.count(SurveyItem.id))
        .filter_by(org_id=MASTER_ORG_ID, version=1)
        .group_by(SurveyItem.domain_code).all()
    )
    org_counts = (
        db.session.query(Org, func.count(SurveyItem.id))
        .join(SurveyItem, SurveyItem.org_id == Org.id)
        .filter(Org.id != MASTER_ORG_ID)
        .group_by(Org.id).order_by(Org.name).all()
    )
    return render_template('admin/templates_list.html', counts=counts,
                           domains=DOMAINS, org_counts=org_counts,
                           total=sum(counts.values()))


@admin_bp.route('/templates/<domain_code>')
@require_capability(EDIT_TEMPLATES)
def template_domain(domain_code):
    """
    The 12 items of one domain, grouped by subdomain, in presentation order.

    Shows `item_code` and the subdomain, which is correct HERE and nowhere a
    respondent can reach: this screen is for the people who own the instrument.
    """
    if domain_code not in DOMAINS:
        abort(404)
    items = (db.session.query(SurveyItem)
             .filter_by(org_id=MASTER_ORG_ID, version=1, domain_code=domain_code)
             .order_by(SurveyItem.subdomain_code, SurveyItem.ordinal).all())
    grouped = [
        (sub, subdomain_label(domain_code, sub),
         [i for i in items if i.subdomain_code == sub])
        for sub in SUBDOMAINS[domain_code]
    ]
    return render_template('admin/template_domain.html',
                           domain_code=domain_code, grouped=grouped)


# ── Audit ───────────────────────────────────────────────────────────────────

@admin_bp.route('/audit')
@require_capability(VIEW_AUDIT_LOG)
def audit_log():
    """
    Append-only record of every destructive or corrective administrative action.

    Capped at 500 rows. An unbounded log screen is fine for a month and then
    becomes the slowest page in the product, on the one screen somebody opens
    when they are already worried about something.
    """
    query = db.session.query(AuditEvent).order_by(AuditEvent.created_at.desc())
    action = (request.args.get('action') or '').strip()
    if action:
        query = query.filter(AuditEvent.action == action)
    events = query.limit(500).all()
    actions = [r[0] for r in db.session.query(AuditEvent.action)
               .distinct().order_by(AuditEvent.action).all()]
    orgs = {o.id: o for o in db.session.query(Org).all()}
    return render_template('admin/audit.html', events=events, actions=actions,
                           selected=action, orgs=orgs)


# ── Placeholders — navigable now, built in the phase named on each ──────────

@admin_bp.route('/catalogue')
@require_capability(MANAGE_CATALOGUE)
def catalogue():
    """
    One catalogue, shared by every tenant.

    `intervention_catalogue` has no org_id, so a single edit here changes what
    every client's report offers. That is why it is L1-only and why it is
    seeded from a reviewable JSON file rather than edited row by row.

    The screen is organised by CELL rather than by course, because the question
    an operator actually has is "what would a finding here produce?" — and the
    answer "nothing" is the one worth seeing.
    """
    rows = catalogue_module.coverage(db.session)
    total = db.session.query(InterventionCatalogue).filter_by(is_active=True).count()
    programmes = sorted({
        r.source_programme for r in
        db.session.query(InterventionCatalogue).filter_by(is_active=True).all()
        if r.source_programme})
    gaps = [r for r in rows if not r['covered']]
    return render_template('admin/catalogue.html', rows=rows, total=total,
                           programmes=programmes, gaps=gaps,
                           reward_guidance=catalogue_module.REWARD_GUIDANCE)


@admin_bp.route('/usage')
@require_capability(VIEW_COSTS)
def usage():
    """
    What interpretation has cost, per organisation and per wave.

    Reads `ai_cost_events`, which is deliberately SEPARATE from `report_events`:
    one records what a narrative cost and is operational, the other records that
    a report was accessed and is a §13.4 compliance record. Merging them means a
    cost cleanup deletes an access record.

    L1 only. A client being shown the unit economics of their own reports is a
    commercial conversation, not a product feature.
    """
    rows = (db.session.query(
                AiCostEvent.org_id,
                func.count(AiCostEvent.id),
                func.sum(AiCostEvent.input_tokens),
                func.sum(AiCostEvent.output_tokens),
                func.sum(AiCostEvent.cost_usd),
                func.max(AiCostEvent.created_at))
            .group_by(AiCostEvent.org_id).all())

    orgs = {o.id: o for o in db.session.query(Org).all()}
    per_org = [{
        'org': orgs.get(row[0]),
        'runs': row[1],
        'input_tokens': row[2] or 0,
        'output_tokens': row[3] or 0,
        'cost': row[4] or Decimal('0'),
        'last': row[5],
    } for row in rows]
    per_org.sort(key=lambda r: r['cost'], reverse=True)

    by_type = (db.session.query(
                   AiCostEvent.report_type,
                   func.count(AiCostEvent.id),
                   func.sum(AiCostEvent.cost_usd))
               .group_by(AiCostEvent.report_type).all())

    recent = (db.session.query(AiCostEvent)
              .order_by(AiCostEvent.created_at.desc()).limit(50).all())

    totals = {
        'runs': sum(r['runs'] for r in per_org),
        'cost': sum((r['cost'] for r in per_org), Decimal('0')),
        'input_tokens': sum(r['input_tokens'] for r in per_org),
        'output_tokens': sum(r['output_tokens'] for r in per_org),
    }

    return render_template('admin/usage.html', per_org=per_org,
                           by_type=by_type, recent=recent, totals=totals,
                           orgs=orgs, model=ai_service.MODEL,
                           configured=ai_service.api_key_present())


@admin_bp.route('/validation')
@require_capability(VIEW_VALIDATION_REPORT)
def validation():
    return render_template(
        'admin/placeholder.html',
        heading='Validation Report',
        phase='Phase 10',
        detail='Internal psychometrics: internal consistency per subdomain, '
               'the General Job Satisfaction / Emotional Experience '
               'discriminant check the specification singles out, item-total '
               'correlations, and straight-lining rates. Evidence about the '
               'instrument, not a finding about any client.',
    )

# ── L1 report access — READ ONLY ───────────────────────────────────────────
#
# The permission table says L1 sees "Reports: all, read-only", and until now it
# could not reach one. The reason is worth recording: every report route lived
# in app/org/, whose every query is scoped by current_user.org_id — and L1 HAS
# NO org_id. A capability that no route can satisfy is a capability in name
# only, which is exactly the trap CLAUDE.md warns about for the retention purge.
#
# READ-ONLY IS THE POINT. L1 oversees and supports; it does not run a client's
# measurement programme. There is deliberately no interpret action here: a
# narrative about a client's workforce is written by that client's own
# administrator, who is accountable for it, or it is not written.
#
# GATED ON MANAGE_ORGS, NOT VIEW_REPORTS.
#
# VIEW_REPORTS is held by L2 and L3 as well, and NOTHING IN THIS BLUEPRINT IS
# ORG-SCOPED — the org comes from the URL. Gated on VIEW_REPORTS, one client's
# administrator could read another client's results by editing the id in the
# address bar. That is the whole product failing at once, and it was live for
# the length of one test run.
#
# The org blueprint can gate on VIEW_REPORTS safely because every query there
# is filtered by current_user.org_id. Here there is no such filter to lean on,
# so the gate must be a capability only L1 holds.

def _client_cycle_or_404(org_id, cycle_id):
    org = _client_org_or_404(org_id)
    cycle = db.session.get(MeasurementCycle, cycle_id)
    if cycle is None or cycle.org_id != org.id:
        abort(404)
    return org, cycle


@admin_bp.route('/orgs/<uuid:org_id>/reports/<cycle_id>/<report_type>')
@require_capability(MANAGE_ORGS)
def org_report(org_id, cycle_id, report_type):
    if report_type not in report_builder.REPORT_TYPES:
        abort(404)
    org, cycle = _client_cycle_or_404(org_id, cycle_id)

    branch = None
    branch_id = (request.args.get('branch') or '').strip()
    if branch_id:
        branch = db.session.get(Branch, branch_id)
        if branch is None or branch.org_id != org.id:
            abort(404)

    deployments = (db.session.query(SurveyDeployment)
                   .filter_by(org_id=org.id, cycle_id=cycle.id)
                   .filter(SurveyDeployment.archived_at.is_(None)).all())
    narrative = (db.session.query(Analysis)
                 .filter_by(org_id=org.id, cycle_id=cycle.id,
                            report_type=report_type,
                            scope_key=(str(branch.id) if branch else None))
                 .order_by(Analysis.generated_at.desc()).first())

    try:
        context = report_builder.assemble(
            db.session, org, cycle, report_type, deployments,
            scope_branch=branch, segments=active_segments(org.id),
            narrative=(narrative.payload if narrative else None))
    except report_builder.ReportNotAvailable as exc:
        return render_template('admin/report_unavailable.html', org=org,
                               cycle=cycle, reason=str(exc),
                               title=report_builder.report_title(report_type))

    return render_template('admin/report_preview.html', org=org, cycle=cycle,
                           context=context, report_type=report_type,
                           branch=branch)


@admin_bp.route('/orgs/<uuid:org_id>/reports/<cycle_id>/<report_type>/<fmt>')
@require_capability(MANAGE_ORGS)
def org_report_download(org_id, cycle_id, report_type, fmt):
    """
    L1 downloads are audited like anybody else's.

    More so, arguably: a platform operator opening a client's workforce
    feedback is precisely the access §13.4 exists to make reviewable.
    """
    if report_type not in report_builder.REPORT_TYPES or fmt not in ('pdf', 'xlsx'):
        abort(404)
    org, cycle = _client_cycle_or_404(org_id, cycle_id)

    branch = None
    branch_id = (request.args.get('branch') or '').strip()
    if branch_id:
        branch = db.session.get(Branch, branch_id)
        if branch is None or branch.org_id != org.id:
            abort(404)

    deployments = (db.session.query(SurveyDeployment)
                   .filter_by(org_id=org.id, cycle_id=cycle.id)
                   .filter(SurveyDeployment.archived_at.is_(None)).all())
    narrative = (db.session.query(Analysis)
                 .filter_by(org_id=org.id, cycle_id=cycle.id,
                            report_type=report_type,
                            scope_key=(str(branch.id) if branch else None))
                 .order_by(Analysis.generated_at.desc()).first())

    try:
        context = report_builder.assemble(
            db.session, org, cycle, report_type, deployments,
            scope_branch=branch, segments=active_segments(org.id),
            narrative=(narrative.payload if narrative else None),
            with_charts=(fmt == 'pdf'))
    except report_builder.ReportNotAvailable as exc:
        flash(str(exc), 'error')
        return redirect(url_for('admin.org_detail', org_id=org.id))

    if fmt == 'pdf':
        payload = report_builder.build_pdf(context)
        mimetype = 'application/pdf'
    else:
        payload = report_builder.build_xlsx(context)
        mimetype = ('application/vnd.openxmlformats-officedocument'
                    '.spreadsheetml.sheet')

    db.session.add(ReportEvent(
        org_id=org.id, cycle_id=cycle.id, report_type=report_type,
        export_format=fmt, scope_key=(str(branch.id) if branch else None),
        generated_by=current_user.id))
    record_audit(REPORT_DOWNLOAD, 'cycle', cycle.id,
                 f'Platform operator downloaded the '
                 f'{report_builder.report_title(report_type)} ({fmt.upper()}) '
                 f'for {org.name}, n={context["n"]}',
                 org_id=org.id,
                 payload={'report_type': report_type, 'format': fmt,
                          'n': context['n'], 'by': 'master_admin'})
    db.session.commit()

    return send_file(
        io.BytesIO(payload), mimetype=mimetype, as_attachment=True,
        download_name=report_builder.report_filename(
            report_type, org.name, cycle.id, fmt))
