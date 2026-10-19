"""
L1 master admin — cross-org.

Every route here is gated on a CAPABILITY, never on a role comparison. L1 has
no org_id, which has a consequence worth stating once: any capability L1 alone
holds — the catalogue, the retention purge, pilot
feedback — must live in THIS blueprint, because every query in app/org/ is
scoped by `current_user.org_id` and L1 can never satisfy one. The sibling
product learned this by putting a candidate purge in the wrong blueprint and
finding it unreachable by the only role allowed to use it.
"""
import io
import re
import uuid
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

# Catalogue codes are a stable JOIN KEY and appear in generated reports, so
# they are constrained to something safe in a filename and readable in an
# email - the same discipline as a cycle reference.
_CATALOGUE_CODE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{2,49}$')
# Imported as report_builder, not reports: app/org/routes.py defines a view
# called reports() and the two blueprints are read side by side. Keeping one
# name for the module everywhere is cheaper than remembering which file is
# safe.
from app import catalogue as catalogue_module
from app import domains
from app.analytics import reports as report_builder
from app.analytics import scoring
from app.audit import (
    CATALOGUE_EDIT, INSTRUMENT_EDIT, ORG_CREATE, ORG_EDIT, REPORT_DOWNLOAD,
    USER_CREATE, USER_DEACTIVATE, USER_REACTIVATE, record_audit, user_payload,
)
from app.auth.routes import require_capability
from app.domains import (
    DOMAIN_LABELS, DOMAINS, MASTER_ORG_ID, OUTCOME_DOMAIN, SUBDOMAINS,
    subdomain_label,
)
from app.models import (
    AiCostEvent, Analysis, AuditEvent, Branch, InterventionCatalogue,
    InterventionCell, MeasurementCycle, Org, ReportEvent,
    SurveyDeployment, SurveyItem, User,
)
from app.permissions import (
    CREATABLE_ROLES, EDIT_TEMPLATES, MANAGE_CATALOGUE, MANAGE_ORGS, ORG_ADMIN,
    VIEW_AUDIT_LOG, VIEW_COSTS,
)
from app.provisioning import (
    ProvisioningError, clone_instrument_to_org,
    default_active_domains, latest_version,
)

# Days since the last deployment, for the status-board traffic light.
# Same thresholds as the sibling boards so one operator reads all three the
# same way.
_AMBER_AFTER_DAYS = 60
_RED_AFTER_DAYS = 90


