"""
Seed the MASTER instrument: 48 items into the master template org.

    python seed_templates.py              # create or verify
    python seed_templates.py --force      # replace the MASTER only
    python seed_templates.py --reclone-org <org_id>

SOURCE OF TRUTH: docs/surveys/{gem,tid,bit,pie}.csv, 12 rows each, transcribed
verbatim from the specification §5-§8 and verified against it item by item (see
docs/workbook_reconciliation.md). CSV rather than the supplied .xlsx because a
CSV diffs in git and a spreadsheet does not — when a client signs off on
wording, the thing they signed off has to be reviewable in a pull request.

A MALFORMED CELL IS REJECTED, NEVER TRUNCATED. A subdomain delivered with 3 or
5 questions, an unknown domain or an unmapped subdomain aborts the seed naming
the file and the line. A silently short subdomain rests a reported score on
three items and is visible only as a wrong number, weeks later, with no way to
tell it from a real finding.

--force replaces the MASTER only. Orgs that have already cloned keep their copy,
by design: a client's instrument is frozen at the moment they first deployed it,
and changing it under them would silently alter what their historical responses
were answers TO.
"""
import argparse
import csv
import io
import os
import sys

from app import create_app, db
from app.domains import (
    DOMAINS, ITEMS_PER_SUBDOMAIN, MASTER_ORG_ID, SUBDOMAINS, item_code,
)
from app.models import Org, SurveyItem, SurveyResponse

SURVEY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'docs', 'surveys')


class SeedError(Exception):
    """Always names the file and the line. A seed failure must be actionable."""


def _read_domain_csv(domain_code):
    """
    Read and VALIDATE one domain's file. Returns 12 rows or raises.

    Validation is deliberately strict and deliberately total — every row is
    checked before any is used, so a bad file produces one clear error rather
    than a half-seeded database.
    """
    path = os.path.join(SURVEY_DIR, f'{domain_code.lower()}.csv')
    if not os.path.exists(path):
        raise SeedError(f'{path}: missing. Expected one CSV per domain.')

    rows = []
    with io.open(path, encoding='utf-8-sig', newline='') as fh:
        for lineno, raw in enumerate(csv.DictReader(fh), start=2):
            dom = (raw.get('domain_code') or '').strip()
            sub = (raw.get('subdomain_code') or '').strip()
            text = (raw.get('item_text') or '').strip()
            ordinal_raw = (raw.get('ordinal') or '').strip()

            if dom != domain_code:
                raise SeedError(
                    f'{path}:{lineno}: domain_code is {dom!r}, expected '
                    f'{domain_code!r}. Each file holds exactly one domain.'
                )
            if sub not in SUBDOMAINS[domain_code]:
                raise SeedError(
                    f'{path}:{lineno}: subdomain_code {sub!r} is not one of '
                    f'{SUBDOMAINS[domain_code]} for domain {domain_code}. '
                    'Subdomain letters repeat across domains, so the pair is '
                    'what identifies a cell.'
                )
            if not text:
                raise SeedError(f'{path}:{lineno}: item_text is empty.')
            try:
                ordinal = int(ordinal_raw)
            except ValueError:
                raise SeedError(
                    f'{path}:{lineno}: ordinal {ordinal_raw!r} is not a number.'
                )
            if not 1 <= ordinal <= ITEMS_PER_SUBDOMAIN:
                raise SeedError(
                    f'{path}:{lineno}: ordinal {ordinal} is outside '
                    f'1-{ITEMS_PER_SUBDOMAIN}.'
                )

            expected = item_code(dom, sub, ordinal)
            supplied = (raw.get('item_code') or '').strip()
            if supplied and supplied != expected:
                raise SeedError(
                    f'{path}:{lineno}: item_code {supplied!r} does not match '
                    f'{expected!r} derived from its own domain, subdomain and '
                    'ordinal. One of the four is wrong; the file does not say '
                    'which, so it is rejected rather than guessed.'
                )
            rows.append({'domain_code': dom, 'subdomain_code': sub,
                         'ordinal': ordinal, 'item_code': expected,
                         'item_text': text})

    # Shape. Checked per subdomain, not merely as a total of 12 — 5 items in one
    # cell and 3 in another also totals 12, and that is the error most likely to
    # survive a human read of the file.
    for sub in SUBDOMAINS[domain_code]:
        got = sorted(r['ordinal'] for r in rows if r['subdomain_code'] == sub)
        if got != list(range(1, ITEMS_PER_SUBDOMAIN + 1)):
            raise SeedError(
                f'{path}: subdomain {domain_code}-{sub} has ordinals {got}, '
                f'expected {list(range(1, ITEMS_PER_SUBDOMAIN + 1))}. '
                f'Specification §4 requires exactly {ITEMS_PER_SUBDOMAIN} '
                'questions per subdomain; a short cell would rest a reported '
                'score on fewer items without saying so.'
            )
    return rows


