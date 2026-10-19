"""
Seed five demonstration client organisations with a full history of closed
measurement cycles, branches and anonymous responses.

    python seed_demo_data.py --confirm
    python seed_demo_data.py --confirm --fresh    # clear client orgs first

WHAT IT CREATES, per organisation
  * one org with a short login name  (KPLC -> kplc@jobs.dev);
  * THREE branches, each with its own L3 admin;
  * a cloned 48-item instrument (app/provisioning.py — never a shared master);
  * THREE organisation-wide cycles, chained through previous_cycle_id so the
    trend section has a baseline to trend against;
  * THREE cycles PER BRANCH, chained the same way — so every branch has its
    own history, not just a slice of the organisation's;
  * one closed deployment per cycle, carrying submitted anonymous sessions
    with 48 answers apiece.

That is 12 cycles and 12 closed deployments per organisation, 60 of each
across the five — enough that every report type builds at every scope.

NO TWO SCOPES PRODUCE THE SAME NUMBERS. Each organisation has its own domain
profile (`profile`), each branch its own offset from it, each cycle its own
drift, and each session its own jitter on top. A demo where every group scores
alike teaches nothing about a product whose entire job is telling groups apart.

RESPONDENTS STAY ANONYMOUS HERE TOO. The sessions this writes carry no name,
no email and no link to a user — not because the script withholds it, but
because response_sessions has nowhere to put it (§13).

SEGMENTS ARE NOT SEEDED. They were removed from the product; the tables remain
but nothing reads them, and writing rows no screen can show would be seeding a
feature that does not exist.

DEVELOPMENT ONLY. It refuses to run anywhere else: these rows are
indistinguishable from real responses once they are in the table, and nothing
downstream would warn anyone that a figure in a client report was invented.
"""
import argparse
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

from flask import current_app                                     # noqa: E402

from app import create_app, db                                    # noqa: E402
from app import domains                                           # noqa: E402
from app.analytics import reports as report_builder               # noqa: E402
from app.analytics import scoring                                 # noqa: E402
from app.models import (                                          # noqa: E402
    Branch, MeasurementCycle, Org, SurveyDeployment, SurveyItem,
    SurveyResponse, ResponseSession, User,
)
from app.permissions import BRANCH_ADMIN, ORG_ADMIN               # noqa: E402
from app.provisioning import clone_instrument_to_org              # noqa: E402

# Published in this file, so it is a development credential and nothing else.
DEMO_PASSWORD = 'changeme123'

LOGIN_DOMAIN = 'jobs.dev'


# ── The five tenants ───────────────────────────────────────────────────────
#
# `profile` is a per-domain offset, and it is what stops the five from looking
# alike: one organisation is strong on relationships and weak on reward,
# another the reverse. `mood` is the centre each scope draws around, `shift`
# the per-cycle drift (positive improves over the three waves, negative
# declines), and `n` the number of submitted responses.

