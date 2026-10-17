"""
The role matrix. Pure Python — no app context, no database.

A capability table is the kind of thing that looks obviously correct and is
quietly wrong, so the non-obvious rules are pinned here rather than left to a
reading of the dict.
"""
from types import SimpleNamespace

from app import permissions as p


def _user(role, **kw):
    return SimpleNamespace(role=role, is_active=True, **kw)


L1 = _user(p.MASTER_ADMIN)
L2 = _user(p.ORG_ADMIN)
L3 = _user(p.BRANCH_ADMIN, branch_id='b1')


# ── Basics ──────────────────────────────────────────────────────────────────

def test_can_is_safe_on_nobody():
    assert p.can(None, p.VIEW_REPORTS) is False
    assert p.can(SimpleNamespace(), p.VIEW_REPORTS) is False


def test_a_deactivated_user_holds_nothing():
    """is_active False means login is denied; it must also mean no capability."""
    frozen = _user(p.MASTER_ADMIN)
    frozen.is_active = False
    assert p.can(frozen, p.MANAGE_ORGS) is False


def test_roles_are_cumulative_for_the_shared_work():
    for cap in (p.VIEW_REPORTS, p.RUN_ANALYSIS, p.MANAGE_CYCLES,
                p.DEPLOY_SURVEYS, p.MANAGE_ACTIONS):
        assert p.can(L1, cap), cap
        assert p.can(L2, cap), cap
        assert p.can(L3, cap), cap


# ── THE CAPABILITY HELD BY NOBODY ───────────────────────────────────────────

def test_lower_min_n_is_held_by_no_role_at_all():
    """
    Suppression is the mechanism that makes an anonymous survey honest
    (spec §9.2, §13.5, §13.6). LOWER_MIN_N exists so that weakening it requires
    a code change and a review, rather than an afternoon's administrative
    decision under pressure from a client who wants to see a four-person team's
    numbers.

    A setting that can be turned down will be turned down. This test is the
    thing that stops someone "fixing" the permission denial by granting it.
    """
    for role in p.ROLES:
        assert p.LOWER_MIN_N not in p.capabilities(role), role
    assert not p.can(L1, p.LOWER_MIN_N)


# ── The one deliberately NON-cumulative rule ────────────────────────────────

def test_l2_deploys_org_wide_and_l3_deploys_to_its_branch():
    """
    L2 does NOT hold DEPLOY_TO_BRANCH. The reasoning is in permissions.py: a
    deployment is either organisation-wide or a branch's own, and letting both tiers
    target the same branch produces two concurrent deployments over one population
    with no way to tell which is authoritative — and a response-rate
    denominator that means nothing.

    L2 keeps full visibility and every removal right it had.
    """
    assert p.can(L2, p.DEPLOY_ORG_WIDE)
    assert not p.can(L2, p.DEPLOY_TO_BRANCH)

    assert p.can(L3, p.DEPLOY_TO_BRANCH)
    assert not p.can(L3, p.DEPLOY_ORG_WIDE)

    # L1 holds both, as operator of last resort — notably for a branch that has
    # no admin yet.
    assert p.can(L1, p.DEPLOY_ORG_WIDE)
    assert p.can(L1, p.DEPLOY_TO_BRANCH)


# ── L1-only ─────────────────────────────────────────────────────────────────

def test_l1_only_capabilities_are_l1_only():
    for cap in (p.MANAGE_ORGS, p.EDIT_TEMPLATES, p.VIEW_COSTS, p.VIEW_AUDIT_LOG,
                p.MANAGE_CATALOGUE,
                p.VIEW_PILOT_FEEDBACK, p.DELETE_ANSWERED, p.RESTORE_ARCHIVED,
                p.DELETE_ORG_OR_BRANCH, p.PURGE_EXPIRED_CYCLE):
        assert p.can(L1, cap), cap
        assert not p.can(L2, cap), cap
        assert not p.can(L3, cap), cap


def test_destroying_collected_responses_is_l1_only():
    """
    Harder here than on the sibling products, and for a reason worth stating:
    an anonymous response cannot be recollected from the person who gave it,
    because the system does not know who they were. There is no "ask them
    again". DELETE_ANSWERED and PURGE_EXPIRED_CYCLE are therefore L1's alone.
    """
    assert p.can(L1, p.DELETE_ANSWERED)
    assert not p.can(L2, p.DELETE_ANSWERED)
    assert not p.can(L3, p.DELETE_ANSWERED)


def test_pilot_feedback_is_not_a_client_deliverable():
    """
    The only free text in the product. A client who reads it will read it as
    employee comment, which is the thing v1 deliberately excludes.
    """
    assert p.can(L1, p.VIEW_PILOT_FEEDBACK)
    assert not p.can(L2, p.VIEW_PILOT_FEEDBACK)


# ── Segments are the org's vocabulary ───────────────────────────────────────

def test_only_l2_and_l1_define_segments():
    """
    If two branches could each add their own options, 'Finance', 'finance' and
    'Fin.' become three groups and none of them reaches the reporting
    threshold. The data is not wrong — it is simply gone, and nothing in the
    system would report that it had been lost.
    """
    assert p.can(L1, p.MANAGE_SEGMENTS)
    assert p.can(L2, p.MANAGE_SEGMENTS)
    assert not p.can(L3, p.MANAGE_SEGMENTS)


# ── Templates ───────────────────────────────────────────────────────────────

def test_templates_are_editable_by_l1_and_viewable_by_the_rest():
    assert p.can(L1, p.EDIT_TEMPLATES)
    assert p.can(L2, p.VIEW_TEMPLATES) and not p.can(L2, p.EDIT_TEMPLATES)
    assert p.can(L3, p.VIEW_TEMPLATES) and not p.can(L3, p.EDIT_TEMPLATES)


# ── Scoping helpers ─────────────────────────────────────────────────────────

def test_visible_branch_ids_returns_none_for_unscoped_users():
    """
    None means "apply no branch filter", NOT "no branches". A caller that reads
    it as the latter shows an org admin nothing — a bug that looks like an empty
    database.
    """
    assert p.visible_branch_ids(L1) is None
    assert p.visible_branch_ids(L2) is None
    assert p.visible_branch_ids(L3) == ['b1']


def test_a_branch_admin_with_no_branch_sees_nothing():
    """The safe default: an empty list, never None."""
    orphan = _user(p.BRANCH_ADMIN, branch_id=None)
    assert p.visible_branch_ids(orphan) == []


def test_only_unscoped_tiers_see_org_wide_deployments():
    assert p.can_see_org_wide(L1)
    assert p.can_see_org_wide(L2)
    assert not p.can_see_org_wide(L3)


def test_only_l1_hard_deletes_another_branchs_work():
    assert p.can_hard_delete_others_branch(L1)
    assert not p.can_hard_delete_others_branch(L2)
    assert not p.can_hard_delete_others_branch(L3)


def test_creatable_roles_do_not_let_a_tier_clone_itself():
    assert p.ORG_ADMIN in p.CREATABLE_ROLES[p.MASTER_ADMIN]
    assert p.ORG_ADMIN not in p.CREATABLE_ROLES[p.ORG_ADMIN]
    assert p.CREATABLE_ROLES[p.BRANCH_ADMIN] == []
