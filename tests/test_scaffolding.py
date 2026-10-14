"""
Phase 1's acceptance test.

    python run.py serves login locally; / redirects; unauthenticated /admin/
    bounces to login; the suite is green in CI.

No database: these exercise the app factory, the routing and the gates.
"""
import pytest

from app import create_app, external_url


# ── The front door ──────────────────────────────────────────────────────────

def test_root_redirects_to_login(client):
    res = client.get('/')
    assert res.status_code == 302
    assert '/login' in res.headers['Location']


def test_login_page_renders(client):
    res = client.get('/login')
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert 'Sign in' in body
    assert 'Job Satisfaction Analysis' in body


def test_login_rejects_a_bad_password_without_saying_which_part_was_wrong(db, client):
    """
    One message for both cases — never reveal whether an address is registered.

    Takes the `db` fixture because a POST to /login queries `users`, so this
    one skips without TEST_DATABASE_URL and runs in CI against Postgres 16.
    """
    res = client.post('/login',
                      data={'email': 'nobody@example.com', 'password': 'x'},
                      follow_redirects=True)
    body = res.get_data(as_text=True)
    assert 'Invalid email or password.' in body
    assert 'not registered' not in body.lower()
    assert 'no such user' not in body.lower()


# ── The gates ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize('path', [
    '/admin/',
    '/admin/templates',
    '/admin/catalogue',
    '/admin/users',
    '/admin/usage',
    '/admin/audit',
    '/admin/validation',
    '/admin/orgs/new',
    '/org/',
    '/org/cycles',
    '/org/segments',
    '/org/deployments',
    '/org/branches',
    '/org/reports',
])
def test_every_admin_route_bounces_an_anonymous_visitor_to_login(client, path):
    """
    Parametrised over every route rather than a sample, so a NEW screen added
    without a gate fails here rather than shipping open. That is the failure
    mode worth testing: nobody forgets to gate the dashboard, they forget to
    gate the eighth tab.
    """
    res = client.get(path)
    assert res.status_code == 302, path
    assert '/login' in res.headers['Location'], path


# ── The anonymous survey ────────────────────────────────────────────────────

def test_an_unknown_survey_slug_is_a_plain_404(db, client):
    """
    An unknown slug, an archived wave and a wave whose cycle is not open all
    return the SAME 404. A probe must never be able to distinguish "no such
    wave" from "wave not open yet": the difference confirms that a particular
    organisation is running a satisfaction survey, which is information about
    that employer nobody outside needs.

    (A wave that WAS open and has since closed is the one exception — see
    tests/test_survey_form.py. There the visitor is probably a real employee
    following a real link, and 404ing them is unkind for no gain.)

    Takes `db` because the route now resolves the slug against the database.
    """
    res = client.get('/survey/does-not-exist')
    assert res.status_code == 404


def test_the_survey_route_does_not_redirect_to_a_login(db, client):
    """
    A respondent is never asked who they are. If the survey blueprint ever
    acquires a login_required, this is what catches it.
    """
    res = client.get('/survey/does-not-exist')
    assert res.status_code != 302
    assert 'login' not in res.headers.get('Location', '')


# ── Error pages ─────────────────────────────────────────────────────────────

def test_404_page_renders_the_friendly_template(client):
    res = client.get('/no/such/path')
    assert res.status_code == 404
    assert 'Page not found' in res.get_data(as_text=True)


# ── Canonical links ─────────────────────────────────────────────────────────

def test_external_url_uses_the_configured_host_not_the_request_host(app):
    """
    Every survey link must carry jobs.leadto.co.ke even when the admin building
    it is browsing *.onrender.com. See app/__init__.py::external_url — the link
    works either way, which is exactly why the mistake survives review.
    """
    app.config['SURVEY_BASE_URL'] = 'https://jobs.leadto.co.ke'
    with app.test_request_context('/', base_url='https://leadtojobsat.onrender.com'):
        url = external_url('survey.landing', slug='abc123')
    assert url == 'https://jobs.leadto.co.ke/survey/abc123'
    assert 'onrender' not in url


