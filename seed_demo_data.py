"""
Seed two demonstration client organisations, with branches, two closed
measurement waves each, and enough anonymous responses for every report to
build.

    python seed_demo_data.py --confirm
    python seed_demo_data.py --confirm --fresh    # clear client orgs first

WHAT IT CREATES, per organisation
  * one org, two branches, an L2 org admin and one L3 branch admin per branch;
  * a cloned 48-item instrument (app/provisioning.py — never a shared master);
  * two active segments with options, so the comparison sections have
    something to compare;
  * TWO measurement cycles, the second linked to the first through
    previous_cycle_id, so the trend section has a baseline to trend against;
  * per cycle, three CLOSED deployments — one org-wide and one per branch —
    each carrying submitted anonymous sessions with 48 answers apiece.

That shape is what "two reports per org and per branch" needs: a report is a
(cycle x scope) pair, so two closed cycles give the organisation two Executive
reports, two HR & OD reports, and two Management reports for each branch.

RESPONDENTS STAY ANONYMOUS HERE TOO. The sessions this writes carry no name,
no email and no link to a user — not because the script withholds it, but
because response_sessions has nowhere to put it (§13). The segment values are
self-declared codes, exactly as the form collects them.

DEVELOPMENT ONLY. It refuses to run anywhere else: these rows are
indistinguishable from real responses once they are in the table, and nothing
downstream would warn anyone that a figure in a client report was invented.
"""
import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

from app import create_app, db                                    # noqa: E402
from app import domains                                           # noqa: E402
from app.analytics import reports as report_builder               # noqa: E402
from app.analytics import scoring                                 # noqa: E402
from app.models import (                                          # noqa: E402
    Branch, MeasurementCycle, Org, Segment, SegmentOption, SurveyDeployment,
    SurveyItem, SurveyResponse, ResponseSession, User,
)
from app.permissions import BRANCH_ADMIN, ORG_ADMIN               # noqa: E402
from app.provisioning import clone_instrument_to_org              # noqa: E402

# Published in this file, so it is a development credential and nothing else.
# seed_admin.py refuses passwords like this outside development for the same
# reason, and this whole script refuses to run there at all.
DEMO_PASSWORD = 'demo-pass-123'

MIN_N = 5          # the production floor, not the local convenience of 1
NA_RATE = 0.06     # ~3 of 48 answers, so the Coverage section is not empty


# ── The two tenants ────────────────────────────────────────────────────────
#
# `mood` is the centre of the response distribution for that scope, and
# `shift` is what the second wave moves by. They differ per branch on purpose:
# a demo where every group scores the same teaches nothing about the product,
# and a split between two branches is precisely the finding the dispersion and
# comparison sections exist to surface.

ORGS = [
    {
        'name': 'Bluewave Logistics',
        'industry': 'Transport and logistics',
        'brand_colour': '2E75B6',
        'domain': 'bluewave.example',
        'branches': [
            {'name': 'Mombasa Depot', 'mood': 3.9, 'shift': 0.25, 'n': 11},
            {'name': 'Nairobi Hub', 'mood': 3.0, 'shift': -0.15, 'n': 9},
        ],
        'hq': {'mood': 3.5, 'shift': 0.10, 'n': 14},
        'departments': [('OPS', 'Operations'), ('FIN', 'Finance'),
                        ('CS', 'Customer Service')],
    },
    {
        'name': 'Serengeti Health Group',
        'industry': 'Healthcare',
        'brand_colour': '2F7D52',
        'domain': 'serengeti.example',
        'branches': [
            {'name': 'Westlands Clinic', 'mood': 3.6, 'shift': 0.05, 'n': 10},
            {'name': 'Karen Clinic', 'mood': 3.3, 'shift': 0.30, 'n': 10},
        ],
        'hq': {'mood': 3.4, 'shift': 0.20, 'n': 13},
        'departments': [('CLIN', 'Clinical'), ('NURS', 'Nursing'),
                        ('ADMIN', 'Administration')],
    },
]

TENURE_BANDS = [('LT1', 'Under a year'), ('1_3', '1-3 years'),
                ('3_10', '3-10 years'), ('GT10', 'Over 10 years')]

# Per-domain offsets from the scope's mood. PIE sits lowest in both tenants,
# which is the ordinary finding in this instrument and the one that most often
# tempts a training recommendation it must never get (§11, §8.5).
DOMAIN_OFFSET = {domains.GEM: 0.0, domains.TID: 0.2,
                 domains.BIT: 0.35, domains.PIE: -0.45}

PURPOSE = (
    'This survey asks how you experience your work here. Answers are '
    'anonymous: we do not record your name, your email address or anything '
    'else that could identify you, and we never see an individual response. '
    'Results are reported only for groups of {min_n} or more people, and are '
    'used to decide where to improve — never to assess any individual. '
    'Nothing you say here can be held against you.'
)

