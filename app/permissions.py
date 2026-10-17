"""
Role → capability table for the three-tier admin architecture.

SINGLE SOURCE OF TRUTH for who can do what. Three roles × per-branch scoping is
easy to get subtly wrong, and scattered `current_user.role == '...'` comparisons
drift apart the moment a fourth capability is added. Never inline a role
comparison in a route or template — ask `can(user, CAPABILITY)` instead.

Pure Python, no Flask import, so it stays unit-testable without an app context.
The `require_capability` decorator that wires this into routes lives with the
other gates in app/auth/routes.py.

Tiers
-----
    L1  master_admin   cross-platform: creates orgs and org admins, owns the
                       survey templates and the intervention catalogue, sees
                       all costs, the audit log and the validation report.
                       Read-only on client results.
    L2  org_admin      whole org: creates branches and branch admins, owns the
                       measurement calendar, DEFINES AND FREEZES the reporting
                       segments, deploys org-wide, sees every branch. Templates
                       are VIEW ONLY.
    L3  branch_admin   one branch: creates its own cycles, deploys into them
                       for its own branch, reads its own branch's reports.
                       Templates and segments are VIEW ONLY.

Roles are CUMULATIVE, not exclusive — L2 holds every L3 capability plus
management ones. An org that never creates a branch must not notice the branch
feature exists, and creating a branch admin must never remove a capability from
L2 (permissions that depend on data state are untestable and produce lockouts
where rights are restored by deleting a user).

RESPONDENTS ARE NOT USERS. An employee answering the survey reaches the system
only through the anonymous link and has no account, no role and no row in
`users`. There is deliberately no capability below L3.
"""

MASTER_ADMIN = 'master_admin'
ORG_ADMIN    = 'org_admin'
BRANCH_ADMIN = 'branch_admin'

ROLES = [MASTER_ADMIN, ORG_ADMIN, BRANCH_ADMIN]

ROLE_LABELS = {
    MASTER_ADMIN: 'Super Admin',
    ORG_ADMIN:    'Organisation Admin',
    BRANCH_ADMIN: 'Branch Admin',
}

# Roles an existing role may create. L1 creates org admins; L2 creates branch
# admins; L3 creates nobody.
CREATABLE_ROLES = {
    MASTER_ADMIN: [ORG_ADMIN, BRANCH_ADMIN],
    ORG_ADMIN:    [BRANCH_ADMIN],
    BRANCH_ADMIN: [],
}

# ── Platform administration (L1) ─────────────────────────────────────────────

MANAGE_ORGS        = 'manage_orgs'          # create/edit orgs and their admins
EDIT_TEMPLATES     = 'edit_templates'       # master + per-client survey items
VIEW_TEMPLATES     = 'view_templates'       # read-only template viewer (L2/L3)
VIEW_COSTS         = 'view_costs'           # Usage & Cost Tracker — L1 ONLY
VIEW_AUDIT_LOG     = 'view_audit_log'       # who did what — L1 ONLY

# The intervention catalogue. L1 ONLY, and not because it is sensitive:
# `intervention_catalogue` has no org_id — it is ONE catalogue shared by every
# tenant, so a single edit changes what every client's report offers.
MANAGE_CATALOGUE   = 'manage_catalogue'

# Pilot instrument feedback (plan §6.7, spec §15). L1 ONLY and never rendered
# in a client report: a client who sees it reads it as employee comment, which
# is the thing v1 deliberately excludes.
VIEW_PILOT_FEEDBACK = 'view_pilot_feedback'

# ── Org structure (L1/L2) ────────────────────────────────────────────────────

MANAGE_BRANCHES      = 'manage_branches'       # create/rename/deactivate branches
MANAGE_BRANCH_ADMINS = 'manage_branch_admins'  # create/deactivate L3 users
VIEW_ALL_BRANCHES    = 'view_all_branches'     # see beyond own branch

# ── Measurement work ────────────────────────────────────────────────────────

