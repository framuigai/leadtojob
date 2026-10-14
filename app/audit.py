"""
Append-only administrative action log.

ALL audit writes go through record_audit() — never construct an AuditEvent
inline. One funnel means one place where the actor, the payload snapshot and the
commit discipline are decided.

Why the payload snapshot matters: after a HARD delete the target row is gone, so
its foreign key is meaningless and the log entry must stand on its own. Without
it the log says "someone deleted something" and nothing more.

────────────────────────────────────────────────────────────────────────────────
THE PAYLOAD CARRIES ADMINISTRATIVE IDENTIFIERS ONLY. NEVER RESPONSE DATA.

This is the one table in the schema where respondent data could plausibly be
smuggled into a long-lived, rarely-reviewed place — e.g. by snapshotting a
voided session's 48 answers "so the decision can be reviewed", or a purged
cycle's scores "for the record". Both are the natural, well-meaning thing to
write, and both would survive the retention purge that was supposed to remove
them (§13.8), in a table nobody thinks of as holding survey data.

The builders below exist so there is a correct thing to reach for. A session
audit records THAT a session was voided and by whom; it does not record what
that session said. tests/test_privacy_and_wording.py scans audit payloads.
────────────────────────────────────────────────────────────────────────────────
"""
from flask_login import current_user

from app import db
from app.models import AuditEvent

# ── Action names ─────────────────────────────────────────────────────────────
#
# Stable strings, used as filter values on the audit screen. Add to this list
# rather than inventing a string at a call site; a typo creates a category that
# silently never matches a filter.

ORG_CREATE          = 'org.create'
ORG_EDIT            = 'org.edit'
ORG_ARCHIVE         = 'org.archive'

BRANCH_CREATE       = 'branch.create'
BRANCH_EDIT         = 'branch.edit'
BRANCH_DEACTIVATE   = 'branch.deactivate'
BRANCH_REACTIVATE   = 'branch.reactivate'

USER_CREATE         = 'user.create'
USER_DEACTIVATE     = 'user.deactivate'
USER_REACTIVATE     = 'user.reactivate'
USER_PASSWORD_RESET = 'user.password_reset'

CYCLE_CREATE        = 'cycle.create'
CYCLE_EDIT          = 'cycle.edit'
CYCLE_OPEN          = 'cycle.open'
CYCLE_CLOSE         = 'cycle.close'
CYCLE_ARCHIVE       = 'cycle.archive'
CYCLE_DELETE        = 'cycle.delete_hard'
# The retention purge (spec §13.8). Never on a timer; always confirmed, always
# logged. The one action in this product that destroys data which cannot be
# recollected from the people who gave it.
CYCLE_PURGE         = 'cycle.purge_expired'

SEGMENT_CREATE      = 'segment.create'
SEGMENT_EDIT        = 'segment.edit'
# Logged separately from an edit because it is irreversible in effect: after the
# freeze, an option rename would re-group responses given under the old meaning.
SEGMENT_FREEZE      = 'segment.freeze'

DEPLOYMENT_CREATE   = 'deployment.create'
DEPLOYMENT_EDIT     = 'deployment.edit'
DEPLOYMENT_CLOSE    = 'deployment.close'
DEPLOYMENT_ARCHIVE  = 'deployment.archive'
DEPLOYMENT_DELETE   = 'deployment.delete_hard'
DEPLOYMENT_RESTORE  = 'deployment.restore'

TICKETS_ISSUE       = 'tickets.issue'

# Excluding a session from analysis (a test submission, a straight line through
# 48 items in 40 seconds). Logged because it changes a reported response count,
# and a count that can change without a trace is a count nobody can defend.
SESSION_VOID        = 'session.void'
SESSION_UNVOID      = 'session.unvoid'

ACTION_CREATE       = 'improvement_action.create'
ACTION_EDIT         = 'improvement_action.edit'
ACTION_COMPLETE     = 'improvement_action.complete'

CATALOGUE_EDIT      = 'catalogue.edit'

# Narrative generation and report downloads. Logged because they cost money and
# because §13.4 requires role-based access to results — this is the evidence
# that it held. A management report downloaded for a team sits in `report_events`
# with its scope; this log carries the administrative view of the same act.
ANALYSIS_RUN        = 'analysis.run'
REPORT_DOWNLOAD     = 'report.download'


