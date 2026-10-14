"""
Shared fixtures.

Anything that needs a real database is gated on TEST_DATABASE_URL and SKIPS
when it is unset — never falls back to DATABASE_URL. See TestingConfig in
config.py for the whole reasoning; the short version is that this fixture calls
drop_all().
"""
import os

import pytest
from flask import g

from app import create_app, db as _db


@pytest.fixture(scope='session')
def app():
    application = create_app('testing')
    return application


@pytest.fixture
def client(app):
    """A request client with no database. Enough for template/route rendering."""
    return app.test_client()


@pytest.fixture
def db(app):
    """
    A real Postgres database, or a skip. Tables are created and dropped per test.

    Gates on the environment variable itself, not on SQLALCHEMY_DATABASE_URI —
    that config value carries a placeholder SQLite URL so the app can be
    constructed for template tests, and SQLite cannot host this schema (ARRAY,
    JSONB, a computed column and a partial unique index are all Postgres-only).
    """
    test_url = os.environ.get('TEST_DATABASE_URL')
    if not test_url:
        pytest.skip('TEST_DATABASE_URL is not set — skipping DB-backed test.')

    # HARD STOP. This fixture calls drop_all(). Pointed at the live database it
    # would destroy every organisation, cycle and survey response in the
    # system, and there is no undo — anonymous responses cannot be recollected
    # from the people who gave them, because the system does not know who they
    # were. That is the design working as intended, and it is precisely what
    # makes an accidental drop unrecoverable.
    #
    # The config-level rule (never fall back to DATABASE_URL) prevents the
    # accident of omission; this prevents the accident of a copy-paste into the
    # wrong .env line.
    if test_url.strip() == (os.environ.get('DATABASE_URL') or '').strip():
        pytest.fail(
            'TEST_DATABASE_URL is identical to DATABASE_URL. Refusing to run: '
            'this fixture drops every table. Point it at a separate database.'
        )
    with app.app_context():
        _db.create_all()
        yield _db
        _db.session.remove()
        _db.drop_all()


def login(client, user):
    """
    Sign `client` in as `user`, for a test that switches identity midway.

    Setting session['_user_id'] alone is not enough HERE, though it is in a
    single-login test. The `db` fixture pushes ONE app context around the whole
    test, and Flask requests reuse an already-pushed app context for the same
    app rather than creating another — so `g` is shared across every request the
    test makes, and Flask-Login's `g._login_user` cache from the first request
    is still there on the second. Without clearing it, a second login is
    silently ignored and the test keeps running as the first user, which makes
    an authorisation test PASS FOR THE WRONG REASON.

    That is the worst class of bug a suite can have: a green branch-scoping
    test that never actually changed user. This is an artefact of the fixture,
    not of the app — in production every request pushes its own app context and
    `g` starts empty.
    """
    with client.session_transaction() as session:
        session['_user_id'] = str(user.id)
        session['_fresh'] = True
    g.pop('_login_user', None)