ORGS = [
    {
        'short': 'KPLC',
        'name': 'Kenya Power',
        'industry': 'Utilities',
        'brand_colour': '2E75B6',
        'mood': 3.4,
        # Strong relationships, weak reward: the classic utility profile, and
        # the one that most tempts a training recommendation §8.5 forbids.
        'profile': {domains.GEM: 0.0, domains.TID: 0.10,
                    domains.BIT: 0.45, domains.PIE: -0.70},
        'branches': [
            {'name': 'Nairobi Region', 'login': 'nairobi',
             'mood': 3.6, 'shift': 0.18, 'n': 22},
            {'name': 'Coast Region', 'login': 'coast',
             'mood': 3.1, 'shift': -0.12, 'n': 14},
            {'name': 'Western Region', 'login': 'western',
             'mood': 3.3, 'shift': 0.05, 'n': 11},
        ],
        'hq': {'mood': 3.4, 'shift': 0.10, 'n': 24},
    },
    {
        'short': 'SAFCOM',
        'name': 'Safaricom',
        'industry': 'Telecommunications',
        'brand_colour': '3F9C35',
        'mood': 4.0,
        # High across the board, which is its own reporting problem: a ceiling
        # effect leaves the diagnostic domains with little to separate them.
        'profile': {domains.GEM: 0.15, domains.TID: 0.30,
                    domains.BIT: 0.20, domains.PIE: 0.05},
        'branches': [
            {'name': 'Westlands Campus', 'login': 'westlands',
             'mood': 4.2, 'shift': 0.05, 'n': 26},
            {'name': 'Mombasa Office', 'login': 'mombasa',
             'mood': 3.8, 'shift': 0.22, 'n': 12},
            {'name': 'Kisumu Office', 'login': 'kisumu',
             'mood': 3.9, 'shift': -0.08, 'n': 9},
        ],
        'hq': {'mood': 4.0, 'shift': 0.08, 'n': 28},
    },
    {
        'short': 'KCB',
        'name': 'KCB Bank',
        'industry': 'Banking',
        'brand_colour': '00653A',
        'mood': 3.1,
        # Challenge and development well above a flat middle, trust below it:
        # a demanding employer people do not quite believe. The dispersion
        # section is the one that shows this.
        'profile': {domains.GEM: -0.10, domains.TID: 0.40,
                    domains.BIT: -0.30, domains.PIE: 0.15},
        'branches': [
            {'name': 'Head Office', 'login': 'ho',
             'mood': 3.3, 'shift': -0.20, 'n': 19},
            {'name': 'Thika Road Branch', 'login': 'thika',
             'mood': 2.9, 'shift': 0.28, 'n': 10},
            {'name': 'Nakuru Branch', 'login': 'nakuru',
             'mood': 3.0, 'shift': 0.12, 'n': 8},
        ],
        'hq': {'mood': 3.1, 'shift': -0.05, 'n': 21},
    },
    {
        'short': 'BIDCO',
        'name': 'Bidco Africa',
        'industry': 'Manufacturing',
        'brand_colour': 'C8102E',
        'mood': 2.9,
        # The lowest of the five and declining: the organisation whose report
        # has to say something difficult without a band label to hide behind.
        'profile': {domains.GEM: -0.20, domains.TID: -0.15,
                    domains.BIT: 0.10, domains.PIE: -0.40},
        'branches': [
            {'name': 'Thika Plant', 'login': 'plant',
             'mood': 2.7, 'shift': -0.25, 'n': 25},
            {'name': 'Nairobi Sales', 'login': 'sales',
             'mood': 3.4, 'shift': 0.15, 'n': 13},
            {'name': 'Eldoret Depot', 'login': 'eldoret',
             'mood': 2.8, 'shift': -0.10, 'n': 7},
        ],
        'hq': {'mood': 2.9, 'shift': -0.18, 'n': 20},
    },
    {
        'short': 'JUBILEE',
        'name': 'Jubilee Insurance',
        'industry': 'Insurance',
        'brand_colour': '6B2C91',
        'mood': 3.7,
        # Reward above the middle and task satisfaction below it — paid well
        # for work people do not find meaningful, which is exactly the
        # association the GEM-relative sections exist to surface.
        'profile': {domains.GEM: 0.05, domains.TID: -0.35,
                    domains.BIT: 0.15, domains.PIE: 0.50},
        'branches': [
            {'name': 'Life Division', 'login': 'life',
             'mood': 3.9, 'shift': 0.10, 'n': 16},
            {'name': 'General Division', 'login': 'general',
             'mood': 3.5, 'shift': 0.20, 'n': 12},
            {'name': 'Medical Division', 'login': 'medical',
             'mood': 3.6, 'shift': -0.15, 'n': 15},
        ],
        'hq': {'mood': 3.7, 'shift': 0.12, 'n': 18},
    },
]

# Three waves per scope, oldest first. Each carries its own N/A rate so the
# Coverage section differs between cycles as well as between organisations.
CYCLES = [
    {'tag': '2025-H1', 'label': 'Employee Satisfaction 2025 H1',
     'opened': 430, 'closed': 400, 'na': 0.05},
    {'tag': '2025-H2', 'label': 'Employee Satisfaction 2025 H2',
     'opened': 250, 'closed': 220, 'na': 0.08},
    {'tag': '2026-H1', 'label': 'Employee Satisfaction 2026 H1',
     'opened': 110, 'closed': 80,  'na': 0.04},
]

