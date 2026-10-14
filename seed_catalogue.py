"""
Seed the intervention catalogue from docs/intervention_catalogue.json.

    python seed_catalogue.py              # create or verify
    python seed_catalogue.py --force      # replace it

ONE CATALOGUE, SHARED BY EVERY TENANT. `intervention_catalogue` has no org_id,
so a single edit changes what every client's report offers — which is why
editing is L1-only, and why this seeds from a reviewable JSON file rather than
from a spreadsheet. A JSON file diffs in a pull request.

A MALFORMED ROW IS REJECTED, NEVER SKIPPED. A topic mapped to an unknown cell,
or to a Reward subdomain, aborts the seed naming the row. Skipping it quietly
would leave a client's reports short of a recommendation nobody knows is
missing.
"""
import argparse
import io
import json
import os
import sys

from app import create_app, db, domains
from app.models import InterventionCatalogue, InterventionCell

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     'docs', 'intervention_catalogue.json')


class CatalogueError(Exception):
    """Always names the offending topic."""


def read_catalogue(path=_PATH):
    if not os.path.exists(path):
        raise CatalogueError(f'{path}: missing.')
    payload = json.loads(io.open(path, encoding='utf-8').read())

    topics = payload.get('topics') or []
    if not topics:
        raise CatalogueError(f'{path}: no topics.')

    seen = set()
    for topic in topics:
        code = (topic.get('intervention_code') or '').strip()
        if not code:
            raise CatalogueError(f'{path}: a topic has no intervention_code.')
        if code in seen:
            raise CatalogueError(
                f'{code}: appears twice as a separate topic. A topic that '
                'serves two cells is ONE topic with two cells — see the '
                '`cells` list — not two rows. Duplicating it would make every '
                'report deduplicate by title, forever.')
        seen.add(code)

        if not (topic.get('title') or '').strip():
            raise CatalogueError(f'{code}: no title.')

        kind = (topic.get('intervention_type') or '').strip()
        if kind not in domains.INTERVENTION_TYPES:
            raise CatalogueError(
                f'{code}: intervention_type {kind!r} is not one of '
                f'{domains.INTERVENTION_TYPES}.')

        cells = topic.get('cells') or []
        if not cells:
            raise CatalogueError(
                f'{code}: mapped to no cell. A topic with no cell reaches no '
                'report — invisible rather than universally recommended, which '
                'is the safe default, but it is almost certainly a mistake in '
                'the file.')

        for cell in cells:
            domain_code = (cell.get('domain_code') or '').strip()
            subdomain_code = (cell.get('subdomain_code') or '').strip()
            if (domain_code, subdomain_code) not in domains.INTERVENTION_MAP:
                raise CatalogueError(
                    f'{code}: ({domain_code}, {subdomain_code}) is not a known '
                    'domain/subdomain pair. The pair is the join key; a '
                    'subdomain letter alone names three different constructs.')
            if kind == domains.TRAINING and not domains.allows_training(domain_code):
                raise CatalogueError(
                    f'{code}: mapped to '
                    f'{domains.subdomain_label(domain_code, subdomain_code)}, '
                    'which is a Reward Satisfaction subdomain. Specification '
                    '§8.5: training is not a substitute for addressing a '
                    'compensation problem. Reward findings carry guidance, not '
                    'courses — see "reward_guidance" in the file.')

    return payload


def seed(force=False):
    payload = read_catalogue()
    topics = payload['topics']

    existing = db.session.query(InterventionCatalogue).count()
    if existing and not force:
        print(f'catalogue already holds {existing} topics — nothing to do '
              '(use --force to replace).')
        return 0
    if existing and force:
        db.session.query(InterventionCell).delete()
        db.session.query(InterventionCatalogue).delete()
        print(f'--force: removed {existing} topics and their cell mappings')

    for sort_order, topic in enumerate(topics, start=1):
        row = InterventionCatalogue(
            intervention_code=topic['intervention_code'],
            title=topic['title'],
            description=topic.get('description'),
            intervention_type=topic['intervention_type'],
            actionable_by=topic.get('actionable_by', 'org'),
            source_programme=topic.get('source_programme'),
            sort_order=sort_order,
            is_active=True,
        )
        db.session.add(row)
        db.session.flush()
        for order, cell in enumerate(topic['cells'], start=1):
            db.session.add(InterventionCell(
                intervention_id=row.id,
                domain_code=cell['domain_code'],
                subdomain_code=cell['subdomain_code'],
                sort_order=order))

    db.session.commit()
    cells = sum(len(t['cells']) for t in topics)
    print(f'seeded {len(topics)} topics across {cells} cells')

    # Coverage is reported every time, because an empty cell is a product
    # question and not a bug: a finding there produces no course, and a report
    # that printed an empty development section would read as "no development
    # needed" — the worst available reading of a real finding.
    from app import catalogue as catalogue_module
    gaps = [c for c in catalogue_module.coverage(db.session) if not c['covered']]
    if gaps:
        print('\n  CELLS WITH NO COURSE (reports will name the gap rather than '
              'print an empty list):')
        for gap in gaps:
            print(f'    - {gap["label"]}')
    else:
        print('  every cell is covered, or is deliberately course-free.')
    return len(topics)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--force', action='store_true',
                        help='replace the catalogue')
    args = parser.parse_args()

    app = create_app(os.environ.get('FLASK_ENV', 'development'))
    with app.app_context():
        try:
            seed(force=args.force)
        except CatalogueError as exc:
            db.session.rollback()
            print(f'CATALOGUE SEED FAILED: {exc}', file=sys.stderr)
            sys.exit(1)


if __name__ == '__main__':
    main()