def record_audit(action, target_type, target_id, summary,
                 *, org_id=None, branch_id=None, payload=None, actor=None):
    """
    Append one audit row. THE CALLER COMMITS.

    Deliberately does NOT commit: an audit entry and the action it describes
    must land in the same transaction, or a rolled-back action leaves a log
    claiming it happened. Add this to the session, then commit once alongside
    the real change.

    `actor` defaults to current_user; pass it explicitly from a script or a
    request-less context.
    """
    actor = actor if actor is not None else current_user

    event = AuditEvent(
        org_id=org_id,
        branch_id=branch_id,
        actor_user_id=getattr(actor, 'id', None),
        # Denormalised: the actor's account may later be deactivated or
        # reassigned, and "who did this" must survive that.
        actor_email=getattr(actor, 'email', 'system'),
        actor_role=getattr(actor, 'role', 'system'),
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        summary=summary,
        payload=payload,
    )
    db.session.add(event)
    return event


# ── Payload builders ─────────────────────────────────────────────────────────
#
# Snapshot the identifying fields as they are at the moment of the action, so
# the entry survives a hard delete of the row it describes.
#
# Read the module docstring before adding one. ADMINISTRATIVE FIELDS ONLY.

def branch_payload(branch):
    return {
        'name': branch.name,
        'is_active': branch.is_active,
    }


def cycle_payload(cycle, deployment_count=0, response_count=0):
    """
    Note `response_count` — a COUNT, never the responses. After a purge this is
    the only surviving statement of how much was destroyed, which is exactly
    what a retention record needs and the most it should ever contain.
    """
    return {
        'cycle_id': cycle.id,
        'cycle_name': cycle.cycle_name,
        'branch_id': str(cycle.branch_id) if cycle.branch_id else None,
        'assessed_domains': list(cycle.assessed_domains or []),
        'status': cycle.status,
        'min_reporting_n': cycle.min_reporting_n,
        'scoring_method': cycle.scoring_method,
        'retention_months': cycle.retention_months,
        'is_pilot': cycle.is_pilot,
        'deployments': deployment_count,
        'responses': response_count,
    }


def segment_payload(segment, option_count=0):
    return {
        'segment_key': segment.segment_key,
        'label': segment.label,
        'collect_at_survey': segment.collect_at_survey,
        'options': option_count,
        'frozen_at': segment.frozen_at.isoformat() if segment.frozen_at else None,
    }


def deployment_payload(deployment):
    """
    No respondent-facing secret in here: `public_slug` is omitted deliberately.
    An archived deployment's slug is dead, and a slug in a long-lived log is a
    live URL in anything that log is later pasted into.
    """
    return {
        'cycle_id': deployment.cycle_id,
        'branch_id': str(deployment.branch_id) if deployment.branch_id else None,
        'assessed_domains': list(deployment.assessed_domains or []),
        'delivery_mode': deployment.delivery_mode,
        'expected_respondents': deployment.expected_respondents,
        'responses_received': deployment.responses_received,
        'closed': deployment.closed,
    }


def session_payload(session, answered_count=0):
    """
    Voiding a session. Records the session id, the wave, the declared segment
    KEYS and how many items had been answered — and NOT the answers, and NOT
    the segment VALUES.

    The keys say which dimensions the respondent declared; the values would
    narrow a 200-person wave to a department of six, in a row that outlives the
    retention purge of the responses themselves.
    """
    return {
        'session_id': str(session.id),
        'deployment_id': str(session.deployment_id),
        'submitted': session.submitted_at is not None,
        'declared_segments': sorted((session.segment_values or {}).keys()),
        'answered_items': answered_count,
    }


def action_payload(action):
    return {
        'cycle_id': action.cycle_id,
        'title': action.title,
        'domain_code': action.domain_code,
        'subdomain_code': action.subdomain_code,
        'intervention_type': action.intervention_type,
        'owner_label': action.owner_label,
        'status': action.status,
    }


def user_payload(user):
    return {
        'email': user.email,
        'role': user.role,
        'branch_id': str(user.branch_id) if user.branch_id else None,
        'is_active': user.is_active,
    }