MANAGE_CYCLES      = 'manage_cycles'       # create measurement cycles
# Define and FREEZE the reporting dimensions (department, function, location,
# tenure band). L2 only: options are a controlled vocabulary set before the
# cycle opens, because two spellings of "Finance" become two groups and neither
# reaches the reporting threshold. L3 views them.
MANAGE_SEGMENTS    = 'manage_segments'
DEPLOY_SURVEYS     = 'deploy_surveys'      # deploy into an existing cycle
DEPLOY_ORG_WIDE    = 'deploy_org_wide'     # deploy with branch_id = NULL (HQ)
# Deploy AT a named branch (branch_id NOT NULL). Deliberately withheld from L2 —
# the one place this table is not cumulative. A deployment is either organisation-wide
# (L2's) or a branch's own (L3's); letting both tiers target the same branch
# produces two concurrent deployments over one population with no way to tell which is
# authoritative, and the response-rate denominator becomes meaningless. L2 keeps
# full VISIBILITY of branch work and every removal right it had — it simply does
# not ORIGINATE a branch-targeted deployment. L1 holds it as operator of last
# resort, notably for a branch with no admin yet.
DEPLOY_TO_BRANCH   = 'deploy_to_branch'
ISSUE_TICKETS      = 'issue_tickets'       # generate opaque single-use tickets
RUN_ANALYSIS       = 'run_analysis'        # generate the narrative for a report
VIEW_REPORTS       = 'view_reports'
MANAGE_ACTIONS     = 'manage_actions'      # improvement actions (plan §3.9, §16)

# ── The capability that exists so it can be granted to NOBODY ───────────────
#
# Lowering a cycle's min_reporting_n below the platform floor
# (config.MIN_REPORTING_N). It is defined, referenced by the route that would
# need it, and held by NO ROLE — not even L1.
#
# That is the whole point. Suppression is the mechanism that makes an anonymous
# survey honest (spec §9.2, §13.5, §13.6), so weakening it must require a CODE
# CHANGE AND A REVIEW rather than an afternoon's administrative decision under
# pressure from a client who wants to see a four-person team's numbers. A
# setting that can be turned down will be turned down.
#
# Do not add this to any role's set. If a client genuinely needs it, that is a
# conversation, a commit and an audit event.
LOWER_MIN_N = 'lower_min_n'

# ── Removal / correction ─────────────────────────────────────────────────────
#
# These say WHO may remove things. Whether a PARTICULAR row may be removed is a
# separate precondition (has it responses? a generated report? a cost event?)
# and is deliberately NOT expressed here: a capability that depends on data
# state is untestable and produces lockouts. The table stays static; row-level
# checks live in the routes.

EDIT_DEPLOYMENT      = 'edit_deployment'       # deadline / expected respondents
ARCHIVE_DEPLOYMENT   = 'archive_deployment'    # soft delete, recoverable
DELETE_DEPLOYMENT    = 'delete_deployment'     # hard delete of an UNUSED deployment
DELETE_ANSWERED      = 'delete_answered'       # destroy collected responses — L1 ONLY
RESTORE_ARCHIVED     = 'restore_archived'      # bring an archived row back — L1 ONLY
DELETE_ORG_OR_BRANCH = 'delete_org_or_branch'  # remove a whole org/branch — L1 ONLY
# Execute the retention purge on a cycle whose retention window has expired
# (spec §13.8). L1 ONLY, confirmed, audited, and never on a timer — there is no
# background worker in this product and a silent purge is worse than a prompt.
PURGE_EXPIRED_CYCLE  = 'purge_expired_cycle'