PURPOSE = (
    'This survey asks how you experience your work at {org}. Answers are '
    'anonymous: we do not record your name, your email address or anything '
    'else that could identify you, and no individual response is ever shown '
    'to anyone. Results are reported for groups only and are used to decide '
    'where to improve — never to assess any individual. Answering honestly '
    'carries no consequence.'
)


def _ago(days):
    return datetime.now(timezone.utc) - timedelta(days=days)


def _slug(rng):
    return 'demo-' + ''.join(
        rng.choice('abcdefghjkmnpqrstuvwxyz23456789') for _ in range(26))


def _answer(rng, centre):
    """
    One 1-5 answer around `centre`, clamped.

    A normal draw rather than a uniform one, so the distribution charts show a
    shape instead of a flat bar. The distribution is the PRIMARY display
    (§10.5), so a demo whose distributions are meaningless demonstrates the
    wrong thing.
    """
    return max(1, min(5, int(round(rng.gauss(centre, 0.95)))))


def _build_deployment(cycle, org, branch, spec, items, rng, profile,
                      wave, closed_at):
    """
    One closed wave over one audience, with its submitted sessions.

    Rows are built in memory and written with bulk_save_objects: at ~48 rows
    per session and sixty deployments this is tens of thousands of inserts,
    and a flush per row against a pooled connection turns a one-minute seed
    into a twenty-minute one.
    """
    branch_id = branch.id if branch else None
    deployment = SurveyDeployment(
        id=uuid.uuid4(),
        org_id=org.id, branch_id=branch_id, cycle_id=cycle.id,
        assessed_domains=list(cycle.assessed_domains or domains.DOMAINS),
        instrument_version=1,
        public_slug=_slug(rng),
        delivery_mode='open_link',
        # A response rate that is neither suspiciously perfect nor identical
        # between scopes.
        expected_respondents=int(spec['n'] / rng.uniform(0.45, 0.88)) + 1,
        responses_received=spec['n'],
        deployed_at=cycle.opened_at,
        closes_at=closed_at,
        closed=True,
    )
    db.session.add(deployment)

    # The scope's centre for THIS wave: its own mood, plus the drift applied
    # once per completed wave, so the three cycles trend rather than repeat.
    centre = spec['mood'] + spec['shift'] * wave

    sessions, responses = [], []
    for _ in range(spec['n']):
        started = closed_at - timedelta(days=rng.randint(1, 25),
                                        minutes=rng.randint(0, 900))
        submitted = started + timedelta(minutes=rng.randint(7, 26))
        session_id = uuid.uuid4()
        sessions.append(ResponseSession(
            id=session_id, deployment_id=deployment.id, org_id=org.id,
            branch_id=branch_id, segment_values={},
            started_at=started, submitted_at=submitted,
            last_seen_at=submitted, resume_secret_hash=None, is_void=False))

        # Per-respondent jitter on top of the scope centre. Without it every
        # session in a group is the same person answered 48 times, and the
        # dispersion figures — a real finding (§10.5) — are meaningless.
        personal = centre + rng.gauss(0, 0.5)

        for item in items:
            offset = profile.get(item.domain_code, 0.0)
            # N/A IS A FIRST-CLASS STATE, never a sentinel score (§9.1). Peer
            # Comparison draws far more of them, which is itself the finding:
            # people often do not know what their peers earn.
            na_rate = cycle_na_rate * (
                3.2 if (item.domain_code, item.subdomain_code)
                == (domains.PIE, 'P') else 1.0)
            if item.allows_na and rng.random() < na_rate:
                responses.append(SurveyResponse(
                    id=uuid.uuid4(), session_id=session_id,
                    deployment_id=deployment.id, org_id=org.id,
                    item_id=item.id, score=None, not_applicable=True,
                    answered_at=submitted))
            else:
                score = _answer(rng, personal + offset)
                responses.append(SurveyResponse(
                    id=uuid.uuid4(), session_id=session_id,
                    deployment_id=deployment.id, org_id=org.id,
                    item_id=item.id, score=score, not_applicable=False,
                    answered_at=submitted))

    # One unsubmitted draft per wave: a partial response is not a response,
    # and a demo that never exercises that distinction hides it.
    sessions.append(ResponseSession(
        id=uuid.uuid4(), deployment_id=deployment.id, org_id=org.id,
        branch_id=branch_id, segment_values={},
        started_at=closed_at - timedelta(days=2), submitted_at=None,
        last_seen_at=closed_at - timedelta(days=2), is_void=False))

    db.session.flush()
    db.session.bulk_save_objects(sessions)
    db.session.bulk_save_objects(responses)
    return len(responses)