def _traffic_light(last_deployed):
    """green < 60 days since the last deployment, amber 60-90, red > 90 or never."""
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
            'domains': [d for d in (org.active_domains or []) if d in DOMAINS],
            'cycles': cycles.get(org.id, 0),
            'deployments': dep['total'],
            'closed': dep['closed'],
            'responses': dep['responses'],
            'last_deployed': dep['last'],
            'light': _traffic_light(dep['last']),
        })

    industries = sorted({r['org'].industry for r in rows if r['org'].industry})

    stats = {
        'organisations': len(rows),
        'active': sum(1 for r in rows if r['light'] == 'green'),
        'needs_attention': sum(1 for r in rows if r['light'] == 'amber'),
        'inactive': sum(1 for r in rows if r['light'] == 'red'),
        'deployments': sum(r['deployments'] for r in rows),
        'responses': sum(r['responses'] for r in rows),
    }

    return render_template('admin/dashboard.html', rows=rows, stats=stats,
                           industries=industries)


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
            return render_template('admin/org_form.html', wizard_step=1)

        # Scope. The outcome domain is not optional: every diagnostic domain
        # is reported IN RELATION TO it (§10.4), so an org without GEM cannot
        # produce a diagnostic at all. It is forced on rather than offered.
        #
        # `scope_submitted` distinguishes "every box was unticked" from "this
        # caller did not render the field". An unchecked checkbox sends
        # nothing, so without the marker the two are identical and unticking
        # the diagnostics would silently look like a default.
        if request.form.get('scope_submitted'):
            chosen = [d for d in request.form.getlist('domains') if d in DOMAINS]
            active = [d for d in DOMAINS if d == OUTCOME_DOMAIN or d in chosen]
            if len(active) < 2:
                flash('Choose at least one diagnostic domain. '
                      f'{DOMAIN_LABELS[OUTCOME_DOMAIN]} on its own measures the '
                      'outcome with nothing to relate it to, which is not a '
                      'diagnostic.', 'error')
                return render_template('admin/org_form.html', wizard_step=1)
        else:
            active = default_active_domains()

        org = Org(
            name=name,
            industry=(request.form.get('industry') or '').strip() or None,
            active_domains=active,
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
            return render_template('admin/org_form.html', wizard_step=1)

        # No default segments any more: segments were removed from the
        # product, so a new org seeds the instrument and nothing else.
        record_audit(ORG_CREATE, 'org', org.id,
                     f'Created organisation {org.name}',
                     org_id=org.id,
                     payload={'name': org.name, 'industry': org.industry,
                              'items_cloned': cloned})
        db.session.commit()
        flash(f'{org.name} created with its own copy of the {cloned}-item '
              'instrument.', 'success')
        return redirect(url_for('admin.org_setup_instrument', org_id=org.id))

    return render_template('admin/org_form.html', wizard_step=1)


@admin_bp.route('/orgs/<uuid:org_id>/setup/instrument')
@require_capability(MANAGE_ORGS)
def org_setup_instrument(org_id):
    """
    Step 2: review the instrument this client now holds.

    Read-only on purpose. The clone is already made and is EVIDENCE of what
    this client will ask; the place to change the wording is the master
    template, before the next client is created. Showing it here is a check
    that the right 48 items arrived, not an invitation to edit them.
    """
    org = _client_org_or_404(org_id)
    version = latest_version(org.id)
    items = (db.session.query(SurveyItem)
             .filter_by(org_id=org.id, version=version)
             .order_by(SurveyItem.sort_order).all())
    assessed = org.active_domains or list(DOMAINS)
    grouped = [(d, [i for i in items if i.domain_code == d])
               for d in DOMAINS if d in assessed]
    return render_template('admin/org_setup_instrument.html', org=org,
                           grouped=grouped, total=len(items),
                           version=version, wizard_step=2)


@admin_bp.route('/orgs/<uuid:org_id>/setup/confirm')
@require_capability(MANAGE_ORGS)
def org_setup_confirm(org_id):
    """
    Step 4: what was set up, and the one thing that blocks finishing.

    The administrator check lives in `org_complete`, not here — this screen
    reports it so the operator sees the blocker before pressing the button,
    but the refusal that matters is the one on the POST.
    """
    org = _client_org_or_404(org_id)
    admins = (db.session.query(User)
              .filter_by(org_id=org.id, role=ORG_ADMIN)
              .order_by(User.email).all())
    items = db.session.query(SurveyItem).filter_by(org_id=org.id).count()
    return render_template('admin/org_setup_confirm.html', org=org,
                           admins=admins, item_count=items, wizard_step=4)


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
            return render_template(
                'admin/user_form.html', org=org, role=ORG_ADMIN,
                wizard_step=(None if org.setup_complete else 3))

        user = User(org_id=org.id, branch_id=None, email=email,
                    role=ORG_ADMIN, full_name=full_name, is_active=True)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        record_audit(USER_CREATE, 'user', user.id,
                     f'Created organisation admin {email} for {org.name}',
                     org_id=org.id, payload=user_payload(user))
        db.session.commit()
        # THE §9.2 HANDOVER, SAID AT THE MOMENT THE ACCOUNT IS HANDED OVER.
        # The same prompt waits for them on their own dashboard until it is
        # done, but the person creating the account is usually the person
        # briefing them, and this is the one moment that conversation happens.
        flash(f'Created {email}. Give them the password directly; it is not '
              'emailed and is not recoverable from here. Ask them to write '
              'what respondents are told, under Settings, before they create '
              'their first cycle — every cycle, including their branch '
              'admins’, starts from it.', 'success')
        if not org.setup_complete:
            return redirect(url_for('admin.org_setup_confirm', org_id=org.id))
        return redirect(url_for('admin.org_detail', org_id=org.id))

    return render_template('admin/user_form.html', org=org, role=ORG_ADMIN,
                           wizard_step=(None if org.setup_complete else 3))


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
    # Filter options come from the rows actually rendered, so a select can
    # never offer a value that matches nothing.
    roles = sorted({u.role for u in users})
    org_names = sorted({orgs[u.org_id].name for u in users
                        if u.org_id and u.org_id in orgs})
    return render_template('admin/users.html', users=users, orgs=orgs,
                           branches=branches, roles=roles,
                           org_names=org_names)


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
    """The master instrument, by domain."""
    version = latest_version(MASTER_ORG_ID)
    counts = dict(
        db.session.query(SurveyItem.domain_code, func.count(SurveyItem.id))
        .filter_by(org_id=MASTER_ORG_ID, version=version)
        .group_by(SurveyItem.domain_code).all()
    )
    return render_template('admin/templates_list.html', counts=counts,
                           domains=DOMAINS, version=version,
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
    version = latest_version(MASTER_ORG_ID)
    items = (db.session.query(SurveyItem)
             .filter_by(org_id=MASTER_ORG_ID, version=version,
                        domain_code=domain_code)
             .order_by(SurveyItem.sort_order, SurveyItem.subdomain_code,
                       SurveyItem.ordinal).all())
    grouped = [
        (sub, subdomain_label(domain_code, sub),
         [i for i in items if i.subdomain_code == sub])
        for sub in SUBDOMAINS[domain_code]
    ]
    return render_template('admin/template_domain.html',
                           domain_code=domain_code, grouped=grouped,
                           items=items, version=version)


@admin_bp.route('/templates/<domain_code>/edit', methods=['GET', 'POST'])
@require_capability(EDIT_TEMPLATES)
def template_edit(domain_code):
    """
    Edit one domain of the master instrument.

    A save NEVER rewrites an item in place. It copies the whole instrument to
    version N+1 with the edits applied, because a client's cloned copy is
    evidence of what was asked and the master's own history is the only record
    of what the clone was made from. Existing clients are untouched either way:
    they hold their own rows, and only a future provisioning reads this.

    Item codes, subdomains and ordinals are NOT editable. They are the identity
    the scoring ladder groups by (§10.1), and letting an admin retype
    'GEM-G-01' as 'GEM-G-02' would silently merge two cells.
    """
    if domain_code not in DOMAINS:
        abort(404)

    version = latest_version(MASTER_ORG_ID)
    items = (db.session.query(SurveyItem)
             .filter_by(org_id=MASTER_ORG_ID, version=version,
                        domain_code=domain_code)
             .order_by(SurveyItem.sort_order, SurveyItem.subdomain_code,
                       SurveyItem.ordinal).all())
    if not items:
        abort(404)

    if request.method == 'POST':
        edits = {}
        for item in items:
            text = (request.form.get(f'text-{item.id}') or '').strip()
            if not text:
                flash(f'{item.item_code} cannot have empty question text.',
                      'error')
                return render_template('admin/template_edit.html',
                                       domain_code=domain_code, items=items,
                                       version=version)
            edits[item.id] = {
                'item_text': text,
                'is_active': request.form.get(f'active-{item.id}') == 'on',
                'is_reverse_scored':
                    request.form.get(f'reverse-{item.id}') == 'on',
            }

        changed = [i.item_code for i in items
                   if edits[i.id]['item_text'] != i.item_text
                   or edits[i.id]['is_active'] != i.is_active
                   or edits[i.id]['is_reverse_scored'] != i.is_reverse_scored]
        if not changed:
            flash('No changes to save.', 'info')
            return redirect(url_for('admin.template_domain',
                                    domain_code=domain_code))

        # The whole instrument moves forward together. A version holding only
        # the edited domain would be a 12-item instrument, and
        # clone_instrument_to_org refuses anything that is not 48.
        whole = (db.session.query(SurveyItem)
                 .filter_by(org_id=MASTER_ORG_ID, version=version).all())
        for item in whole:
            edit = edits.get(item.id)
            db.session.add(SurveyItem(
                org_id=MASTER_ORG_ID,
                version=version + 1,
                domain_code=item.domain_code,
                subdomain_code=item.subdomain_code,
                item_code=item.item_code,
                ordinal=item.ordinal,
                sort_order=item.sort_order,
                item_text=edit['item_text'] if edit else item.item_text,
                anchor_type=item.anchor_type,
                allows_na=item.allows_na,
                is_reverse_scored=(edit['is_reverse_scored'] if edit
                                   else item.is_reverse_scored),
                is_active=edit['is_active'] if edit else item.is_active,
            ))

        record_audit(
            INSTRUMENT_EDIT, 'survey_item_version', version + 1,
            f'Master instrument {domain_code} edited; version '
            f'{version} copied to {version + 1}.',
            payload={'domain_code': domain_code,
                     'from_version': version,
                     'to_version': version + 1,
                     'item_codes_changed': sorted(changed)},
        )
        db.session.commit()
        flash(f'Saved as version {version + 1}. '
              f'{len(changed)} question(s) changed. Existing clients are '
              'unaffected.', 'success')
        return redirect(url_for('admin.template_domain',
                                domain_code=domain_code))

    return render_template('admin/template_edit.html',
                           domain_code=domain_code, items=items,
                           version=version)


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

    The coverage grid comes first because the question an operator actually
    has is "what would a finding here produce?" — and the answer "nothing" is
    the one worth seeing. The topic list below it is the catalogue itself.
    """
    rows = catalogue_module.coverage(db.session)
    gaps = [r for r in rows if not r['covered']]
    # The same cells as a domain x subdomain grid, for the coverage panel.
    grid = [(d, [r for r in rows if r['domain_code'] == d]) for d in DOMAINS]

    # Every active topic with the cells it is mapped to, grouped by programme.
    # A topic may sit in more than one cell, so the cells are collected onto
    # the topic rather than repeating the topic once per cell.
    cells_by_topic = {}
    for cell in db.session.query(InterventionCell).all():
        cells_by_topic.setdefault(cell.intervention_id, []).append(
            (cell.domain_code, cell.subdomain_code))

    courses = []
    for row in (db.session.query(InterventionCatalogue)
                .filter_by(is_active=True)
                .order_by(InterventionCatalogue.source_programme,
                          InterventionCatalogue.sort_order,
                          InterventionCatalogue.intervention_code).all()):
        pairs = sorted(cells_by_topic.get(row.id, []))
        courses.append({
            'id': row.id,
            'code': row.intervention_code,
            'title': row.title,
            'programme': row.source_programme or 'Unassigned',
            'type': row.intervention_type,
            'cells': [(d, subdomain_label(d, s)) for d, s in pairs],
            'domains': sorted({d for d, _ in pairs}),
        })
    by_programme = [
        (name, [c for c in courses if c['programme'] == name])
        for name in sorted({c['programme'] for c in courses})
    ]

    # Removed topics are shown in their own section rather than hidden. A
    # topic that silently vanished is one an operator re-creates under a
    # second code, and then both appear in every report.
    retired = [
        {'id': row.id, 'code': row.intervention_code, 'title': row.title,
         'programme': row.source_programme or 'Unassigned',
         'type': row.intervention_type}
        for row in (db.session.query(InterventionCatalogue)
                    .filter_by(is_active=False)
                    .order_by(InterventionCatalogue.intervention_code).all())
    ]

    return render_template('admin/catalogue.html', gaps=gaps, grid=grid,
                           by_programme=by_programme, courses=courses,
                           retired=retired)


# ── Editing the catalogue (L1 only) ─────────────────────────────────────────
#
# It is shared reference data with no org_id, so one edit here changes what
# EVERY client's report offers. That is why these routes are L1-only, why a
# removal is reversible, and why seed_catalogue.py still exists: a bulk import
# belongs in a reviewable JSON file that diffs in git. These screens are for
# the single correction, not for loading a curriculum.


def _catalogue_or_404(intervention_id):
    row = db.session.get(InterventionCatalogue, intervention_id)
    if row is None:
        abort(404)
    return row


def _catalogue_form_values(form):
    return {
        'intervention_code': (form.get('intervention_code') or '').strip().upper(),
        'title': (form.get('title') or '').strip(),
        'description': (form.get('description') or '').strip() or None,
        'intervention_type': (form.get('intervention_type') or '').strip(),
        'actionable_by': (form.get('actionable_by') or 'org').strip(),
        'source_programme': (form.get('source_programme') or '').strip() or None,
        'cells': form.getlist('cells'),
    }


def _validate_catalogue(values, existing=None):
    """
    Returns an error string, or None.

    THE ONE RULE THAT IS NOT HOUSEKEEPING is the last one. Everything above it
    keeps the row usable; §8.5 keeps the product honest.
    """
    if not _CATALOGUE_CODE_RE.match(values['intervention_code']):
        return ('The code must be 3-50 characters of letters, numbers, hyphens '
                'or underscores - it is the stable join key and appears in '
                'reports. For example PO-EPS-T01.')
    clash = (db.session.query(InterventionCatalogue)
             .filter_by(intervention_code=values['intervention_code']).first())
    if clash is not None and (existing is None or clash.id != existing.id):
        return (f'{values["intervention_code"]} is already in use'
                + (' by a removed topic - restore it instead.'
                   if not clash.is_active else '.'))

    if not values['title']:
        return 'A topic needs a title. It is what the report prints.'
    if values['intervention_type'] not in domains.INTERVENTION_TYPES:
        return 'Choose an intervention type.'
    if values['actionable_by'] not in ('org', 'manager', 'both'):
        return 'Choose who can act on this.'

    cells = []
    for raw in values['cells']:
        parts = raw.split(':')
        if len(parts) != 2:
            return 'Unrecognised capability area.'
        domain_code, subdomain_code = parts
        if subdomain_code not in domains.SUBDOMAINS.get(domain_code, []):
            return f'Unrecognised capability area: {raw}.'
        cells.append((domain_code, subdomain_code))

    if not cells:
        # Not a nicety: InterventionCell's own docstring says a topic with no
        # row there reaches no report. Saving one is saving something that
        # cannot ever be recommended, with nothing on screen to say so.
        return ('Map the topic to at least one capability area. A topic with '
                'no area reaches no report at all.')

    # §8.5, THE SPECIFICATION'S ONE EXPLICIT PROHIBITION: "Training should not
    # be treated as a substitute for addressing substantiated compensation
    # inequities or structural reward problems." Enforced here as well as in
    # app/catalogue.py, because a row that cannot legally be recommended
    # should never be storable in the first place.
    if values['intervention_type'] == domains.TRAINING:
        banned = [f'{DOMAIN_LABELS.get(d, d)} - {subdomain_label(d, sub)}'
                  for d, sub in cells if not domains.allows_training(d, sub)]
        if banned:
            return ('Specification §8.5: a capability-development topic cannot '
                    'be mapped to ' + '; '.join(banned) + '. Training is not a '
                    'substitute for addressing a compensation or structural '
                    'reward problem. Choose another intervention type, or '
                    'another capability area.')

    values['parsed_cells'] = cells
    return None


def _write_cells(row, cells):
    """Replace a topic's cells wholesale. Simpler than diffing, and the
    unique index on (intervention_id, domain, subdomain) stays satisfied."""
    db.session.query(InterventionCell).filter_by(intervention_id=row.id).delete(
        synchronize_session=False)
    for domain_code, subdomain_code in cells:
        db.session.add(InterventionCell(
            intervention_id=row.id, domain_code=domain_code,
            subdomain_code=subdomain_code))


@admin_bp.route('/catalogue/new', methods=['GET', 'POST'])
@require_capability(MANAGE_CATALOGUE)
def catalogue_new():
    if request.method == 'POST':
        values = _catalogue_form_values(request.form)
        error = _validate_catalogue(values)
        if error:
            flash(error, 'error')
            return render_template('admin/catalogue_form.html', row=None,
                                   form=values)

        row = InterventionCatalogue(
            intervention_code=values['intervention_code'],
            title=values['title'],
            description=values['description'],
            intervention_type=values['intervention_type'],
            actionable_by=values['actionable_by'],
            source_programme=values['source_programme'],
            is_active=True,
        )
        db.session.add(row)
        db.session.flush()
        _write_cells(row, values['parsed_cells'])
        record_audit(CATALOGUE_EDIT, 'intervention', row.id,
                     f'Added catalogue topic {row.intervention_code}',
                     payload={'code': row.intervention_code,
                              'type': row.intervention_type,
                              'cells': [f'{d}:{s}' for d, s
                                        in values['parsed_cells']]})
        db.session.commit()
        flash(f'Added {row.intervention_code}. It is now offered to every '
              'client whose report reaches one of its capability areas.',
              'success')
        return redirect(url_for('admin.catalogue'))

    return render_template('admin/catalogue_form.html', row=None, form={
        'intervention_code': '', 'title': '', 'description': '',
        'intervention_type': '', 'actionable_by': 'org',
        'source_programme': '', 'cells': [],
    })


@admin_bp.route('/catalogue/<uuid:intervention_id>/edit', methods=['GET', 'POST'])
@require_capability(MANAGE_CATALOGUE)
def catalogue_edit(intervention_id):
    row = _catalogue_or_404(intervention_id)

    if request.method == 'POST':
        values = _catalogue_form_values(request.form)
        error = _validate_catalogue(values, existing=row)
        if error:
            flash(error, 'error')
            return render_template('admin/catalogue_form.html', row=row,
                                   form=values)

        row.intervention_code = values['intervention_code']
        row.title = values['title']
        row.description = values['description']
        row.intervention_type = values['intervention_type']
        row.actionable_by = values['actionable_by']
        row.source_programme = values['source_programme']
        _write_cells(row, values['parsed_cells'])
        record_audit(CATALOGUE_EDIT, 'intervention', row.id,
                     f'Edited catalogue topic {row.intervention_code}',
                     payload={'code': row.intervention_code,
                              'type': row.intervention_type,
                              'cells': [f'{d}:{s}' for d, s
                                        in values['parsed_cells']]})
        db.session.commit()
        flash(f'Saved {row.intervention_code}.', 'success')
        return redirect(url_for('admin.catalogue'))

    cells = [f'{c.domain_code}:{c.subdomain_code}' for c in
             db.session.query(InterventionCell)
             .filter_by(intervention_id=row.id).all()]
    return render_template('admin/catalogue_form.html', row=row, form={
        'intervention_code': row.intervention_code,
        'title': row.title,
        'description': row.description or '',
        'intervention_type': row.intervention_type,
        'actionable_by': row.actionable_by,
        'source_programme': row.source_programme or '',
        'cells': cells,
    })


@admin_bp.route('/catalogue/<uuid:intervention_id>/status', methods=['POST'])
@require_capability(MANAGE_CATALOGUE)
def catalogue_status(intervention_id):
    """
    Remove a topic from the catalogue, or put it back.

    SOFT, NOT A DELETE. `coverage()` and every report filter on is_active, so
    a removed topic stops being offered everywhere at once — which is what
    "remove" has to mean for data shared by every tenant. It is kept because
    the code is a stable join key that has appeared in generated reports, and
    because an operator who removes the wrong row at 17:00 should not need a
    database restore.
    """
    row = _catalogue_or_404(intervention_id)
    restore = request.form.get('restore') == '1'
    row.is_active = restore
    record_audit(CATALOGUE_EDIT, 'intervention', row.id,
                 f'{"Restored" if restore else "Removed"} catalogue topic '
                 f'{row.intervention_code}',
                 payload={'code': row.intervention_code,
                          'is_active': row.is_active})
    db.session.commit()
    flash(f'{row.intervention_code} '
          + ('is back in the catalogue.' if restore
             else 'is no longer offered in any report.'), 'success')
    return redirect(url_for('admin.catalogue'))


@admin_bp.route('/usage')
@require_capability(VIEW_COSTS)
def usage():
    """
    What interpretation has cost, per organisation and per cycle.

    Reads `ai_cost_events`, which is deliberately SEPARATE from `report_events`:
    one records what a narrative cost and is operational, the other records that
    a report was accessed and is a §13.4 compliance record. Merging them means a
    cost cleanup deletes an access record.

    L1 only. A client being shown the unit economics of their own reports is a
    commercial conversation, not a product feature.
    """
    orgs = {o.id: o for o in db.session.query(Org).all()}
    cycles = db.session.query(MeasurementCycle).all()

    # AI cost and report downloads are counted SEPARATELY and never merged:
    # ai_cost_events is operational, report_events is a §13.4 access record.
    ai_by_cycle = {}
    for ev in db.session.query(AiCostEvent).all():
        key = (ev.org_id, ev.cycle_id)
        agg = ai_by_cycle.setdefault(key, {'runs': 0, 'cost': Decimal('0'),
                                           'input_tokens': 0,
                                           'output_tokens': 0, 'last': None})
        agg['runs'] += 1
        agg['cost'] += ev.cost_usd or Decimal('0')
        agg['input_tokens'] += ev.input_tokens or 0
        agg['output_tokens'] += ev.output_tokens or 0
        if agg['last'] is None or (ev.created_at and ev.created_at > agg['last']):
            agg['last'] = ev.created_at

    dl_by_cycle = {}
    for ev in db.session.query(ReportEvent).all():
        key = (ev.org_id, ev.cycle_id)
        agg = dl_by_cycle.setdefault(key, {'pdf': 0, 'excel': 0, 'total': 0})
        agg['total'] += 1
        if ev.export_format == 'pdf':
            agg['pdf'] += 1
        elif ev.export_format == 'excel':
            agg['excel'] += 1

    def _blank_ai():
        return {'runs': 0, 'cost': Decimal('0'), 'input_tokens': 0,
                'output_tokens': 0, 'last': None}

    per_org = []
    for org in sorted(orgs.values(), key=lambda o: o.name):
        org_cycles = [c for c in cycles if c.org_id == org.id]
        rows = []
        for cycle in sorted(org_cycles, key=lambda c: c.cycle_name):
            ai = ai_by_cycle.get((org.id, cycle.id), _blank_ai())
            dl = dl_by_cycle.get((org.id, cycle.id),
                                 {'pdf': 0, 'excel': 0, 'total': 0})
            rows.append({'cycle': cycle, 'ai': ai, 'dl': dl})

        # Events whose cycle was deleted still cost money, so they are shown
        # against the org rather than dropped from the total.
        loose_ai = ai_by_cycle.get((org.id, None))
        loose_dl = dl_by_cycle.get((org.id, None))
        if loose_ai or loose_dl:
            rows.append({'cycle': None,
                         'ai': loose_ai or _blank_ai(),
                         'dl': loose_dl or {'pdf': 0, 'excel': 0, 'total': 0}})

        if not rows:
            continue
        per_org.append({
            'org': org,
            'rows': rows,
            'cycle_count': len(org_cycles),
            'runs': sum(r['ai']['runs'] for r in rows),
            'cost': sum((r['ai']['cost'] for r in rows), Decimal('0')),
            'input_tokens': sum(r['ai']['input_tokens'] for r in rows),
            'output_tokens': sum(r['ai']['output_tokens'] for r in rows),
            'pdf': sum(r['dl']['pdf'] for r in rows),
            'excel': sum(r['dl']['excel'] for r in rows),
            'downloads': sum(r['dl']['total'] for r in rows),
        })
    per_org.sort(key=lambda r: (-r['cost'], r['org'].name))

    totals = {
        'orgs': len(per_org),
        'runs': sum(r['runs'] for r in per_org),
        'cost': sum((r['cost'] for r in per_org), Decimal('0')),
        'input_tokens': sum(r['input_tokens'] for r in per_org),
        'output_tokens': sum(r['output_tokens'] for r in per_org),
        'pdf': sum(r['pdf'] for r in per_org),
        'excel': sum(r['excel'] for r in per_org),
        'downloads': sum(r['downloads'] for r in per_org),
    }

    return render_template('admin/usage.html', per_org=per_org,
                           totals=totals, model=ai_service.MODEL,
                           configured=ai_service.api_key_present())


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
            scope_branch=branch, segments=[],
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
            scope_branch=branch, segments=[],
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

@admin_bp.route('/reports')
@require_capability(MANAGE_ORGS)
def reports_board():
    """
    Every client's reports, on one screen.

    Both sibling products bury this inside the organisation detail page, which
    is fine when an operator is looking at one client and wrong when they are
    supporting twelve: the question "which cycles are ready to report on" cannot
    be answered without opening every org in turn.

    Same three states as the client-facing screen — not deployed / still
    collecting / below the threshold — because they are three different jobs
    and the third is not a job at all.

    Read-only, like every L1 report path. No interpret action: a narrative
    about a client's workforce is written by that client's own administrator.
    """
    org_query = db.session.query(Org).filter(Org.id != MASTER_ORG_ID)
    only_org = (request.args.get('org') or '').strip()
    if only_org:
        try:
            org_query = org_query.filter(Org.id == uuid.UUID(only_org))
        except ValueError:
            abort(404)
    orgs = org_query.order_by(Org.name).all()
    org_by_id = {o.id: o for o in orgs}

    cycles = (db.session.query(MeasurementCycle)
              .filter(MeasurementCycle.org_id.in_(list(org_by_id)))
              .filter(MeasurementCycle.archived_at.is_(None))
              .order_by(MeasurementCycle.created_at.desc()).all()
              if org_by_id else [])

    deployments_by_cycle = {}
    for deployment in (db.session.query(SurveyDeployment)
                 .filter(SurveyDeployment.archived_at.is_(None)).all()):
        deployments_by_cycle.setdefault(deployment.cycle_id, []).append(deployment)

    # One grouped query rather than one per cycle: a platform with forty
    # clients would otherwise issue forty queries to draw one table.
    narrated = {
        (row[0], row[1])
        for row in db.session.query(Analysis.cycle_id, Analysis.report_type).all()
    }

    groups = {}
    for cycle in cycles:
        deployments = deployments_by_cycle.get(cycle.id, [])
        responses = sum(w.responses_received for w in deployments)
        groups.setdefault(cycle.org_id, []).append({
            'cycle': cycle,
            'deployments': deployments,
            'responses': responses,
            'expected': sum(w.expected_respondents for w in deployments),
            'state': _report_state(cycle, deployments, responses),
            'narrated': {t for (c, t) in narrated if c == cycle.id},
        })

    # Per-org counters for the header strip. Downloads come from report_events
    # and interpretations from analyses; the two are never summed.
    downloads = dict(
        db.session.query(ReportEvent.org_id, func.count(ReportEvent.id))
        .group_by(ReportEvent.org_id).all())
    pdfs = dict(
        db.session.query(ReportEvent.org_id, func.count(ReportEvent.id))
        .filter(ReportEvent.export_format == 'pdf')
        .group_by(ReportEvent.org_id).all())
    narratives = dict(
        db.session.query(Analysis.org_id, func.count(Analysis.id))
        .group_by(Analysis.org_id).all())
    branch_counts = dict(
        db.session.query(Branch.org_id, func.count(Branch.id))
        .group_by(Branch.org_id).all())

    rows = []
    for org_id, entries in groups.items():
        all_downloads = downloads.get(org_id, 0)
        pdf_count = pdfs.get(org_id, 0)
        rows.append({
            'org': org_by_id[org_id],
            'cycles': entries,
            'branches': branch_counts.get(org_id, 0),
            'deployments': sum(len(c['deployments']) for c in entries),
            'responses': sum(c['responses'] for c in entries),
            'narratives': narratives.get(org_id, 0),
            'downloads': all_downloads,
            'pdf': pdf_count,
            'excel': all_downloads - pdf_count,
        })
    rows.sort(key=lambda r: r['org'].name)

    totals = {
        'organisations': len(rows),
        'cycles': len(cycles),
        'ready': sum(1 for r in rows for c in r['cycles']
                     if c['state']['key'] == 'ready'),
        'responses': sum(c['responses'] for r in rows for c in r['cycles']),
    }
    return render_template('admin/reports_board.html', rows=rows,
                           single_org=(rows[0] if only_org and rows else None),
                           totals=totals,
                           report_types=report_builder.REPORT_TYPES,
                           report_title=report_builder.report_title)


def _report_state(cycle, deployments, responses):
    """
    Shared with the client-facing screen in app/org/routes.py, deliberately
    duplicated rather than imported: that module defines a view called
    reports() and importing from it across blueprints is how the shadowing bug
    this project already paid for gets a second home. Three states, one
    meaning, two short functions. A still-collecting cycle is ready and
    provisional; it is not withheld.
    """
    if not deployments:
        return {'key': 'not_deployed', 'provisional': False,
                'needs': 'not deployed yet'}
    if responses < cycle.min_reporting_n:
        short = cycle.min_reporting_n - responses
        return {'key': 'below_threshold', 'provisional': False,
                'needs': (f'{responses} of {cycle.min_reporting_n} responses '
                          f'needed \u2014 {short} more')}
    # Still collecting does NOT withhold the reports - see the longer note
    # on the copy in app/org/routes.py. The board marks them provisional.
    return {'key': 'ready', 'needs': None,
            'provisional': any(not w.closed for w in deployments)}