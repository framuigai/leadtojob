"""
Remove every CLIENT organisation and all its data. Development only.

    python clear_demo_data.py --confirm

WHAT IT KEEPS
  * the master template org and its 48 items — infrastructure, not a client;
  * the intervention catalogue — reference data shared by every tenant;
  * the L1 master admin account.

WHAT IT REMOVES
  Every client org and, with it, every branch, user, segment, cycle,
  deployment, access ticket, anonymous session, response, pilot comment,
  improvement action, analysis, cost event, report event and audit row.

REFUSES TO RUN OUTSIDE DEVELOPMENT. Deleting anonymous survey responses is
irreversible in a way that matters: the people who gave them cannot be asked
again, because the system does not know who they were. That is the design
working as intended, and it is exactly what makes an accidental run
unrecoverable. Production has a per-cycle retention purge instead — confirmed,
audited, and scoped to one wave.
"""
import argparse
import os
import sys

from app import create_app, db
from app.domains import MASTER_ORG_ID
from app.models import (
    AccessTicket, AiCostEvent, Analysis, AuditEvent, Branch,
    ImprovementActions, MeasurementCycle, Org, PilotFeedback, ReportEvent,
    ResponseSession, Segment, SegmentOption, SurveyDeployment, SurveyItem,
    SurveyResponse, User,
)
from app.permissions import MASTER_ADMIN


def clear():
    client_ids = [row[0] for row in
                  db.session.query(Org.id).filter(Org.id != MASTER_ORG_ID).all()]
    if not client_ids:
        print('No client organisations. Nothing to remove.')
        return 0

    names = [row[0] for row in
             db.session.query(Org.name).filter(Org.id.in_(client_ids)).all()]
    print(f'Removing {len(client_ids)} client organisation(s): '
          f'{", ".join(sorted(names))}')

    session_ids = [row[0] for row in
                   db.session.query(ResponseSession.id)
                   .filter(ResponseSession.org_id.in_(client_ids)).all()]

    counts = {}

    def wipe(model, label, query=None):
        query = query if query is not None else db.session.query(model).filter(
            model.org_id.in_(client_ids))
        counts[label] = query.delete(synchronize_session=False)

    # Children first: every one of these has a foreign key into something below
    # it, and Postgres will refuse the parent delete otherwise.
    if session_ids:
        wipe(SurveyResponse, 'responses', db.session.query(SurveyResponse)
             .filter(SurveyResponse.session_id.in_(session_ids)))
        wipe(PilotFeedback, 'pilot comments', db.session.query(PilotFeedback)
             .filter(PilotFeedback.session_id.in_(session_ids)))
    else:
        counts['responses'] = counts['pilot comments'] = 0

    wipe(ResponseSession, 'anonymous sessions')
    wipe(AccessTicket, 'access tickets')
    wipe(ImprovementActions, 'improvement actions')
    wipe(Analysis, 'interpretations')
    wipe(AiCostEvent, 'cost events')
    wipe(ReportEvent, 'report events')
    wipe(AuditEvent, 'audit rows')
    wipe(SurveyDeployment, 'deployments')
    wipe(MeasurementCycle, 'cycles')

    segment_ids = [row[0] for row in db.session.query(Segment.id)
                   .filter(Segment.org_id.in_(client_ids)).all()]
    counts['segment options'] = (
        db.session.query(SegmentOption)
        .filter(SegmentOption.segment_id.in_(segment_ids))
        .delete(synchronize_session=False) if segment_ids else 0)
    wipe(Segment, 'segments')
    wipe(SurveyItem, 'cloned instrument items')

    # Users before branches: a user may point at a branch.
    counts['users'] = (db.session.query(User)
                       .filter(User.org_id.in_(client_ids))
                       .delete(synchronize_session=False))
    wipe(Branch, 'branches')
    counts['organisations'] = (db.session.query(Org)
                               .filter(Org.id.in_(client_ids))
                               .delete(synchronize_session=False))

    db.session.commit()

    for label, number in counts.items():
        print(f'  removed {number:>5}  {label}')

    master_items = db.session.query(SurveyItem).filter_by(
        org_id=MASTER_ORG_ID).count()
    admins = db.session.query(User).filter_by(role=MASTER_ADMIN).count()
    print(f'\nKept: the master template ({master_items} items), the '
          f'intervention catalogue, and {admins} platform administrator(s).')
    return counts['organisations']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm', action='store_true',
                        help='required; there is no undo')
    args = parser.parse_args()

    env = os.environ.get('FLASK_ENV', 'development')
    if env != 'development':
        print(f'Refusing to run in {env}. This removes anonymous survey '
              'responses, and the people who gave them cannot be asked again '
              'because the system does not know who they were. Production '
              'uses the per-cycle retention purge instead.', file=sys.stderr)
        sys.exit(1)

    if not args.confirm:
        print('Pass --confirm. This cannot be undone.', file=sys.stderr)
        sys.exit(2)

    app = create_app(env)
    with app.app_context():
        clear()


if __name__ == '__main__':
    main()
