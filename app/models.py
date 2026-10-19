"""
ALL SQLAlchemy models. ONE FILE RULE — never split these across modules.

THE ONE THING TO UNDERSTAND BEFORE CHANGING ANYTHING HERE
---------------------------------------------------------
This schema is *anonymous but not aggregate-only*, and that is the whole
architectural trick of the product.

  * `response_sessions` holds NO identity and NO foreign key to any identity —
    no email, no employee number, no IP, no user-agent, no FK to `users`. There
    is no screen, export or API that can return "who said this", because the
    information was never collected (spec §9.2, §13.2, §13.9).

  * `survey_responses` nevertheless carries `session_id`, so the answers of ONE
    respondent stay grouped. That is what makes spec §10.4 possible — the
    correlation of a respondent's own TID score with their own GEM score.

Both halves are mandatory and they pull in opposite directions:

    an aggregate-only design (storing per-item means and discarding rows)
      -> makes §10.4 impossible;
    an identified design (promo's `assessment_invitations`)
      -> makes §13 impossible, and destroys the candour the instrument depends
         on in the first place.

Rows keyed to an anonymous session is the only shape that satisfies both. If a
change here would add an identity column to `response_sessions`, or would strip
`session_id` from `survey_responses`, it is breaking one of the two guarantees —
stop and re-read spec §10.4 and §13.

How this differs from the siblings, in one line each:

  * trainiq's unit of analysis is (org|branch, domain, cycle, assessment_type)
    over an anonymous pooled population — close to this product, but it has no
    per-respondent grouping, so it could not do §10.4.
  * promo's unit is (position, candidate, instrument) over IDENTIFIED assessors.
    Every attribution pattern in that schema is wrong here.
"""
import uuid
from datetime import datetime, timezone

