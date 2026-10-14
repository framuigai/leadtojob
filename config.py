import os

from dotenv import load_dotenv

load_dotenv()

_BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'dev-only-insecure-key')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Logging -----------------------------------------------------------
    # Configured in app/__init__.py::_configure_logging. The destination is
    # owned here rather than by the gunicorn command line, so that turning
    # DEBUG off never leaves faults with nowhere to go regardless of how the
    # process is launched.
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
    LOG_DIR = os.environ.get('LOG_DIR', os.path.join(_BASE_DIR, 'logs'))
    LOG_FILENAME = os.environ.get('LOG_FILENAME', 'jobsat.log')
    LOG_TO_FILE = os.environ.get('LOG_TO_FILE', 'True').lower() == 'true'
    LOG_TO_STDERR = os.environ.get('LOG_TO_STDERR', 'True').lower() == 'true'
    LOG_MAX_BYTES = int(os.environ.get('LOG_MAX_BYTES', 10 * 1024 * 1024))
    LOG_BACKUP_COUNT = int(os.environ.get('LOG_BACKUP_COUNT', 10))

    # 12 hours. 48 Likert items is an 8-11 minute form (plan §4.2) but a
    # respondent may leave the tab open across a working day. Save-and-resume
    # posts per section, so each POST refreshes the token; this ceiling only
    # bites on a form left open from the very first section.
    WTF_CSRF_TIME_LIMIT = 43200

    # The canonical public origin. external_url() builds every anonymous survey
    # link from this and never from the request Host header.
    SURVEY_BASE_URL = os.environ.get('SURVEY_BASE_URL', 'http://localhost:5000')

    # ── The two privacy constants ──────────────────────────────────────────
    #
    # MIN_REPORTING_N (spec §9.2, §13.5-13.6) is the platform floor. A cycle
    # may raise its own min_reporting_n above this and NEVER below it; the
    # LOWER_MIN_N capability exists in app/permissions.py precisely so that it
    # can be granted to nobody. Enforced in app/analytics/, never in a template
    # — a template-level check is one {% if %} away from being forgotten in the
    # next report.
    MIN_REPORTING_N = int(os.environ.get('MIN_REPORTING_N', 5))

    # Below this many sessions an association coefficient is not reported
    # (plan §5.2). The tertile contrast still is: it is robust at small n and
    # readable by a non-statistical audience.
    ASSOCIATION_MIN_N = int(os.environ.get('ASSOCIATION_MIN_N', 30))


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'DEBUG').upper()


class TestingConfig(Config):
    TESTING = True
    DEBUG = False
    LOG_TO_FILE = False
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'WARNING').upper()

    # TEST_DATABASE_URL ONLY — NEVER fall back to DATABASE_URL.
    #
    # Inherited rule, learned the hard way on trainiq: DATABASE_URL points at
    # the hosted database, so a fallback would aim a fixture that creates
    # tables and writes rows at live client data. Here the stakes are higher
    # still — the rows are anonymous employee survey responses that cannot be
    # recollected.
    #
    # The in-memory SQLite default is a PLACEHOLDER so the app can be
    # constructed for template and route tests. It is NOT a usable target for
    # this schema (ARRAY, JSONB, Computed and a partial unique index are all
    # Postgres-only). The DB-backed fixtures in tests/conftest.py gate on the
    # TEST_DATABASE_URL environment variable itself and skip when it is unset,
    # rather than inspecting this value. Do not "helpfully" point this at
    # DATABASE_URL.
    SQLALCHEMY_DATABASE_URI = os.environ.get('TEST_DATABASE_URL') or 'sqlite://'

    WTF_CSRF_ENABLED = False

    # bcrypt at the production cost factor dominates the runtime of any test
    # that logs a user in. models.User.set_password hardcodes rounds=12, so
    # tests/conftest.py patches it down; this flag records the intent.
    BCRYPT_ROUNDS = 4


class ProductionConfig(Config):
    # Explicit rather than inherited, so a future DEBUG on the base Config
    # cannot silently switch on the interactive debugger in production.
    DEBUG = False
    TESTING = False

    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')

    # Supabase (and any pooler in front of Postgres) closes idle connections,
    # but a sync gunicorn worker holds its pooled connections open between
    # requests. Without pre_ping the first request after a quiet period checks
    # out a socket the server already closed and fails with "SSL connection has
    # been closed unexpectedly" — once, then recovers, which reads as random
    # flakiness rather than a configuration problem.
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'pool_size': 5,
        'max_overflow': 5,
        'pool_timeout': 30,
    }

    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'


config = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig,
}
