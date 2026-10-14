"""
Create the L1 master administrator.

    python seed_admin.py                      # uses SEED_ADMIN_* from .env
    python seed_admin.py --email a@b.c --password ...

L1 has NO org_id and NO branch_id — it is the platform operator, above every
tenant. That is also why L1-only capabilities live in app/admin/: every query in
app/org/ is scoped by current_user.org_id, which L1 can never satisfy.

Idempotent: re-running reports the existing account rather than creating a
second one or resetting a password by surprise. Use --reset-password to change
one deliberately.
"""
import argparse
import os
import sys

from dotenv import load_dotenv

# Loaded HERE, not left to config.py. argparse evaluates its defaults from
# os.environ at parse time, which happens before create_app() imports config and
# calls load_dotenv() — so without this the --email/--password defaults are
# always None and the script reports missing arguments that are sitting in .env.
load_dotenv()

from app import create_app, db  # noqa: E402
from app.models import User
from app.permissions import MASTER_ADMIN

# Rejected outright in a non-development environment. The point is not that
# these are weak — it is that they are PUBLISHED, in this file and in the
# project's documentation, so an L1 account carrying one is open to anyone who
# has read the repository. L1 sees every tenant.
_PUBLISHED_PASSWORDS = {'changeme123', 'password', 'admin', 'changeme'}


def seed_admin(email, password, full_name='Master Admin', reset_password=False):
    email = email.strip().lower()
    existing = db.session.query(User).filter_by(email=email).first()

    if existing is not None:
        if not reset_password:
            print(f'{email} already exists (role={existing.role}) — unchanged. '
                  'Use --reset-password to change its password.')
            return existing
        existing.set_password(password)
        existing.is_active = True
        db.session.commit()
        print(f'reset the password for {email}')
        return existing

    user = User(email=email, role=MASTER_ADMIN, full_name=full_name,
                org_id=None, branch_id=None, is_active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    print(f'created L1 master admin {email}')
    return user


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--email', default=os.environ.get('SEED_ADMIN_EMAIL'))
    ap.add_argument('--password', default=os.environ.get('SEED_ADMIN_PASSWORD'))
    ap.add_argument('--name', default=os.environ.get('SEED_ADMIN_NAME', 'Master Admin'))
    ap.add_argument('--reset-password', action='store_true')
    args = ap.parse_args()

    if not args.email or not args.password:
        print('Need --email and --password (or SEED_ADMIN_EMAIL / '
              'SEED_ADMIN_PASSWORD in .env).', file=sys.stderr)
        sys.exit(2)

    env = os.environ.get('FLASK_ENV', 'development')
    if env != 'development' and args.password.lower() in _PUBLISHED_PASSWORDS:
        print(f'Refusing to set a published default password in {env}. '
              'This one appears verbatim in seed_admin.py, so an L1 account '
              'carrying it is open to anyone who has read the repository — and '
              'L1 sees every tenant.', file=sys.stderr)
        sys.exit(1)

    app = create_app(env)
    with app.app_context():
        seed_admin(args.email, args.password, args.name, args.reset_password)
        if args.password.lower() in _PUBLISHED_PASSWORDS:
            print('\n  NOTE: this password is a published default and is fine '
                  'for local development only.\n  Change it before this '
                  'database is reachable by anyone else.')


if __name__ == '__main__':
    main()
