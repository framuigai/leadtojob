"""
Cloning the master instrument into a client org, and the default segments.

WHY CLONE AT ALL, rather than every org reading the master directly?

Because the instrument a client's respondents answered is EVIDENCE. Once a cycle
has run, "what did question GEM-G-01 say?" has exactly one correct answer: the
text that was on the screen. A shared master makes that text mutable
retrospectively — an edit for client B silently rewrites what client A's 300
respondents were answering, months after the fact, with nothing in the data to
show it happened.

So each org gets its own frozen copy at provisioning time. The cost is 48 rows
per client. The alternative costs the ability to say what was asked.

Pure-ish: takes an org_id, uses db.session, DOES NOT COMMIT. The caller owns the
transaction, so a clone and the org row it belongs to land together or not at
all.
"""
from sqlalchemy import func

from app import db
from app.domains import (
    DOMAINS, MASTER_ORG_ID, SUGGESTED_SEGMENT_KEYS,
)
from app.models import Segment, SegmentOption, SurveyItem


class ProvisioningError(Exception):
    pass


def latest_version(org_id):
    """
    The highest instrument version an org holds, or 1 when it holds none.

    Editing the master never rewrites an item in place: a save writes a whole
    new version and the old one stays readable, because a cycle that has run
    must still be able to answer "what did this question say?" exactly. Every
    reader therefore asks for the latest version rather than assuming 1.
    """
    return (db.session.query(func.max(SurveyItem.version))
            .filter_by(org_id=org_id).scalar() or 1)


def clone_instrument_to_org(org_id, version=None):
    """
    Copy the 48 master items into `org_id`. Returns the number cloned.

    Refuses if the org already holds items at this version — re-cloning is
    `seed_templates.py --reclone-org`, which checks for responses first.
    """
    if org_id == MASTER_ORG_ID:
        raise ProvisioningError('refusing to clone the master into itself')

    # Default to whatever the master currently holds, so an org provisioned
    # after an edit receives the edited instrument rather than version 1.
    if version is None:
        version = latest_version(MASTER_ORG_ID)

    existing = (db.session.query(SurveyItem)
                .filter_by(org_id=org_id, version=version).count())
    if existing:
        raise ProvisioningError(
            f'org {org_id} already holds {existing} items at version {version}. '
            'Use seed_templates.py --reclone-org, which refuses once any '
            'response points at them.'
        )

    master = (db.session.query(SurveyItem)
              .filter_by(org_id=MASTER_ORG_ID, version=version)
              .order_by(SurveyItem.sort_order).all())
    if not master:
        raise ProvisioningError(
            'the master template is empty. Run `python seed_templates.py` '
            'before provisioning a client org — an org cloned from an empty '
            'master gets a survey with no questions, and the failure surfaces '
            'only when someone opens the link.'
        )
    if len(master) != 48:
        raise ProvisioningError(
            f'the master holds {len(master)} items, expected 48. Refusing to '
            'clone a partial template: specification §4 requires 4 domains x '
            '3 subdomains x 4 questions, and a short clone produces scores '
            'that look valid and are not.'
        )

    for item in master:
        db.session.add(SurveyItem(
            org_id=org_id,
            version=item.version,
            domain_code=item.domain_code,
            subdomain_code=item.subdomain_code,
            item_code=item.item_code,
            ordinal=item.ordinal,
            sort_order=item.sort_order,
            item_text=item.item_text,
            anchor_type=item.anchor_type,
            allows_na=item.allows_na,
            is_reverse_scored=item.is_reverse_scored,
            is_active=item.is_active,
        ))
    return len(master)


def create_default_segments(org_id):
    """
    Seed the four suggested reporting dimensions, INACTIVE and with no options.

    Deliberately empty of options: §3.2.4 wants patterns across "departments,
    functions, locations and other appropriate organizational groups", and only
    the client knows what theirs are called. A helpfully pre-filled list of
    plausible department names would be wrong for every client and would be
    accepted by at least one of them.

    Inactive until the admin fills in options, because a segment the form asks
    about but has no options for is a dropdown with nothing in it.
    """
    created = []
    for sort_order, (key, label) in enumerate(SUGGESTED_SEGMENT_KEYS, start=1):
        if db.session.query(Segment).filter_by(org_id=org_id, segment_key=key).first():
            continue
        segment = Segment(
            org_id=org_id, segment_key=key, label=label,
            collect_at_survey=True, is_active=False, sort_order=sort_order,
        )
        db.session.add(segment)
        created.append(segment)
    return created


def freeze_segments(org_id):
    """
    Stamp every active segment as frozen. Called on the FIRST RESPONSE of the
    first cycle that uses them.

    After this, option codes and labels are read-only. Renaming an option
    mid-cycle silently re-groups responses that were given under the old
    meaning — and because the response rows themselves are unchanged, nothing
    in the data would ever reveal it.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    frozen = []
    segments = (db.session.query(Segment)
                .filter_by(org_id=org_id, is_active=True)
                .filter(Segment.frozen_at.is_(None)).all())
    for segment in segments:
        segment.frozen_at = now
        frozen.append(segment)
    return frozen


def active_segments(org_id):
    """Active segments with at least one active option, in display order."""
    segments = (db.session.query(Segment)
                .filter_by(org_id=org_id, is_active=True)
                .order_by(Segment.sort_order, Segment.label).all())
    return [s for s in segments if any(o.is_active for o in s.options)]


def segment_option_codes(segment):
    return {o.option_code for o in segment.options if o.is_active}


def validate_segment_values(org_id, values):
    """
    Check self-declared segment answers against the controlled vocabulary.

    Returns the subset that is valid. UNKNOWN KEYS AND UNKNOWN CODES ARE
    DROPPED, NOT REJECTED: a respondent whose department was renamed between
    the cycle opening and their answering must not lose their 48 answers over
    it. The segment is simply absent for them, and they fall into the
    "unspecified" group, which the report shows as its own row rather than
    silently folding into another.
    """
    if not values:
        return {}
    allowed = {s.segment_key: segment_option_codes(s) for s in active_segments(org_id)}
    return {
        key: code for key, code in values.items()
        if key in allowed and code in allowed[key]
    }


def default_active_domains():
    """All four. A satisfaction diagnostic without the outcome domain is not one."""
    return list(DOMAINS)
