"""
L1 master admin — cross-org. Phase 1: the status board plus navigable
placeholders for the screens phases 3-11 fill in.

Every route here is gated on a CAPABILITY, never on a role comparison. L1 has
no org_id, which has a consequence worth stating once: any capability L1 alone
holds — the catalogue, the validation report, the retention purge, pilot
feedback — must live in THIS blueprint, because every query in app/org/ is
scoped by `current_user.org_id` and L1 can never satisfy one. The sibling
product learned this by putting a candidate purge in the wrong blueprint and
finding it unreachable by the only role allowed to use it.
"""
from datetime import datetime, timedelta, timezone

from flask import render_template
from sqlalchemy import func

from app import db
from app.admin import admin_bp
from app.auth.routes import require_capability
from app.domains import MASTER_ORG_ID
from app.models import MeasurementCycle, Org, SurveyDeployment
from app.permissions import (
    MANAGE_CATALOGUE, MANAGE_ORGS, VIEW_AUDIT_LOG, VIEW_COSTS, EDIT_TEMPLATES,
    VIEW_VALIDATION_REPORT,
)

# Days since the last wave closed, for the status-board traffic light. Same
# thresholds as the sibling products so one operator reads all three boards the
# same way.
_AMBER_AFTER_DAYS = 60
_RED_AFTER_DAYS = 90


def _traffic_light(last_deployed):
    """green < 60 days since the last wave, amber 60-90, red > 90 or never."""
    if last_deployed is None:
        return 'red'
    # Rows written before a timezone-aware default was in place can come back
    # naive; comparing a naive to an aware datetime raises, and it raises on a
    # dashboard, which is the worst place for it.
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


# ── Placeholders — navigable now, built in the phase named on each ──────────
#
# These exist in phase 1 so the three-tier shell is complete and walkable while
# the engine is built behind it. A nav link to a 404 is indistinguishable from a
# bug, and six phases with nothing to look at is how a build loses its thread.

@admin_bp.route('/templates')
@require_capability(EDIT_TEMPLATES)
def templates_list():
    return render_template(
        'admin/placeholder.html',
        heading='Instrument Templates',
        phase='Phase 3',
        detail='The 48-item master instrument, per-client clones frozen on '
               'first use, and the L1 editor.',
    )


@admin_bp.route('/catalogue')
@require_capability(MANAGE_CATALOGUE)
def catalogue():
    return render_template(
        'admin/placeholder.html',
        heading='Intervention Catalogue',
        phase='Phase 10',
        detail='One catalogue shared by every tenant, joined on '
               '(domain, subdomain) and filtered by intervention type. '
               'Awaiting the curriculum content.',
    )


@admin_bp.route('/users')
@require_capability(MANAGE_ORGS)
def users_list():
    return render_template(
        'admin/placeholder.html',
        heading='Users',
        phase='Phase 2',
        detail='Organisation and branch administrators. Respondents are never '
               'users and never appear here.',
    )


@admin_bp.route('/usage')
@require_capability(VIEW_COSTS)
def usage():
    return render_template(
        'admin/placeholder.html',
        heading='Usage & Costs',
        phase='Phase 11',
        detail='Narrative generation cost per organisation and per wave, from '
               'ai_cost_events.',
    )


@admin_bp.route('/audit')
@require_capability(VIEW_AUDIT_LOG)
def audit_log():
    return render_template(
        'admin/placeholder.html',
        heading='Audit Log',
        phase='Phase 2',
        detail='Append-only record of every destructive or corrective '
               'administrative action.',
    )


@admin_bp.route('/validation')
@require_capability(VIEW_VALIDATION_REPORT)
def validation():
    return render_template(
        'admin/placeholder.html',
        heading='Validation Report',
        phase='Phase 10',
        detail="Internal psychometrics: internal consistency per subdomain, "
               "the General Job Satisfaction / Emotional Experience "
               "discriminant check the specification singles out, item-total "
               "correlations, N/A and straight-lining rates. Evidence about "
               "the instrument, not a finding about any client.",
    )


@admin_bp.route('/orgs/new')
@require_capability(MANAGE_ORGS)
def orgs_new():
    return render_template(
        'admin/placeholder.html',
        heading='New Organisation',
        phase='Phase 2',
        detail='Organisation wizard: details, branches, reporting segments, '
               'first administrator.',
    )
