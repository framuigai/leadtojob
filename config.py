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
    #
    # SET TO 1 IN EVERY ENVIRONMENT, INCLUDING PRODUCTION, ON REQUEST.
    #
    # AT 1 THERE IS NO SUPPRESSION. A group of one reports, so a breakdown by
    # a segment that happens to contain one person shows that person's own
    # answers to whoever reads the report — their manager included. The
    # differencing defence (suppressing the next-smallest cell alongside a
    # suppressed one) never fires either, because nothing is ever suppressed
    # to begin with.
    #
    # It used to be 5 in production and 1 in development and testing, split
    # deliberately so that a threshold convenient for a local walkthrough
    # could not reach a real workforce by inheritance. That split is gone; 1
    # is now the intended production value, not a leftover.
    #
    # Still a FLOOR, and still one-directional: a cycle may raise its own
    # min_reporting_n above this and never below it, and LOWER_MIN_N remains
    # held by no role. At a floor of 1 that guarantee protects nothing on its
    # own, because there is nothing below 1 to be lowered to.
    #
    # ALSO NOTE: app/templates/survey/landing.html no longer renders any claim
    # about the threshold — the section that did was removed. So respondents
    # are not told that a result may rest on their answers alone. Previously
    # the form said so in those words whenever this value was below 2.
    #
    # ASSOCIATION OUTPUTS ARE NOT AFFECTED. association.contrast_floor() takes
    # max(ASSOCIATION_MIN_N, 3 x min_reporting_n), so ASSOCIATION_MIN_N still
    # binds there independently of this value.
    MIN_REPORTING_N = int(os.environ.get('MIN_REPORTING_N', 1))

    # The floor for ANY association output (app/analytics/association.py).
    #
    # Originally 30, for a correlation coefficient. There is no coefficient:
    # the specification never asks for one — "correlation", "Pearson" and
    # "coefficient" appear nowhere in it — and §10.5 asks for "scores,
    # response distributions, comparisons and limitations" during early
    # implementation. The engine computes tertile contrasts and segment
    # comparisons, which answer §10.4's four questions in its own words.
    #
    # 12 is the floor for a CONTRAST, which splits a group into thirds: below
    # it, each third is too small to be a comparison. The binding floor is
    # max(this, 3 x min_reporting_n), because every third is itself a reported
    # group — see association.contrast_floor().
    ASSOCIATION_MIN_N = int(os.environ.get('ASSOCIATION_MIN_N', 12))


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'DEBUG').upper()

    # 1 locally, so a hand-made fixture of two or three responses produces a
    # visible number instead of a wall of "Suppressed". Walking the reports
    # with 5 real respondents to set up is friction that gets in the way of
    # actually looking at them.
    #
    # This is a LOCAL convenience and nothing else. At 1 a "group result" is
    # one person's answer sheet with a different heading, and the survey form
    # says so plainly while the threshold is this low.
    MIN_REPORTING_N = int(os.environ.get('MIN_REPORTING_N', 1))


class TestingConfig(Config):
    TESTING = True
    DEBUG = False
    LOG_TO_FILE = False
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'WARNING').upper()

    # 1, matching development, so route-level tests can assert on real numbers
    # from small fixtures. The SUPPRESSION tests do not rely on this: they pass
    # an explicit threshold to the scoring functions, because a suppression
    # rule that only works at the configured default is a rule nobody has
    # actually tested.
    MIN_REPORTING_N = int(os.environ.get('MIN_REPORTING_N', 1))

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

    # 1 IN PRODUCTION, ON REQUEST. See the base class for what that means.
    #
    # This used to be 5, and the comment here used to warn against the local
    # testing value arriving in production by accident. It is no longer an
    # accident: 1 is the intended value. app/__init__.py still logs a warning
    # at boot whenever this is below 3, which is now expected rather than a
    # signal — it is left in place so the choice is at least visible in the
    # logs of every production start.
    MIN_REPORTING_N = int(os.environ.get('MIN_REPORTING_N', 1))

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