CYCLES = [
    {'suffix': '2025-H2', 'name': 'Employee Satisfaction 2025 H2',
     'opened': 300, 'closed': 272},
    {'suffix': '2026-H1', 'name': 'Employee Satisfaction 2026 H1',
     'opened': 120, 'closed': 92},
]


def _ago(days):
    return datetime.now(timezone.utc) - timedelta(days=days)


def _slug(prefix, rng):
    return f'{prefix}-' + ''.join(rng.choice('abcdefghjkmnpqrstuvwxyz23456789')
                                  for _ in range(24))


def _answer(rng, centre):
    """
    One 1-5 answer around `centre`, clamped. A normal draw rather than a
    uniform one, so the distribution charts show a shape instead of a flat
    bar — the distribution is the primary display (§10.5), so a demo whose
    distributions are meaningless is a demo of the wrong thing.
    """
    draw = rng.gauss(centre, 0.95)
    return max(1, min(5, int(round(draw))))


def _fill_session(rng, session, deployment, items, centre):
    """48 answers for one anonymous session, a few of them N/A."""
    rows = []
    for item in items:
        offset = DOMAIN_OFFSET.get(item.domain_code, 0.0)
        # N/A is a FIRST-CLASS STATE, never a sentinel score (§9.1). Peer
        # Comparison draws more of them, which is itself the finding: people
        # often do not know what their peers earn.
        na_rate = NA_RATE * (3.0 if (item.domain_code, item.subdomain_code)
                             == (domains.PIE, 'P') else 1.0)
        if item.allows_na and rng.random() < na_rate:
            rows.append(SurveyResponse(
                session_id=session.id, deployment_id=deployment.id,
                org_id=deployment.org_id, item_id=item.id,
                score=None, not_applicable=True,
                answered_at=session.submitted_at))
        else:
            rows.append(SurveyResponse(
                session_id=session.id, deployment_id=deployment.id,
                org_id=deployment.org_id, item_id=item.id,
                score=_answer(rng, centre + offset), not_applicable=False,
                answered_at=session.submitted_at))
    db.session.add_all(rows)
    return rows


def _deployment(cycle, org, branch, spec, items, rng, departments, closed_at):
    """One closed wave over one audience, with its submitted sessions."""
    branch_id = branch.id if branch else None
    deployment = SurveyDeployment(
        org_id=org.id, branch_id=branch_id, cycle_id=cycle.id,
        assessed_domains=list(cycle.assessed_domains or domains.DOMAINS),
        instrument_version=1,
        public_slug=_slug('demo', rng),
        delivery_mode='open_link',
        expected_respondents=int(spec['n'] * 1.6) + 4,
        responses_received=0,
        deployed_at=cycle.opened_at, closes_at=closed_at,
        closed=True,
    )
    db.session.add(deployment)
    db.session.flush()

    centre = spec['mood'] + (spec['shift'] if cycle.previous_cycle_id else 0.0)
    submitted = 0
    for _ in range(spec['n']):
        started = closed_at - timedelta(days=rng.randint(1, 20),
                                        minutes=rng.randint(0, 600))
        taken = timedelta(minutes=rng.randint(7, 25))
        session = ResponseSession(
            deployment_id=deployment.id, org_id=org.id, branch_id=branch_id,
            segment_values={
                'department': rng.choice(departments)[0],
                'tenure_band': rng.choice(TENURE_BANDS)[0],
            },
            started_at=started,
            submitted_at=started + taken,
            last_seen_at=started + taken,
            resume_secret_hash=None,
            is_void=False,
        )
        db.session.add(session)
        db.session.flush()
        _fill_session(rng, session, deployment, items,
                      centre + rng.gauss(0, 0.45))
        submitted += 1

    # One unsubmitted draft per wave: a partial response is not a response,
    # and a demo that never exercises that distinction hides it.
    db.session.add(ResponseSession(
        deployment_id=deployment.id, org_id=org.id, branch_id=branch_id,
        segment_values={'department': rng.choice(departments)[0]},
        started_at=closed_at - timedelta(days=2), submitted_at=None,
        last_seen_at=closed_at - timedelta(days=2), is_void=False))

    deployment.responses_received = submitted
    return deployment