# Set per cycle by seed_org; read by _build_deployment. A module-level value
# rather than another positional argument, because it varies per WAVE and not
# per scope, and threading it through would invite passing the wrong one.
cycle_na_rate = 0.06


def _make_cycle(cycle_id, org, branch, spec, label, previous_id, admin_id):
    cycle = MeasurementCycle(
        id=cycle_id,
        org_id=org.id,
        branch_id=branch.id if branch else None,
        cycle_name=label,
        purpose_statement=PURPOSE.format(org=org.name),
        assessed_domains=list(domains.DOMAINS),
        previous_cycle_id=previous_id,
        status='closed',
        opened_at=_ago(spec['opened']),
        closes_at=_ago(spec['closed']),
        closed_at=_ago(spec['closed']),
        # MIN_REPORTING_N IS 1, PLATFORM-WIDE, AND THIS SCRIPT DOES NOT VARY
        # IT. A seeded cycle carrying a different threshold from the one the
        # platform is configured with makes every screen disagree with
        # config.py, for no gain but a prettier demo.
        min_reporting_n=current_app.config['MIN_REPORTING_N'],
        scoring_method='mean_of_subdomains',
        retention_months=24,
        is_pilot=False,
        created_by=admin_id,
    )
    db.session.add(cycle)
    db.session.flush()
    return cycle


def seed_org(blueprint, rng):
    global cycle_na_rate

    if db.session.query(Org).filter_by(name=blueprint['name']).first():
        print(f"  {blueprint['name']}: already present — skipped. "
              'Use --fresh to rebuild.')
        return None

    org = Org(name=blueprint['name'], industry=blueprint['industry'],
              brand_colour=blueprint['brand_colour'],
              active_domains=list(domains.DOMAINS),
              # The §9.2 statement every new cycle now inherits. Without it an
              # L2 admin cannot create a cycle at all, so a seeded org that
              # omitted it would look broken on the first click.
              default_purpose_statement=PURPOSE.format(org=blueprint['name']),
              setup_complete=True, is_active=True)
    db.session.add(org)
    db.session.flush()

    clone_instrument_to_org(org.id)
    items = (db.session.query(SurveyItem)
             .filter_by(org_id=org.id, version=1)
             .order_by(SurveyItem.sort_order).all())

    short = blueprint['short'].lower()
    org_admin = User(org_id=org.id, branch_id=None, role=ORG_ADMIN,
                     email=f'{short}@{LOGIN_DOMAIN}',
                     full_name=f"{blueprint['name']} Admin", is_active=True)
    org_admin.set_password(DEMO_PASSWORD)
    db.session.add(org_admin)
    db.session.flush()

    branches = []
    for spec in blueprint['branches']:
        branch = Branch(org_id=org.id, name=spec['name'], is_active=True)
        db.session.add(branch)
        db.session.flush()
        admin = User(org_id=org.id, branch_id=branch.id, role=BRANCH_ADMIN,
                     email=f"{short}-{spec['login']}@{LOGIN_DOMAIN}",
                     full_name=f"{spec['name']} Admin", is_active=True)
        admin.set_password(DEMO_PASSWORD)
        db.session.add(admin)
        db.session.flush()
        branches.append((branch, spec, admin))

    prefix = blueprint['short']
    rows = cycles = 0

    # ── Three ORGANISATION-WIDE cycles, chained ────────────────────────────
    previous_id = None
    for wave, spec in enumerate(CYCLES):
        cycle_na_rate = spec['na']
        cycle = _make_cycle(f"{prefix}-ORG-{spec['tag']}", org, None, spec,
                            spec['label'], previous_id, org_admin.id)
        rows += _build_deployment(cycle, org, None, blueprint['hq'], items,
                                  rng, blueprint['profile'], wave,
                                  cycle.closed_at)
        previous_id = cycle.id
        cycles += 1

    # ── Three cycles PER BRANCH, each chain independent ────────────────────
    for branch, bspec, admin in branches:
        previous_id = None
        for wave, spec in enumerate(CYCLES):
            cycle_na_rate = spec['na']
            cycle = _make_cycle(
                f"{prefix}-{bspec['login'].upper()}-{spec['tag']}",
                org, branch, spec,
                f"{branch.name} — {spec['label']}",
                previous_id, admin.id)
            rows += _build_deployment(cycle, org, branch, bspec, items, rng,
                                      blueprint['profile'], wave,
                                      cycle.closed_at)
            previous_id = cycle.id
            cycles += 1

    db.session.commit()
    print(f"  {blueprint['name']:<22} {len(branches)} branches, "
          f'{cycles} closed cycles, {rows:,} answers')
    return org