import bcrypt
from flask_login import UserMixin
from sqlalchemy import (
    Boolean, CheckConstraint, Column, Computed, DateTime, Float, ForeignKey,
    Index, Integer, Numeric, SmallInteger, String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship

from app import db

# Folded onto this in COALESCE() inside the partial unique indexes below.
# Postgres treats NULLs as distinct in a unique index, so a bare nullable
# branch_id would silently stop blocking duplicate ORG-WIDE rows.
_NIL_UUID = '00000000-0000-0000-0000-000000000000'


def _utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Orgs
# ---------------------------------------------------------------------------

class Org(db.Model):
    __tablename__ = 'orgs'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    industry = Column(String(100))
    logo_url = Column(String(500))
    brand_colour = Column(String(6))         # hex without '#', e.g. '2E75B6'
    # The domains this client measures by default. All four is the normal case
    # and the recommended one — a satisfaction diagnostic without GEM is not a
    # diagnostic — but a client may legitimately run a shorter pulse cycle. A
    # new cycle inherits this set and may narrow it further; it can never
    # widen beyond it.
    active_domains = Column(ARRAY(String))

    # THE ORG'S STANDING ANSWER TO §9.2, WRITTEN ONCE INSTEAD OF EVERY CYCLE.
    #
    # `measurement_cycles.purpose_statement` stays NOT NULL — the guarantee is
    # still a property of the schema and every cycle still carries its own
    # text. This column only supplies the DEFAULT that a new cycle is created
    # with, so an L2 admin writes the statement once and an L3 admin creating a
    # branch cycle inherits it rather than inventing a worse one under time
    # pressure. Either may edit a cycle's own copy afterwards; editing it
    # changes that cycle only, never this default or another cycle.
    #
    # Nullable because an org exists before anyone has written it. Blank is the
    # prompt state, not a valid operating state: the L2 dashboard carries a
    # reminder until it is filled, and cycle creation refuses while it is
    # empty — which is the same §9.2 check as before, moved one screen earlier.
    default_purpose_statement = Column(Text)

    # False while the creation wizard is in progress. The org exists as a
    # resumable DRAFT from step 1, rather than the wizard holding its state in
    # the session — an abandoned wizard then loses nothing, and org_detail
    # offers "Resume setup".
    setup_complete = Column(Boolean, nullable=False, default=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    users = relationship('User', back_populates='org')
    branches = relationship('Branch', back_populates='org')
    cycles = relationship('MeasurementCycle', back_populates='org')
    segments = relationship('Segment', back_populates='org')


# ---------------------------------------------------------------------------
# Branches  (optional subdivision of an org — depts / divisions / sites)
# ---------------------------------------------------------------------------

class Branch(db.Model):
    """
    One flat level below Org. Deliberately NOT nestable and with no parent
    branch: real orgs are matrices (region x function) and someone will ask for
    "Sales across all branches", but nesting makes every scoped query recursive
    for no benefit the flat model doesn't already cover. Cross-cutting views
    are what `segments` are for.

    Branches are optional. A deployment with branch_id = NULL is org-wide (HQ).
    """
    __tablename__ = 'branches'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey('orgs.id'), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    # Soft delete only. Historical cycles keep pointing at this row, so a
    # branch is never hard-deleted and a branch_id is never reassigned.
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    org = relationship('Org', back_populates='branches')
    users = relationship('User', back_populates='branch')
    cycles = relationship('MeasurementCycle', back_populates='branch')


# ---------------------------------------------------------------------------
# Users  (admins only — RESPONDENTS ARE NEVER USERS)
# ---------------------------------------------------------------------------

class User(UserMixin, db.Model):
    """
    Admin accounts only.

    An employee answering the survey reaches the system through the anonymous
    link and has no account. This is not a feature that was deferred: giving a
    respondent an account would create exactly the identity link the product
    exists to refuse.
    """
    __tablename__ = 'users'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey('orgs.id'), nullable=True)  # NULL for master_admin
    # Set only for branch_admin (L3); NULL for master_admin and org_admin, both
    # of which see the whole scope above a branch.
    branch_id = Column(UUID(as_uuid=True), ForeignKey('branches.id'), nullable=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    # master_admin | org_admin | branch_admin — app/permissions.py ROLES is the
    # single source of truth for what each one may do.
    role = Column(String(20), nullable=False)
    full_name = Column(String(255))
    is_active = Column(Boolean, nullable=False, default=True)  # False = login denied
    last_login_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    org = relationship('Org', back_populates='users')
    branch = relationship('Branch', back_populates='users')

    def set_password(self, password: str) -> None:
        self.password_hash = bcrypt.hashpw(
            password.encode('utf-8'), bcrypt.gensalt(rounds=12)
        ).decode('utf-8')

    def check_password(self, password: str) -> bool:
        return bcrypt.checkpw(
            password.encode('utf-8'), self.password_hash.encode('utf-8')
        )

    def get_id(self) -> str:
        return str(self.id)


# ---------------------------------------------------------------------------
# Measurement Cycles  — ONE MEASUREMENT ROUND
# ---------------------------------------------------------------------------

class MeasurementCycle(db.Model):
    """
    A cycle: "Employee Satisfaction Survey 2026 H1".

    NOT a person and NOT a position — the sibling products' cycles are both.
    This is what spec §3.2.6 ("establish a baseline for subsequent
    measurement"), §3.2.7 ("track changes in employee perceptions over time")
    and the whole of §16 rest on.
    """
    __tablename__ = 'measurement_cycles'

    # A human-meaningful string PK ('JS-2026-H1'), as in both siblings: it
    # appears in report filenames and in conversation, and a UUID there is
    # unreadable.
    id = Column(String(50), primary_key=True)
    org_id = Column(UUID(as_uuid=True), ForeignKey('orgs.id'), nullable=False, index=True)
    # NULL = org-wide. A branch-owned cycle is stamped with its branch_id.
    branch_id = Column(UUID(as_uuid=True), ForeignKey('branches.id'), nullable=True, index=True)

    cycle_name = Column(String(255), nullable=False)

    # NOT NULL FOR A REASON (spec §9.2). Before the first question, the
    # respondent must be told the purpose, how results will be used, who sees
    # what, the reporting threshold, and the non-retaliation commitment. The org
    # admin writes that statement when the cycle is created, and the survey
    # landing page renders it. A nullable column here would mean the survey can
    # open with nothing to say — which is exactly the survey whose answers are
    # not worth having.
    purpose_statement = Column(Text, nullable=False)

    # Defaults to all four. Frozen on the first response (see §4.3 in the plan):
    # two cycles with different domain sets are not comparable, and the trend
    # section refuses to trend a domain absent from either cycle rather than
    # plotting a gap as a drop.
    assessed_domains = Column(ARRAY(String))

    # Baseline linkage. Carried from day one even though trend reporting ships
    # in phase 9 — promo learned that retrofitting this link after real data
    # exists is far harder than carrying an unused column.
    previous_cycle_id = Column(String(50), ForeignKey('measurement_cycles.id'), nullable=True)

    status = Column(String(10), nullable=False, default='draft')   # draft|open|closed
    opened_at = Column(DateTime(timezone=True))
    closes_at = Column(DateTime(timezone=True))
    closed_at = Column(DateTime(timezone=True))

    # The suppression floor for this cycle. May be raised above the platform
    # default (config.MIN_REPORTING_N) and NEVER lowered below it — the check
    # lives in app/analytics/ and the LOWER_MIN_N capability is held by nobody.
    # 1 EVERYWHERE, ON REQUEST — see config.MIN_REPORTING_N for what that
    # means. At 1 nothing is ever suppressed, so a one-person group reports
    # that person's own answers. A cycle created before this change keeps the
    # value it was created with, and no screen can lower it.
    min_reporting_n = Column(Integer, nullable=False, default=1)

    # Which of the two documented domain-scoring methods is in force
    # (spec §10.2: the organisation must document which it uses, because
    # mean-of-items and mean-of-subdomains differ when completeness varies).
    # FROZEN per cycle: changing it mid-stream silently changes everyone's
    # numbers and makes two cycles incomparable.
    scoring_method = Column(String(24), nullable=False, default='mean_of_subdomains')

    # Retention, spec §13.8. NOT NULL, bounded 3-120 by the check below.
    #
    # A nullable column would mean "not set", which in practice is INDEFINITE —
    # the state §13.8 forbids — and an unbounded integer is how "indefinitely"
    # gets entered as 9999. The clock starts when the cycle CLOSES, not when it
    # opens: an open cycle is never overdue however long it has been open.
    #
    # NOTHING DELETES ON A TIMER. There is no background worker in this product
    # and a silent purge of anonymous responses that cannot be recollected is
    # worse than a prompt. An overdue cycle surfaces as a warning on the L1
    # board with a confirmed, audited purge behind PURGE_EXPIRED_CYCLE.
    retention_months = Column(Integer, nullable=False, default=24)

    # A pilot cycle (spec §15). Turns on the optional instrument-feedback block
    # at the end of the form, whose answers are L1-only and never reach a
    # client report. See PilotFeedback.
    is_pilot = Column(Boolean, nullable=False, default=False)

    created_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    # Soft delete. A cycle may be HARD deleted only while it holds no
    # deployments; once it has held any, it is archived so its reports and cost
    # events keep their parent.
    archived_at = Column(DateTime(timezone=True), index=True)
    archived_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)

    __table_args__ = (
        CheckConstraint(
            'retention_months >= 3 AND retention_months <= 120',
            name='ck_retention_bounds',
        ),
        CheckConstraint(
            "status IN ('draft', 'open', 'closed')",
            name='ck_cycle_status',
        ),
        CheckConstraint(
            "scoring_method IN ('mean_of_subdomains', 'mean_of_items')",
            name='ck_scoring_method',
        ),
    )

    org = relationship('Org', back_populates='cycles')
    branch = relationship('Branch', back_populates='cycles')
    deployments = relationship('SurveyDeployment', back_populates='cycle')
    previous_cycle = relationship('MeasurementCycle', remote_side=[id])
    creator = relationship('User', foreign_keys=[created_by])


# ---------------------------------------------------------------------------
# Segments  — the reporting dimensions (spec §3.2.4)
# ---------------------------------------------------------------------------

class Segment(db.Model):
    """
    "Patterns across departments, functions, locations and other appropriate
    organizational groups" is a stated objective, so this is a first-class
    table rather than a hardcoded enum.

    TWO SHARP RULES, BOTH PRIVACY:

    1. A segment with few options is fine; a COMBINATION of segments is not.
       Cross-tabs (department x tenure) re-identify fast — 12 departments x 4
       tenure bands over 200 staff averages 4 people a cell. v1 reports ONE
       segment at a time. A cross-tab is an L1-gated, min-N-enforced,
       explicitly-logged action, or it is phase 12.

    2. FREE TEXT IS NEVER A SEGMENT. Options are a controlled vocabulary set by
       the org admin before the cycle opens and frozen on the first response.
       Otherwise "Finance", "finance" and "Fin." become three groups, and none
       of them reaches the reporting threshold — the data is not wrong, it is
       simply gone.
    """
    __tablename__ = 'segments'
    __table_args__ = (
        UniqueConstraint('org_id', 'segment_key', name='uq_segment_key_per_org'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey('orgs.id'), nullable=False, index=True)
    # 'department' | 'function' | 'location' | 'tenure_band' | custom.
    # See domains.SUGGESTED_SEGMENT_KEYS — a default, not a limit.
    segment_key = Column(String(50), nullable=False)
    label = Column(String(255), nullable=False)
    # Whether the survey form asks the respondent to self-declare this.
    # A segment the form does not collect cannot be reported on, which is the
    # honest behaviour: the platform never ingests an HR staff list to fill it
    # in (§13.2 data minimisation).
    collect_at_survey = Column(Boolean, nullable=False, default=True)
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    # Set on the first response of the first cycle that uses it. After this,
    # options are read-only: renaming an option mid-cycle reassigns responses
    # that were given under the old meaning.
    frozen_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    org = relationship('Org', back_populates='segments')
    options = relationship('SegmentOption', back_populates='segment')


class SegmentOption(db.Model):
    __tablename__ = 'segment_options'
    __table_args__ = (
        UniqueConstraint('segment_id', 'option_code', name='uq_option_code_per_segment'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    segment_id = Column(UUID(as_uuid=True), ForeignKey('segments.id'), nullable=False, index=True)
    # The STORED value. `response_sessions.segment_values` holds this code, not
    # the label, so a later rename of the label cannot re-group historical
    # responses.
    option_code = Column(String(50), nullable=False)
    option_label = Column(String(255), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)

    segment = relationship('Segment', back_populates='options')


# ---------------------------------------------------------------------------
# Survey Deployments  — ONE CYCLE, ONE AUDIENCE, ONE LINK, ALL FOUR DOMAINS
# ---------------------------------------------------------------------------

class SurveyDeployment(db.Model):
    """
    ONE DEPLOYMENT = ONE CYCLE FOR ONE AUDIENCE, COVERING THE ASSESSED DOMAINS,
    REACHED THROUGH ONE ANONYMOUS LINK.

    In trainiq a deployment IS a domain, so four domains mean four links —
    because trainiq's domains are independently licensed modules bought à la
    carte. Here the four domains are ONE INSTRUMENT: a satisfaction diagnostic
    without GEM is not a diagnostic, since GEM is the outcome everything else is
    reported against. So domain is an attribute of the QUESTION, not of the
    deployment, and 48 items are reachable through a single link. That removes
    the survey-fatigue problem rather than mitigating it.
    """
    __tablename__ = 'survey_deployments'
    __table_args__ = (
        # Hard-block two concurrent open deployments over the same population.
        # Declared here (not as raw SQL in a migration) so Alembic autogenerate
        # recognises it and doesn't propose dropping it. And declared in the
        # DATABASE, not merely checked in the route, because two admins
        # pressing Deploy is a race the route cannot win.
        #
        # branch_id is wrapped in COALESCE, not used raw: Postgres treats NULLs
        # as distinct in a unique index, so a bare `branch_id` column would
        # quietly stop blocking duplicate ORG-WIDE (branch_id IS NULL) rows.
        # Folding NULL onto the nil UUID keeps org-wide deployments mutually
        # exclusive while leaving real branches independent. Do not "simplify"
        # this back to a bare column.
        Index(
            'uq_open_deployment',
            'org_id', 'cycle_id',
            text(f"COALESCE(branch_id, '{_NIL_UUID}'::uuid)"),
            unique=True,
            postgresql_where=text('closed = false'),
        ),
        CheckConstraint(
            "delivery_mode IN ('open_link', 'ticketed')",
            name='ck_delivery_mode',
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey('orgs.id'), nullable=False, index=True)
    # NULL = org-wide / HQ deployment. Branch admins never see these.
    branch_id = Column(UUID(as_uuid=True), ForeignKey('branches.id'), nullable=True, index=True)
    cycle_id = Column(String(50), ForeignKey('measurement_cycles.id'), nullable=False, index=True)

    # Snapshot of the cycle's assessed_domains at deploy time, so narrowing the
    # cycle later cannot retroactively change what was asked.
    assessed_domains = Column(ARRAY(String))
    instrument_version = Column(Integer, nullable=False, default=1)

    # THE ONE ANONYMOUS LINK. secrets.token_urlsafe(32).
    public_slug = Column(String(64), unique=True, nullable=False, index=True)

    # open_link | ticketed — see AccessTicket, and note that the two modes make
    # DIFFERENT HONEST CLAIMS about duplicate submissions. The landing page
    # wording is selected from this field, and a test asserts the claim matches
    # the mode (spec §9.2: "avoid promising absolute anonymity if the
    # administration method cannot guarantee it").
    delivery_mode = Column(String(16), nullable=False, default='open_link')

    # Headcount of the invited population — the DENOMINATOR of the response
    # rate, and nothing else. It is a number typed by an admin, not a list of
    # people; there is no roster anywhere in this schema.
    expected_respondents = Column(Integer, nullable=False, default=0)
    responses_received = Column(Integer, nullable=False, default=0)
    response_rate_pct = Column(
        Float,
        Computed(
            'CASE WHEN expected_respondents > 0 '
            'THEN (responses_received::float / expected_respondents) * 100 '
            'ELSE 0 END',
            persisted=True,
        ),
    )

    created_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    deployed_at = Column(DateTime(timezone=True))
    closes_at = Column(DateTime(timezone=True))
    closed = Column(Boolean, nullable=False, default=False)

    # Soft delete. Archiving ALSO sets closed=True: it kills the public link and
    # takes the row out of uq_open_deployment, so the deployment can be remade.
    # Only L1 may restore one.
    archived_at = Column(DateTime(timezone=True), index=True)
    archived_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)

    cycle = relationship('MeasurementCycle', back_populates='deployments')
    sessions = relationship('ResponseSession', back_populates='deployment')
    responses = relationship('SurveyResponse', back_populates='deployment')
    tickets = relationship('AccessTicket', back_populates='deployment')
    creator = relationship('User', foreign_keys=[created_by])


# ---------------------------------------------------------------------------
# Access Tickets  — single-submission control WITHOUT identity
# ---------------------------------------------------------------------------

class AccessTicket(db.Model):
    """
    `ticketed` mode. HR generates N opaque tickets and hands them out; the app
    stores only sha256(ticket) and used_at, and NEVER WHO RECEIVED WHICH.

    Honest claim: "One response per ticket. We do not know which ticket is
    yours." Both halves are true, and together they are the strongest
    combination of integrity and anonymity available without a trusted third
    party.

    DO NOT resurrect trainiq's dropped `respondent_tokens` columns — email_hash,
    used, expires_at. An `email_hash` is a re-identification vector with a
    small, enumerable input space: anyone holding the staff list can hash all
    400 addresses and match. It is precisely the thing this design refuses, and
    it is tempting because it looks like anonymisation.
    """
    __tablename__ = 'access_tickets'
    __table_args__ = (
        Index('uq_ticket_hash', 'deployment_id', 'ticket_hash', unique=True),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deployment_id = Column(
        UUID(as_uuid=True), ForeignKey('survey_deployments.id'),
        nullable=False, index=True,
    )
    org_id = Column(UUID(as_uuid=True), ForeignKey('orgs.id'), nullable=False, index=True)
    # sha256 of the ticket string, hex. The plaintext exists only on the slip of
    # paper handed to the employee — the app cannot reproduce it, by design.
    ticket_hash = Column(String(64), nullable=False)
    used_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    deployment = relationship('SurveyDeployment', back_populates='tickets')


# ---------------------------------------------------------------------------
# Response Sessions  — ANONYMITY WITH INTEGRITY
# ---------------------------------------------------------------------------

class ResponseSession(db.Model):
    """
    THE TABLE THAT MAKES THIS PRODUCT CORRECT. It holds no identity and no
    foreign key to any identity.

    What is deliberately absent, and must stay absent: email, employee number,
    name, IP ADDRESS, user-agent, device id, any FK to `users` or to an
    invitation. Adding any of them re-creates the attribution the product
    exists to refuse, and no permission check can undo that — the guarantee is
    a property of the SCHEMA, which is the only form of it worth making
    (§13.9, §17.5).

    A session is created on the first GET of the survey link and carries the
    partial answers, so save-and-resume works WITHOUT KNOWING WHO THE PERSON
    IS: the browser holds an opaque `resume_secret`, the server stores only its
    hash. Lose the browser, lose the draft. That is the correct trade.

    ON IP ADDRESSES, HONESTLY: the web server and Render's edge DO log them.
    That is outside this application and outside this table, and the privacy
    notice says so rather than promising absolute anonymity — which spec §9.2
    explicitly warns against ("avoid promising absolute anonymity if the
    administration method cannot guarantee it").
    """
    __tablename__ = 'response_sessions'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deployment_id = Column(
        UUID(as_uuid=True), ForeignKey('survey_deployments.id'),
        nullable=False, index=True,
    )
    org_id = Column(UUID(as_uuid=True), ForeignKey('orgs.id'), nullable=False, index=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey('branches.id'), nullable=True, index=True)

    # SELF-DECLARED at the start of the form: {'department': 'FIN',
    # 'tenure_band': '1_3'}. Codes from SegmentOption.option_code, never
    # labels, and never looked up from an HR file — the platform has no staff
    # list to look anything up in.
    #
    # A segment combination so rare that it identifies someone is handled by
    # SUPPRESSION AT REPORT TIME, not by refusing the response. Refusing it
    # would both lose the data and tell the respondent they are identifiable.
    segment_values = Column(JSONB)

    started_at = Column(DateTime(timezone=True), default=_utcnow)
    # NULL until final submit. Only a submitted session counts toward
    # responses_received and toward any reported figure.
    submitted_at = Column(DateTime(timezone=True), index=True)
    last_seen_at = Column(DateTime(timezone=True), default=_utcnow)

    # sha256 of the opaque resume secret held in the respondent's browser.
    resume_secret_hash = Column(String(64))

    # Set by an admin to exclude a session from analysis (a test submission, an
    # obvious straight-line through 48 items in 40 seconds). Kept rather than
    # deleted so the exclusion is visible and reversible, and so the response
    # count cannot quietly change without a trace.
    is_void = Column(Boolean, nullable=False, default=False)

    deployment = relationship('SurveyDeployment', back_populates='sessions')
    responses = relationship('SurveyResponse', back_populates='session')


# ---------------------------------------------------------------------------
# Survey Items  — the instrument
# ---------------------------------------------------------------------------

class SurveyItem(db.Model):
    """
    48 items: 4 domains x 3 subdomains x 4 items (spec §4).

    Org-scoped, versioned, clonable from the master org — trainiq's
    SurveyQuestion pattern, which already does what is needed: seed once, clone
    per client, freeze on first clone.

    Group and join on domain_code / subdomain_code / item_code — NEVER on a
    human-readable label. In trainiq `sub_module` is simultaneously the display
    string, the grouping key AND the course-catalogue join key, so a rename
    there silently kills recommendations. The pair (domain_code,
    subdomain_code) is the key here, always both: subdomain letters repeat
    across domains.

    FOUR ITEMS PER SUBDOMAIN IS A MATERIALLY BETTER INSTRUMENT THAN PROMO'S
    TWO. A subdomain score rests on 4 items and is a legitimate reporting unit,
    so there is no "limited evidence" flag to carry over and the subdomain can
    be reported freely. The domain is the summary, not the floor.
    """
    __tablename__ = 'survey_items'
    __table_args__ = (
        Index('ix_items_lookup', 'org_id', 'version', 'domain_code', 'subdomain_code'),
        Index('uq_item_identity', 'org_id', 'version', 'item_code', unique=True),
        CheckConstraint('ordinal BETWEEN 1 AND 4', name='ck_item_ordinal'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey('orgs.id'), nullable=False)
    version = Column(Integer, nullable=False, default=1)

    domain_code = Column(String(3), nullable=False)       # GEM|TID|BIT|PIE
    subdomain_code = Column(String(1), nullable=False)    # G|E|M|T|I|D|B|P
    item_code = Column(String(20), nullable=False)        # 'GEM-G-01'
    ordinal = Column(SmallInteger, nullable=False)        # 1..4 within the cell
    # Presentation order across the whole instrument. Within a domain the three
    # subdomains are INTERLEAVED rather than blocked — it reduces
    # straight-lining, and nothing on the form names a subdomain anyway.
    sort_order = Column(Integer, nullable=False, default=0)

    item_text = Column(Text, nullable=False)
    anchor_type = Column(String(20), nullable=False, default='agreement')

    # Spec §9.1 offers N/A "where a question genuinely does not apply or the
    # respondent lacks sufficient information". Per-item rather than global so
    # the instrument can be tuned after the pilot without a migration.
    allows_na = Column(Boolean, nullable=False, default=True)

    # Honoured in scoring FROM DAY ONE (as 6 - score) even though none of the
    # 48 supplied items is currently reverse-worded. Pilot validation (§15) is
    # the most likely reason to introduce one, and retrofitting a reverse flag
    # through a scoring ladder after real data exists is the kind of change
    # that silently flips a subdomain for every historical cycle.
    is_reverse_scored = Column(Boolean, nullable=False, default=False)

    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    responses = relationship('SurveyResponse', back_populates='item')


# ---------------------------------------------------------------------------
# Survey Responses
# ---------------------------------------------------------------------------

class SurveyResponse(db.Model):
    """
    One answer, keyed to an ANONYMOUS session.

    `session_id` is what makes within-respondent analysis possible — spec
    §10.4's correlation of a respondent's own diagnostic scores with their own
    GEM scores — while `response_sessions` holds no identity. See this module's
    docstring: that pairing is the architecture.

    `score` is NULLABLE and `not_applicable` is a FIRST-CLASS STATE, never a
    sentinel. Spec §9.1 is explicit that N/A responses "should not be treated as
    neutral scores". Never encode it as 0, 3 or 6 — a sentinel reaches an AVG()
    eventually and corrupts a result silently, weeks before anyone notices the
    number is wrong. N/A rows are excluded from BOTH numerator and denominator
    at every level, and are counted into the Coverage section.
    """
    __tablename__ = 'survey_responses'
    __table_args__ = (
        CheckConstraint(
            '(not_applicable = true AND score IS NULL) OR '
            '(not_applicable = false AND score BETWEEN 1 AND 5)',
            name='ck_score_or_not_applicable',
        ),
        # One answer per item per session. Save-and-resume UPDATES this row
        # rather than inserting a second one.
        Index('uq_response_identity', 'session_id', 'item_id', unique=True),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True), ForeignKey('response_sessions.id'),
        nullable=False, index=True,
    )
    deployment_id = Column(
        UUID(as_uuid=True), ForeignKey('survey_deployments.id'),
        nullable=False, index=True,
    )
    org_id = Column(UUID(as_uuid=True), ForeignKey('orgs.id'), nullable=False, index=True)
    item_id = Column(UUID(as_uuid=True), ForeignKey('survey_items.id'), nullable=False)

    score = Column(SmallInteger, nullable=True)
    not_applicable = Column(Boolean, nullable=False, default=False)

    answered_at = Column(DateTime(timezone=True), default=_utcnow)

    session = relationship('ResponseSession', back_populates='responses')
    deployment = relationship('SurveyDeployment', back_populates='responses')
    item = relationship('SurveyItem', back_populates='responses')


# ---------------------------------------------------------------------------
# Intervention Catalogue  — the curriculum (content arrives later)
# ---------------------------------------------------------------------------

class InterventionCatalogue(db.Model):
    """
    Reference data, NOT tenant data: ONE catalogue shared by every org, so
    there is no org_id and editing is L1-only — a single edit changes what
    every client's report offers.

    Joins on the PAIR (domain_code, subdomain_code) — structured, and immune to
    label renames. Explicitly NOT trainiq's free-text `sub_module` join.

    `intervention_type` is what makes spec §11 and §8.5 structural rather than
    advisory. app/catalogue.py filters candidate rows against
    domains.INTERVENTION_MAP's `types` list BEFORE anything reaches a report or
    the model, so a compensation finding cannot produce a training
    recommendation however the narrative is phrased. §11: "Training is one
    intervention category among several."

    Seeded from a reviewable docs/intervention_catalogue.json via
    seed_catalogue.py. A JSON file diffs in git; a spreadsheet does not.

    ────────────────────────────────────────────────────────────────────────
    ONE TOPIC CAN SERVE SEVERAL CELLS, WHICH IS WHY THE CELLS ARE A SEPARATE
    TABLE.

    The supplied draft curriculum already contains an instance: topic
    `PO-EPS-T01` ("Goal Clarity and Alignment") is mapped to BOTH
    (GEM, 'M') Match with Overall Expectations AND (TID, 'I') Purpose and
    Impact. That is correct content, not a data error — the same intervention
    legitimately answers two findings.

    Folding `domain_code`/`subdomain_code` onto this row would force a choice
    between two bad options: duplicate the topic under two codes (and then
    deduplicate it in every report, by title, forever), or drop one mapping
    and silently lose a recommendation. Promo hit the same shape and resolved
    it the same way — the code names the TOPIC, the mapping names the CELL IT
    SERVES, and they are different things.
    ────────────────────────────────────────────────────────────────────────
    """
    __tablename__ = 'intervention_catalogue'
    __table_args__ = (
        UniqueConstraint('intervention_code', name='uq_intervention_code'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    intervention_code = Column(String(50), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    # One of domains.INTERVENTION_TYPES. Reports SURFACE this, so a reader can
    # see that the suggested response is a pay-equity review rather than a
    # course.
    intervention_type = Column(String(30), nullable=False)
    # Who can actually act on it. A Management Report filters to what a manager
    # can change: a manager cannot fix a pay structure, and offering it as
    # "your action" is worse than silence.
    actionable_by = Column(String(30), nullable=False, default='org')  # org|manager|both
    # 'PO' | 'CO' | 'WWO' | 'LD' — the leadTo programme a topic comes from,
    # parsed from the code prefix and stored so a report can say where a
    # recommendation originates.
    source_programme = Column(String(255))
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    cells = relationship('InterventionCell', back_populates='intervention')


class InterventionCell(db.Model):
    """
    Which (domain, subdomain) cells a catalogue topic serves.

    THE JOIN KEY IS THE PAIR, never a label — immune to a rename, and
    explicitly not trainiq's free-text `sub_module` join, where a renamed
    display string silently killed every recommendation that depended on it.

    A topic with no row here reaches no report. That is the safe default: an
    unmapped topic is invisible rather than universally recommended.
    """
    __tablename__ = 'intervention_cells'
    __table_args__ = (
        UniqueConstraint('intervention_id', 'domain_code', 'subdomain_code',
                         name='uq_intervention_cell'),
        Index('ix_cell_lookup', 'domain_code', 'subdomain_code'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    intervention_id = Column(
        UUID(as_uuid=True), ForeignKey('intervention_catalogue.id'),
        nullable=False, index=True,
    )
    domain_code = Column(String(3), nullable=False)
    subdomain_code = Column(String(1), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)

    intervention = relationship('InterventionCatalogue', back_populates='cells')


# ---------------------------------------------------------------------------
# Improvement Actions  — the half of spec §16 that needs state (plan §3.9)
# ---------------------------------------------------------------------------

class ImprovementActions(db.Model):
    """
    Spec §16 asks the system to measure organisational value, including
    "completion of agreed improvement actions", and §12.3 ends the Management
    Report with "follow-up commitments and progress monitoring". Neither is a
    report section that can be rendered from survey rows: both need state that
    persists BETWEEN cycles. Neither sibling product has an analogue to inherit.

    Without this table the product is a score generator, which §16's closing
    sentence explicitly says it is not: "The system's value lies in enabling a
    more disciplined process of diagnosis, decision-making and follow-up — not
    merely generating scores."

    THREE RULES:

    1. AN ACTION IS OWNED BY A ROLE, NEVER BY A RESPONDENT. `owner_label` is
       free text by design (a team name, a job title), and the privacy test
       scans it like any other rendered string. An action reading "follow up
       with the person who scored Trust a 1" is exactly the re-identification
       §13.5-§13.6 exists to prevent, and it is the natural thing for a
       well-meaning manager to type.

    2. ACTIONS ARE ENTERED BY AN ADMIN, NOT GENERATED. §11 and §17.1 are
       diagnose-before-prescribing: the system proposes RESPONSE CATEGORIES, a
       human commits to an action. A row may be SEEDED FROM a catalogue entry
       and then carries source_catalogue_id, so the provenance survives.

    3. The next cycle's trend section reports actions ALONGSIDE the change, under
       §16's caveat (domains.TREND_CAVEAT). It prints both and refuses to join
       them into a claim.
    """
    __tablename__ = 'improvement_actions'
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed', 'agreed', 'in_progress', 'completed', 'dropped')",
            name='ck_action_status',
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey('orgs.id'), nullable=False, index=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey('branches.id'), nullable=True, index=True)
    cycle_id = Column(String(50), ForeignKey('measurement_cycles.id'), nullable=False, index=True)

    # What the action responds to. NULL subdomain = a domain-level action;
    # NULL domain = org-wide.
    domain_code = Column(String(3))
    subdomain_code = Column(String(1))

    title = Column(String(255), nullable=False)
    description = Column(Text)
    intervention_type = Column(String(30))
    source_catalogue_id = Column(
        UUID(as_uuid=True), ForeignKey('intervention_catalogue.id'), nullable=True,
    )

    # A ROLE OR TEAM — 'Head of Finance', 'OD team'. Never a respondent.
    owner_label = Column(String(255))

    status = Column(String(16), nullable=False, default='proposed')
    agreed_at = Column(DateTime(timezone=True))
    due_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    # What changed, in the owner's words. Feeds §16's "employee perceptions of
    # changes implemented" alongside the next cycle's scores.
    evidence_note = Column(Text)

    created_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


# ---------------------------------------------------------------------------
# Pilot Feedback  — spec §15, L1 ONLY (plan §6.7)
# ---------------------------------------------------------------------------

class PilotFeedback(db.Model):
    """
    Spec §15 asks whether respondents "find any questions repetitive, confusing
    or difficult to answer" and whether they "understand the questions
    consistently". The psychometrics in the Validation Report are a PROXY for
    that: a high N/A rate tells you an item is a problem, not WHY.

    So on a cycle with is_pilot = True, the form appends ONE optional free-text
    block at the very end, and it lands here.

    THIS IS THE ONLY FREE TEXT IN THE PRODUCT, and its scope is what makes it
    acceptable:

      * only on a pilot cycle;
      * about the INSTRUMENT, not about the employer — which is a different
        risk profile from an employee comment box, the single largest
        re-identification vector in any employee survey;
      * VIEW_PILOT_FEEDBACK is L1-only. It never appears in a client report.
        A client who reads it will read it as employee comment, which is the
        thing v1 deliberately excludes;
      * purged with its cycle.

    Keyed to the session like any other response, so it carries no identity.
    """
    __tablename__ = 'pilot_feedback'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True), ForeignKey('response_sessions.id'),
        nullable=False, index=True,
    )
    deployment_id = Column(
        UUID(as_uuid=True), ForeignKey('survey_deployments.id'),
        nullable=False, index=True,
    )
    org_id = Column(UUID(as_uuid=True), ForeignKey('orgs.id'), nullable=False, index=True)
    comment = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------------------
# Analyses  — one generated narrative per report
# ---------------------------------------------------------------------------

class Analysis(db.Model):
    """
    The narrative for one report scope, generated on a MANUAL TRIGGER and
    stored so a report renders identically on every later download.

    Every QUANTITY in a report is computed deterministically in
    app/analytics/scoring.py. This table holds only the prose that narrates
    them. Reports never mention AI, Claude or Anthropic — the section is called
    "Interpretation".
    """
    __tablename__ = 'analyses'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey('orgs.id'), nullable=False, index=True)
    cycle_id = Column(String(50), ForeignKey('measurement_cycles.id'), nullable=False, index=True)
    deployment_id = Column(
        UUID(as_uuid=True), ForeignKey('survey_deployments.id'), nullable=True,
    )
    # executive | hr_od | management | validation
    report_type = Column(String(30), nullable=False)
    # Which group this narrative is about: NULL = whole scope, else
    # 'department:FIN'. Part of the identity of the narrative, because a
    # management report is per-group.
    scope_key = Column(String(100))

    payload = Column(JSONB, nullable=False)       # the structured sections
    model = Column(String(64))
    generated_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    generated_at = Column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------------------
# AI Cost Events  — what a narrative cost (plan §3.10)
# ---------------------------------------------------------------------------

class AiCostEvent(db.Model):
    """
    Written on every narrative generation, never on a deterministic render.

    DELIBERATELY SEPARATE FROM ReportEvent. That one records THAT A REPORT WAS
    PRODUCED and is a compliance record under §13.4 (role-based access, who saw
    what); this one records WHAT IT COST and is operational. They have different
    retention needs, and merging them means a cost cleanup deletes an access
    record.
    """
    __tablename__ = 'ai_cost_events'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey('orgs.id'), nullable=False, index=True)
    cycle_id = Column(String(50), ForeignKey('measurement_cycles.id'), nullable=True)
    report_type = Column(String(30), nullable=False)
    model = Column(String(64), nullable=False)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    # Numeric, not Float: this is money and it is summed on the Usage screen.
    cost_usd = Column(Numeric(10, 6), nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=_utcnow, index=True)


# ---------------------------------------------------------------------------
# Report Events  — every generation and every export (spec §13.4)
# ---------------------------------------------------------------------------

class ReportEvent(db.Model):
    """
    An access record, not a log line. §13.4 requires role-based access to
    results; this is the evidence that it held. Written on every generation and
    every export, including a management report a manager downloaded for their
    own team.
    """
    __tablename__ = 'report_events'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey('orgs.id'), nullable=False, index=True)
    cycle_id = Column(String(50), ForeignKey('measurement_cycles.id'), nullable=True)
    deployment_id = Column(
        UUID(as_uuid=True), ForeignKey('survey_deployments.id'), nullable=True,
    )
    report_type = Column(String(30), nullable=False)   # executive|hr_od|management|validation
    export_format = Column(String(10), nullable=False)  # pdf|excel
    # Which group the report covered, so an access record can be matched to a
    # suppression decision after the fact.
    scope_key = Column(String(100))
    generated_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    generated_at = Column(DateTime(timezone=True), default=_utcnow, index=True)


# ---------------------------------------------------------------------------
# Audit Events  (append-only administrative action log)
# ---------------------------------------------------------------------------

class AuditEvent(db.Model):
    """
    Every destructive or corrective admin action: archive, hard delete, edit,
    segment freeze, retention purge, voiding a session, across deployments,
    cycles, segments, branches and orgs.

    `payload` is what makes the log useful after a HARD delete: the row is gone,
    so its FK is meaningless and the entry must stand on its own. It carries the
    identifying fields as they were at the moment of deletion.

    `actor_email` is denormalised for the same reason — the actor's account may
    later be deactivated or reassigned, and "who did this" must survive that.

    NOTE: `payload` is the one place where a careless implementation could
    smuggle respondent data into a long-lived table — e.g. dumping a voided
    session's answers into it. It carries ADMINISTRATIVE identifiers only, and
    the privacy test scans it.
    """
    __tablename__ = 'audit_events'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Nullable: an L1 action on an org itself has no surviving org scope.
    org_id = Column(UUID(as_uuid=True), ForeignKey('orgs.id'), nullable=True, index=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey('branches.id'), nullable=True)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    actor_email = Column(String(255), nullable=False)
    actor_role = Column(String(20), nullable=False)

    action = Column(String(50), nullable=False, index=True)
    # deployment|cycle|segment|branch|org|session|action|catalogue
    target_type = Column(String(30), nullable=False)
    target_id = Column(String(64), nullable=False)
    summary = Column(Text, nullable=False)
    payload = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=_utcnow, index=True)