def seed_org(blueprint, rng):
    existing = db.session.query(Org).filter_by(name=blueprint['name']).first()
    if existing is not None:
        print(f"  {blueprint['name']}: already present — skipped. "
              'Use --fresh to rebuild.')
        return None

    org = Org(name=blueprint['name'], industry=blueprint['industry'],
              brand_colour=blueprint['brand_colour'],
              active_domains=list(domains.DOMAINS),
              setup_complete=True, is_active=True)
    db.session.add(org)
    db.session.flush()

    clone_instrument_to_org(org.id)
    items = (db.session.query(SurveyItem)
             .filter_by(org_id=org.id, version=1)
             .order_by(SurveyItem.sort_order).all())

    # Segments, ACTIVE and with options — provisioning seeds them inactive and
    # empty on purpose, because only a client knows what their departments are
    # called. Here we are the client.
    now = datetime.now(timezone.utc)
    for sort_order, (key, label, options) in enumerate([
        ('department', 'Department', blueprint['departments']),
        ('tenure_band', 'Length of service', TENURE_BANDS),
    ], start=1):
        segment = Segment(org_id=org.id, segment_key=key, label=label,
                          collect_at_survey=True, is_active=True,
                          sort_order=sort_order, frozen_at=now)
        db.session.add(segment)
        db.session.flush()
        for n, (code, option_label) in enumerate(options, start=1):
            db.session.add(SegmentOption(
                segment_id=segment.id, option_code=code,
                option_label=option_label, sort_order=n, is_active=True))

    branches = []
    for spec in blueprint['branches']:
        branch = Branch(org_id=org.id, name=spec['name'], is_active=True)
        db.session.add(branch)
        db.session.flush()
        branches.append((branch, spec))

    domain_name = blueprint['domain']
    org_admin = User(org_id=org.id, branch_id=None, role=ORG_ADMIN,
                     email=f'admin@{domain_name}', full_name='Org Admin',
                     is_active=True)
    org_admin.set_password(DEMO_PASSWORD)
    db.session.add(org_admin)

    for branch, _ in branches:
        local = branch.name.split()[0].lower()
        user = User(org_id=org.id, branch_id=branch.id, role=BRANCH_ADMIN,
                    email=f'{local}@{domain_name}',
                    full_name=f'{branch.name} Admin', is_active=True)
        user.set_password(DEMO_PASSWORD)
        db.session.add(user)
    db.session.flush()

    prefix = ''.join(w[0] for w in blueprint['name'].split()[:2]).upper()
    previous_id = None
    for spec in CYCLES:
        cycle = MeasurementCycle(
            id=f'{prefix}-{spec["suffix"]}',
            org_id=org.id, branch_id=None,
            cycle_name=spec['name'],
            purpose_statement=PURPOSE.format(min_n=MIN_N),
            assessed_domains=list(domains.DOMAINS),
            previous_cycle_id=previous_id,
            status='closed',
            opened_at=_ago(spec['opened']),
            closes_at=_ago(spec['closed']),
            closed_at=_ago(spec['closed']),
            min_reporting_n=MIN_N,
            scoring_method='mean_of_subdomains',
            retention_months=24,
            is_pilot=False,
            created_by=org_admin.id,
        )
        db.session.add(cycle)
        db.session.flush()

        closed_at = cycle.closed_at
        _deployment(cycle, org, None, blueprint['hq'], items, rng,
                    blueprint['departments'], closed_at)
        for branch, branch_spec in branches:
            _deployment(cycle, org, branch, branch_spec, items, rng,
                        blueprint['departments'], closed_at)

        previous_id = cycle.id

    print(f"  {blueprint['name']}: 2 branches, 2 cycles, 6 closed "
          f'deployments, instrument cloned ({len(items)} items)')
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
    branches = db.session.query(Branch).filter_by(org_id=org.id).all()

    for cycle in cycles:
        waves = (db.session.query(SurveyDeployment)
                 .filter_by(cycle_id=cycle.id, org_id=org.id).all())
        scopes = [(None, 'Whole organisation', waves)]
        for branch in branches:
            scopes.append((branch, branch.name,
                           [w for w in waves if w.branch_id == branch.id]))

        for branch, label, scoped in scopes:
            profiles = scoring.load_profiles(
                db.session, [w.id for w in scoped], method=cycle.scoring_method)
            types = (report_builder.REPORT_TYPES if branch is None
                     else (report_builder.MANAGEMENT,))
            for report_type in types:
                try:
                    report_builder.require_reportable(
                        report_type, cycle, scoped, profiles, label)
                    state = f'OK  n={len(profiles)}'
                except report_builder.ReportNotAvailable as exc:
                    ok = False
                    state = f'UNAVAILABLE — {exc}'
                print(f'    {cycle.id:<22} {label:<22} '
                      f'{report_builder.report_title(report_type):<26} {state}')
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm', action='store_true',
                        help='required: this writes rows that look exactly '
                             'like real anonymous responses')
    parser.add_argument('--fresh', action='store_true',
                        help='clear every client org first (clear_demo_data)')
    parser.add_argument('--seed', type=int, default=20260415,
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
                   (seed_org(blueprint, rng) for blueprint in ORGS)
                   if org is not None]
        db.session.commit()

        if not created:
            print('Nothing created.')
            return

        print('\nReport availability, through app/analytics/reports.py:')
        all_ok = all(verify(org) for org in created)

        print(f'\nSign in as any of these with the password {DEMO_PASSWORD!r}:')
        for org in created:
            for user in (db.session.query(User).filter_by(org_id=org.id)
                         .order_by(User.role, User.email).all()):
                scope = user.branch.name if user.branch else org.name
                print(f'  {user.email:<34} {user.role:<13} {scope}')

        if not all_ok:
            print('\nSome reports are unavailable — see above.', file=sys.stderr)
            sys.exit(3)


if __name__ == '__main__':
    main()