def read_all_items():
    """All 48, validated. Raises SeedError naming the file and line on any fault."""
    items = []
    for domain_code in DOMAINS:
        items.extend(_read_domain_csv(domain_code))
    codes = [r['item_code'] for r in items]
    if len(set(codes)) != len(codes):
        dupes = sorted({c for c in codes if codes.count(c) > 1})
        raise SeedError(f'duplicate item_code across files: {dupes}')
    return items


def seed_master(force=False):
    master = db.session.get(Org, MASTER_ORG_ID)
    if master is None:
        master = Org(
            id=MASTER_ORG_ID,
            name='leadTo Master Template',
            industry='Template',
            active_domains=list(DOMAINS),
            setup_complete=True,
        )
        db.session.add(master)
        db.session.flush()
        print(f'created master template org {MASTER_ORG_ID}')

    existing = (db.session.query(SurveyItem)
                .filter_by(org_id=MASTER_ORG_ID, version=1).count())
    if existing and not force:
        print(f'master already holds {existing} items — nothing to do '
              '(use --force to replace).')
        return 0
    if existing and force:
        db.session.query(SurveyItem).filter_by(
            org_id=MASTER_ORG_ID, version=1).delete()
        print(f'--force: removed {existing} master items')

    rows = read_all_items()
    for sort_order, row in enumerate(_interleaved(rows), start=1):
        db.session.add(SurveyItem(
            org_id=MASTER_ORG_ID, version=1, sort_order=sort_order,
            anchor_type='agreement', allows_na=True, is_reverse_scored=False,
            **row,
        ))
    db.session.commit()
    print(f'seeded {len(rows)} master items')
    return len(rows)


def _interleaved(rows):
    """
    Presentation order: within a domain, ROTATE THROUGH THE SUBDOMAINS rather
    than blocking them.

    Four near-identical autonomy questions in a row invite straight-lining — the
    respondent answers the first and pattern-matches the rest. Interleaving
    breaks that. Nothing on the form names a subdomain, so interleaving costs
    the respondent nothing in coherence.

    Domains stay blocked: each is its own step of the form with its own intro.
    """
    out = []
    for domain_code in DOMAINS:
        subs = SUBDOMAINS[domain_code]
        for ordinal in range(1, ITEMS_PER_SUBDOMAIN + 1):
            for sub in subs:
                out.extend(r for r in rows
                           if r['domain_code'] == domain_code
                           and r['subdomain_code'] == sub
                           and r['ordinal'] == ordinal)
    return out


def reclone_org(org_id):
    """
    Re-clone an org that is still holding placeholder wording.

    REFUSES the moment any response points at one of its items. At that point
    the frozen copy is EVIDENCE: it is what those respondents actually read, and
    replacing it would silently change the meaning of answers already given.
    """
    from app.provisioning import clone_instrument_to_org

    org = db.session.get(Org, org_id)
    if org is None:
        raise SeedError(f'no org {org_id}')

    answered = (db.session.query(SurveyResponse)
                .join(SurveyItem, SurveyResponse.item_id == SurveyItem.id)
                .filter(SurveyItem.org_id == org.id).count())
    if answered:
        raise SeedError(
            f'refusing to re-clone {org.name}: {answered} responses already '
            'point at its items. Those items are what the respondents read, '
            'and replacing them would change what their answers meant.'
        )

    db.session.query(SurveyItem).filter_by(org_id=org.id).delete()
    n = clone_instrument_to_org(org.id)
    db.session.commit()
    print(f're-cloned {n} items into {org.name}')
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--force', action='store_true',
                    help='replace the MASTER instrument (never a client clone)')
    ap.add_argument('--reclone-org', metavar='ORG_ID',
                    help='re-clone an org that holds no responses')
    args = ap.parse_args()

    app = create_app(os.environ.get('FLASK_ENV', 'development'))
    with app.app_context():
        try:
            if args.reclone_org:
                reclone_org(args.reclone_org)
            else:
                seed_master(force=args.force)
        except SeedError as exc:
            db.session.rollback()
            print(f'SEED FAILED: {exc}', file=sys.stderr)
            sys.exit(1)


if __name__ == '__main__':
    main()