_CAPABILITIES = {
    MASTER_ADMIN: {
        MANAGE_ORGS, EDIT_TEMPLATES, VIEW_TEMPLATES, VIEW_COSTS, VIEW_AUDIT_LOG,
        MANAGE_CATALOGUE, VIEW_PILOT_FEEDBACK,
        MANAGE_BRANCHES, MANAGE_BRANCH_ADMINS, VIEW_ALL_BRANCHES,
        MANAGE_CYCLES, MANAGE_SEGMENTS, DEPLOY_SURVEYS,
        DEPLOY_ORG_WIDE, DEPLOY_TO_BRANCH, ISSUE_TICKETS,
        RUN_ANALYSIS, VIEW_REPORTS, MANAGE_ACTIONS,
        EDIT_DEPLOYMENT, ARCHIVE_DEPLOYMENT, DELETE_DEPLOYMENT,
        DELETE_ANSWERED, RESTORE_ARCHIVED, DELETE_ORG_OR_BRANCH,
        PURGE_EXPIRED_CYCLE,
        # NOT LOWER_MIN_N. See its definition — it is held by nobody, forever.
    },
    ORG_ADMIN: {
        # Deliberately NOT: MANAGE_ORGS, EDIT_TEMPLATES, VIEW_COSTS,
        # VIEW_AUDIT_LOG, MANAGE_CATALOGUE,
        # VIEW_PILOT_FEEDBACK. Template editing is L1-only; the catalogue is
        # shared by every tenant; the validation and pilot outputs are evidence
        # about the instrument, not about this client.
        VIEW_TEMPLATES,
        MANAGE_BRANCHES, MANAGE_BRANCH_ADMINS, VIEW_ALL_BRANCHES,
        MANAGE_CYCLES, MANAGE_SEGMENTS, ISSUE_TICKETS,
        # Deploys ORG-WIDE ONLY. Not DEPLOY_TO_BRANCH — see its definition.
        DEPLOY_SURVEYS, DEPLOY_ORG_WIDE,
        RUN_ANALYSIS, VIEW_REPORTS, MANAGE_ACTIONS,
        # Not DELETE_ANSWERED / RESTORE_ARCHIVED / DELETE_ORG_OR_BRANCH /
        # PURGE_EXPIRED_CYCLE: destroying collected responses and undoing an
        # archive are L1's. On this product that matters more than on the
        # siblings — anonymous responses cannot be recollected from the people
        # who gave them.
        EDIT_DEPLOYMENT, ARCHIVE_DEPLOYMENT, DELETE_DEPLOYMENT,
    },
    BRANCH_ADMIN: {
        # Runs its own branch end to end: creates its OWN cycles (stamped with
        # its branch_id), deploys into them, reads its own results, commits to
        # its own improvement actions. It never sees the org's cycles, sibling
        # branches, or org-wide deployments. Templates and segments are
        # read-only.
        VIEW_TEMPLATES,
        MANAGE_CYCLES, ISSUE_TICKETS,
        DEPLOY_SURVEYS, DEPLOY_TO_BRANCH,
        RUN_ANALYSIS, VIEW_REPORTS, MANAGE_ACTIONS,
        # Cleans up its own mistakes within its own branch. The branch scoping
        # of the queries — not this table — is what stops it reaching a sibling.
        EDIT_DEPLOYMENT, ARCHIVE_DEPLOYMENT, DELETE_DEPLOYMENT,
    },
}


def capabilities(role: str) -> set:
    """Every capability held by a role. Unknown roles hold nothing."""
    return _CAPABILITIES.get(role, set())


def can(user, capability: str) -> bool:
    """True if `user` holds `capability`. Safe on None/anonymous users."""
    role = getattr(user, 'role', None)
    if not role or not getattr(user, 'is_active', True):
        return False
    return capability in _CAPABILITIES.get(role, set())


def can_hard_delete_others_branch(user) -> bool:
    """
    Whether `user` may HARD delete a row owned by a branch other than their own.

    L2 may archive a branch's deployment (recoverable, audited) but not destroy it:
    oversight should not let one tier irrecoverably remove work a branch
    created. L1 may, as operator of last resort. L3 owns its own rows and never
    reaches another branch at all.
    """
    return getattr(user, 'role', None) == MASTER_ADMIN


def is_branch_scoped(user) -> bool:
    """True if this user may only ever see one branch's data."""
    return getattr(user, 'role', None) == BRANCH_ADMIN


def visible_branch_ids(user):
    """
    Branch filter for a user's queries.

    Returns None when the user may see every branch in their org (L1/L2) —
    callers must treat None as "apply no branch filter", NOT as "no branches".
    Returns a list for a branch-scoped user; an L3 with no branch assigned gets
    an empty list, i.e. sees nothing, which is the safe default.
    """
    if not is_branch_scoped(user):
        return None
    branch_id = getattr(user, 'branch_id', None)
    return [branch_id] if branch_id else []


def can_see_org_wide(user) -> bool:
    """
    Whether org-wide (branch_id IS NULL / HQ) deployments are visible.

    L1 and L2 yes; L3 no — a branch admin must not see deployments that were
    deliberately run above the branch layer.
    """
    return not is_branch_scoped(user)
