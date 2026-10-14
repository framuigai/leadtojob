"""
L2 (org admin) and L3 (branch admin). Phase 1: the dashboard shell plus
navigable placeholders.

BRANCH ISOLATION COMES FROM ROUTE-LEVEL SCOPED QUERIES, NOT FROM RLS.
trainiq's CLAUDE.md is explicit and it applies unchanged here: the app connects
as the table owner and bypasses row-level security entirely. The Supabase
policies exist to shield the anon key and nothing more. Never write a
branch-aware RLS predicate, and never treat a policy as a substitute for
_apply_branch_scope().
"""
from flask import render_template
from flask_login import current_user

from app import domains
from app.auth.routes import require_role_any
from app.org import org_bp
from app.permissions import (
    BRANCH_ADMIN, ORG_ADMIN, is_branch_scoped, visible_branch_ids,
)

_ORG_TIERS = (ORG_ADMIN, BRANCH_ADMIN)


def _apply_branch_scope(query, model):
    """
    Restrict a query to what this user may see.

    `visible_branch_ids` returns None for L1/L2 meaning "apply no filter" —
    callers must not read that as "no branches", which would show an org admin
    nothing. An L3 with no branch assigned gets an empty list and therefore
    sees nothing, which is the safe default.

    L3 also never sees org-wide (branch_id IS NULL) waves: those were run
    deliberately above the branch layer.
    """
    branch_ids = visible_branch_ids(current_user)
    if branch_ids is None:
        return query.filter(model.org_id == current_user.org_id)
    return (query
            .filter(model.org_id == current_user.org_id)
            .filter(model.branch_id.in_(branch_ids)))


@org_bp.route('/')
@require_role_any(*_ORG_TIERS)
def dashboard():
    """
    The org dashboard shell.

    WHAT IS DELIBERATELY NOT AT THE TOP OF THIS PAGE: a single
    "Organisational Satisfaction 3.4 / 5" headline.

    Spec §10.3 forbids the composite — the three Overall Satisfaction
    subdomains "should initially be reported separately. An overall GEM
    composite should only be introduced if validation supports its
    interpretation and the intended use" — and §10.5 forbids the band the
    number would imply. The sibling products both have a figure in this slot
    and it is the first thing a client asks for here.

    The visual slot and its styling are preserved. The metric becomes
    participation plus the three Overall Satisfaction subdomains side by side,
    which is a more honest answer to the same question and takes the same
    amount of room. Phase 7 fills it with real scores.
    """
    return render_template(
        'org/dashboard.html',
        branch_scoped=is_branch_scoped(current_user),
        gem_subdomains=[
            (domains.GEM, code) for code in domains.SUBDOMAINS[domains.GEM]
        ],
    )


# ── Placeholders — navigable now, built in the phase named on each ──────────

@org_bp.route('/cycles')
@require_role_any(*_ORG_TIERS)
def cycles_list():
    return render_template(
        'org/placeholder.html',
        heading='Measurement Cycles',
        phase='Phase 4',
        detail='A cycle is a measurement wave, not a person and not a '
               'position. It carries the purpose statement respondents are '
               'shown, the domains in scope, the reporting threshold and the '
               'retention period.',
    )


@org_bp.route('/segments')
@require_role_any(ORG_ADMIN)        # L3 does not define segments — see below
def segments_list():
    """
    L2 only, by capability AND by role gate.

    Segment options are a controlled vocabulary frozen before a cycle opens. If
    two branches could each add their own, "Finance" and "finance" become two
    groups and neither reaches the reporting threshold — the data is not wrong,
    it is simply gone, and nothing in the system would report that it had been
    lost.
    """
    return render_template(
        'org/placeholder.html',
        heading='Reporting Segments',
        phase='Phase 4',
        detail='Department, function, location, length of service. Defined '
               'and frozen before a wave opens, reported one segment at a '
               'time. Respondents self-declare these; no staff list is ever '
               'imported.',
    )


@org_bp.route('/deployments')
@require_role_any(*_ORG_TIERS)
def deployments_list():
    return render_template(
        'org/placeholder.html',
        heading='Deployments',
        phase='Phase 5',
        detail='One wave, one anonymous link, all four domains. Open-link or '
               'ticketed delivery, each with its own honest statement about '
               'what can and cannot be guaranteed.',
    )


@org_bp.route('/branches')
@require_role_any(ORG_ADMIN)
def branches_list():
    return render_template(
        'org/placeholder.html',
        heading='Branches',
        phase='Phase 2',
        detail='Departments, divisions or sites. One flat level, deliberately '
               'not nestable.',
    )


@org_bp.route('/reports')
@require_role_any(*_ORG_TIERS)
def reports():
    """
    Grouped by cycle, with a "needs: ..." line on anything not yet generatable.

    Three states, not two — this is trainiq's compute_impact_missing pattern
    with one addition:

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
               'not generated, and says so.',
    )