def verify(org):
    """
    Prove the reports actually build, through the same gate the routes use.

    A seed that produces rows the report layer then refuses is worse than no
    seed at all: it looks finished right up to the moment somebody clicks
    Download.
    """
    ok = True
    cycles = (db.session.query(MeasurementCycle)
              .filter_by(org_id=org.id)
              .order_by(MeasurementCycle.opened_at).all())

    for cycle in cycles:
        waves = (db.session.query(SurveyDeployment)
                 .filter_by(cycle_id=cycle.id, org_id=org.id).all())
        branch = (db.session.get(Branch, cycle.branch_id)
                  if cycle.branch_id else None)
        label = branch.name if branch else 'Whole organisation'
        profiles = scoring.load_profiles(
            db.session, [w.id for w in waves], method=cycle.scoring_method)
        types = (report_builder.REPORT_TYPES if branch is None
                 else (report_builder.MANAGEMENT,))
        for report_type in types:
            try:
                report_builder.require_reportable(
                    report_type, cycle, waves, profiles, label)
            except report_builder.ReportNotAvailable as exc:
                ok = False
                print(f'    {cycle.id:<26} {label:<22} '
                      f'{report_builder.report_title(report_type):<26} '
                      f'UNAVAILABLE — {exc}')
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm', action='store_true',
                        help='required: this writes rows that look exactly '
                             'like real anonymous responses')
    parser.add_argument('--fresh', action='store_true',
                        help='clear every client org first (clear_demo_data)')
    parser.add_argument('--seed', type=int, default=20261019,
                        help='RNG seed; the same seed gives the same figures')
    args = parser.parse_args()

    env = os.environ.get('FLASK_ENV', 'development')
    if env != 'development':
        print(f'Refusing to run in {env}. Seeded responses are '
              'indistinguishable from real ones once written, and no report '
              'downstream would warn anyone that a figure was invented.',
              file=sys.stderr)
        sys.exit(1)

    if not args.confirm:
        print('Pass --confirm.', file=sys.stderr)
        sys.exit(2)

    app = create_app(env)
    with app.app_context():
        if args.fresh:
            from clear_demo_data import clear
            clear()

        rng = random.Random(args.seed)
        created = [org for org in
                   (seed_org(bp, rng) for bp in ORGS) if org is not None]

        if not created:
            print('Nothing created.')
            return

        print('\nChecking every report builds, through app/analytics/reports.py')
        all_ok = all(verify(org) for org in created)
        print('  all reports available' if all_ok
              else '  SOME REPORTS UNAVAILABLE — see above')

        print(f'\nSign in with the password {DEMO_PASSWORD!r}:')
        for org in created:
            for user in (db.session.query(User).filter_by(org_id=org.id)
                         .order_by(User.role.desc(), User.email).all()):
                scope = user.branch.name if user.branch else org.name
                print(f'  {user.email:<32} {user.role:<13} {scope}')

        if not all_ok:
            sys.exit(3)


if __name__ == '__main__':
    main()