# ── Production boot guards ──────────────────────────────────────────────────
#
# NOTE ON HOW THESE PATCH. ProductionConfig reads os.environ at CLASS-BODY
# evaluation, i.e. once, when config.py is first imported — so a
# monkeypatch.setenv('DATABASE_URL', ...) inside a test happens far too late to
# change SQLALCHEMY_DATABASE_URI, and every one of these tests would pass for
# the wrong reason (tripping the DATABASE_URL guard regardless of what it meant
# to exercise). They patch the CLASS ATTRIBUTE instead.
#
# monkeypatch.setattr restores the original after each test, so these do not
# leak into one another.

def _prod(monkeypatch, *, database_url='postgresql+psycopg2://u:p@localhost/x',
          secret='a' * 64, base_url='https://jobs.leadto.co.ke'):
    """Point ProductionConfig at a specific combination, then build the app."""
    from config import ProductionConfig
    monkeypatch.setattr(ProductionConfig, 'SQLALCHEMY_DATABASE_URI',
                        database_url, raising=False)
    monkeypatch.setattr(ProductionConfig, 'SECRET_KEY', secret, raising=False)
    monkeypatch.setattr(ProductionConfig, 'SURVEY_BASE_URL', base_url,
                        raising=False)
    # _check_required_config reads FLASK_SECRET_KEY from the environment too,
    # to tell "unset" apart from "set to the development default".
    if secret is None:
        monkeypatch.delenv('FLASK_SECRET_KEY', raising=False)
    else:
        monkeypatch.setenv('FLASK_SECRET_KEY', secret)
    return create_app('production')


def test_production_refuses_to_boot_without_a_database_url(monkeypatch):
    """
    Flask-SQLAlchemy does not validate the URI at init_app — it raises on first
    use. Without this guard a deploy missing DATABASE_URL boots
    "successfully", passes a health check against a page that happens not to
    touch the database, and is reported Live while every real action 500s.
    """
    with pytest.raises(RuntimeError, match='DATABASE_URL'):
        _prod(monkeypatch, database_url=None)


def test_production_refuses_a_localhost_survey_base_url(monkeypatch):
    """
    The failure this prevents is not a crash. It is survey links pointing at
    localhost, pasted into an all-staff email, surfacing days later as a wave
    with a zero response rate that nobody can explain.
    """
    with pytest.raises(RuntimeError, match='SURVEY_BASE_URL'):
        _prod(monkeypatch, base_url='http://localhost:5000')


def test_production_refuses_a_plain_http_survey_base_url(monkeypatch):
    """SESSION_COOKIE_SECURE is True, so an http:// link cannot sign anyone in."""
    with pytest.raises(RuntimeError, match='must be\\s+https://'):
        _prod(monkeypatch, base_url='http://jobs.leadto.co.ke')


def test_production_refuses_the_development_default_secret(monkeypatch):
    with pytest.raises(RuntimeError, match='development default'):
        _prod(monkeypatch, secret='dev-only-insecure-key')


def test_production_refuses_the_secret_key_generator_command(monkeypatch):
    """
    Pasting the COMMAND instead of its OUTPUT yields a "key" that is published
    verbatim in .env.example and CLAUDE.md. Every session cookie would then be
    forgeable for any user, including L1: authentication bypassed completely,
    with nothing in the logs to show for it. This has happened once on the
    sibling project, which is why it is checked by shape rather than trusted to
    care.
    """
    with pytest.raises(RuntimeError, match='generator COMMAND'):
        _prod(monkeypatch,
              secret='python -c "import secrets; print(secrets.token_hex(32))"')


def test_production_refuses_a_short_or_whitespaced_secret(monkeypatch):
    with pytest.raises(RuntimeError, match='at least 32 characters'):
        _prod(monkeypatch, secret='too short')


def test_production_boots_when_everything_is_set(monkeypatch):
    """
    The complement of the five guards above. Without it, a guard that became
    unconditional — refusing every production boot — would leave all of them
    green while the service could never start.
    """
    assert _prod(monkeypatch) is not None


# ── The threshold is configured, not scattered ──────────────────────────────

def test_the_reporting_threshold_has_a_sane_default(app):
    """
    5 is the common standard. It may be raised per cycle and never lowered —
    the enforcement lives in app/analytics/ and is pinned by
    test_suppression.py once the engine exists (phase 6).
    """
    assert app.config['MIN_REPORTING_N'] == 5
    assert app.config['ASSOCIATION_MIN_N'] == 30
