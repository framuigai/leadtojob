import logging
import logging.handlers
import os
import sys

from flask import Flask, current_app, has_request_context, request, url_for
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


def create_app(config_name=None):
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')

    app = Flask(__name__)
    # x_proto/x_host keep url_for() and redirects on https and on
    # jobs.leadto.co.ke rather than the service's internal origin.
    #
    # NOTE x_for=0, UNLIKE THE SIBLING PRODUCTS. They trust one hop of
    # X-Forwarded-For so request.remote_addr is the real visitor IP, which they
    # need for assessor-side diagnostics. This application has no use for a
    # respondent's IP and deliberately no place to put one: see
    # models.ResponseSession. Not resolving it is one fewer way for it to end up
    # in a log line, a flash message or an audit payload by accident.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_for=0)

    from config import config
    app.config.from_object(config.get(config_name, config['default']))

    # Before anything else touches app.logger: Flask installs its own default
    # handler on first access if none is reachable, and that handler is only
    # attached when DEBUG is on.
    _configure_logging(app)

    _check_required_config(app, config_name)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    login_manager.login_view = 'auth.login'

    @login_manager.user_loader
    def load_user(user_id):
        from app.models import User
        user = db.session.get(User, user_id)
        # Deactivated mid-session: end the session immediately rather than
        # waiting for the next login attempt.
        if user is not None and not user.is_active:
            return None
        return user

    from app.auth import auth as auth_bp
    app.register_blueprint(auth_bp)

    from app.admin import admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')

    from app.org import org_bp
    app.register_blueprint(org_bp, url_prefix='/org')

    # The anonymous survey. No login, no leadTo chrome, and CSRF-exempt
    # endpoints are declared inside the blueprint — not here.
    from app.survey import survey_bp
    app.register_blueprint(survey_bp, url_prefix='/survey')

    _register_template_globals(app)
    _register_error_handlers(app)

    return app


def external_url(endpoint, **values):
    """
    Build an absolute URL on the CANONICAL host, not on the host being browsed.

    Every link that leaves this system — a survey link pasted into an all-staff
    email, a sign-in URL handed to a new administrator — must carry
    jobs.leadto.co.ke. `url_for(..., _external=True)` cannot guarantee that: it
    builds from the request's Host header, so an admin who happens to be on
    https://leadtojobsat.onrender.com (which stays reachable, and always will)
    copies a link carrying THAT hostname and sends it to a whole workforce. The
    link works, which is why the mistake survives review — it is a branding and
    trust failure, not a 404, and nothing in the app would ever flag it.

    On this product it is worse than branding. A survey link is the one thing an
    employee is asked to trust before answering honestly about their employer,
    and a link to a hostname nobody recognises is a link people do not click.

    So the host comes from configuration (SURVEY_BASE_URL) rather than from
    whoever happens to be browsing. Production refuses to boot without it — see
    _check_required_config — because the fallback below would otherwise mint
    http://localhost:5000 links silently.
    """
    base = (current_app.config.get('SURVEY_BASE_URL') or '').rstrip('/')
    if not base:
        return url_for(endpoint, _external=True, **values)
    return base + url_for(endpoint, **values)


def _register_template_globals(app):
    """
    Framework vocabulary, available to every template.

    Unlike promo, the framework here IS client-facing: GEM / TID / BIT / PIE and
    the twelve subdomain names are the product's own words (spec §4, §18). What
    these globals exist to prevent is different — a SECOND COPY of a label, a
    definition or an anchor string. trainiq's confidence scale drifted between
    two private copies and reports mislabelled every answer by one step.

    Note what is NOT exposed: nothing that would let a template invent a band
    label while domains.BANDS_VALIDATED is False (spec §10.5).
    """
    from app import domains
    from app.permissions import ROLE_LABELS, can

    app.jinja_env.globals.update(
        can=can,
        # Use this, NOT url_for(_external=True), for any URL a human will be
        # given outside the app. See external_url() for why.
        external_url=external_url,
        role_label=lambda r: ROLE_LABELS.get(r, r),
        DOMAINS=domains.DOMAINS,
        OUTCOME_DOMAIN=domains.OUTCOME_DOMAIN,
        DIAGNOSTIC_DOMAINS=domains.DIAGNOSTIC_DOMAINS,
        domain_label=lambda d: domains.DOMAIN_LABELS.get(d, d),
        domain_definition=lambda d: domains.DOMAIN_DEFINITIONS.get(d, ''),
        domain_scope_note=lambda d: domains.DOMAIN_SCOPE_NOTES.get(d, ''),
        subdomains_of=lambda d: domains.SUBDOMAINS.get(d, []),
        subdomain_label=domains.subdomain_label,
        subdomain_definition=domains.subdomain_definition,
        anchor_labels=domains.anchor_labels,
        # Single source of truth for the N/A wording — a template that restates
        # it drifts. And N/A is NOT a neutral score (spec §9.1): it is never
        # rendered inside the 1-5 radio row as a sixth option.
        not_applicable_label=domains.NOT_APPLICABLE_LABEL,
        not_applicable_short=domains.NOT_APPLICABLE_SHORT,
        # The constants that keep the reports honest. One copy each, so a new
        # template or chart cannot omit one by forgetting it exists.
        BANDS_VALIDATED=domains.BANDS_VALIDATED,
        BANDS_DEFERRED_NOTE=domains.BANDS_DEFERRED_NOTE,
        ASSOCIATION_CAVEAT=domains.ASSOCIATION_CAVEAT,
        PERCEPTION_CAVEAT=domains.PERCEPTION_CAVEAT,
        TREND_CAVEAT=domains.TREND_CAVEAT,
        DAUNTING_CAVEAT=domains.DAUNTING_CAVEAT,
        INTERVENTION_MAP_NOTE=domains.INTERVENTION_MAP_NOTE,
    )


def _check_required_config(app, config_name):
    """
    Refuse to start in production without the settings the app cannot work
    without.

    Flask-SQLAlchemy does not validate the URI at init_app — it raises on first
    use. Without this, a deploy missing DATABASE_URL boots "successfully",
    passes a health check against a page that happens not to touch the
    database, and is reported Live while every real action returns 500. A
    process that refuses to start is far easier to diagnose than one that lies
    about being healthy.

    Development is deliberately exempt: a missing URL there is usually someone
    working on templates, and the failure is immediate and obvious anyway.
    """
    if config_name != 'production':
        return

    missing = [
        name for name, value in (
            ('DATABASE_URL', app.config.get('SQLALCHEMY_DATABASE_URI')),
            ('FLASK_SECRET_KEY', os.environ.get('FLASK_SECRET_KEY')),
        ) if not value
    ]
    if missing:
        raise RuntimeError(
            'Refusing to start in production: '
            + ', '.join(missing)
            + ' not set. Set these in the hosting environment '
              '(Render > Environment); they are secrets and are never '
              'committed to the repository.'
        )

    # SURVEY_BASE_URL is the canonical public origin, and external_url() builds
    # every outbound link from it. Its default is http://localhost:5000, which
    # in production would not fail — it would quietly print survey links to
    # localhost into the admin UI, where they look plausible and are pasted
    # into an all-staff email. The failure surfaces days later, as a wave with
    # a zero response rate that nobody can explain.
    base_url = (app.config.get('SURVEY_BASE_URL') or '').strip()
    if not base_url or 'localhost' in base_url or '127.0.0.1' in base_url:
        raise RuntimeError(
            'Refusing to start in production: SURVEY_BASE_URL is '
            f'{base_url!r}. Set it to the canonical public origin '
            '(https://jobs.leadto.co.ke) in Render > Environment. Every '
            'survey and sign-in link is built from it.'
        )
    if not base_url.startswith('https://'):
        raise RuntimeError(
            'Refusing to start in production: SURVEY_BASE_URL must be '
            f'https://, got {base_url!r}. SESSION_COOKIE_SECURE is True, so an '
            'http:// link would drop the session cookie and fail to sign in.'
        )

    # A default secret key in production means every session cookie is
    # forgeable by anyone who has read the source.
    secret = app.config.get('SECRET_KEY') or ''
    if secret == 'dev-only-insecure-key':
        raise RuntimeError(
            'Refusing to start in production: FLASK_SECRET_KEY is still the '
            'development default. Generate one with '
            'python -c "import secrets; print(secrets.token_hex(32))".'
        )

    # Not merely weak — PUBLISHED. The generator command appears verbatim in
    # .env.example, in CLAUDE.md and in the error message above, so pasting the
    # COMMAND into the dashboard instead of its OUTPUT yields a "key" that is
    # printed in a repository. Every session cookie would then be forgeable for
    # any user, including the L1 master admin: authentication bypassed
    # completely, with nothing in the logs to show for it. This has happened
    # once on the sibling project.
    #
    # Checked by shape rather than by blocklist: a real key is a long opaque
    # token with no whitespace. Anything with a space in it is prose or a shell
    # command, never a secret.
    if 'secrets.token_hex' in secret or 'python' in secret.lower():
        raise RuntimeError(
            'Refusing to start in production: FLASK_SECRET_KEY looks like the '
            'generator COMMAND rather than its OUTPUT. Run it and paste the '
            '64-character hex result:\n'
            '    python -c "import secrets; print(secrets.token_hex(32))"'
        )
    if len(secret) < 32 or any(c.isspace() for c in secret):
        raise RuntimeError(
            'Refusing to start in production: FLASK_SECRET_KEY must be at '
            f'least 32 characters with no whitespace (got {len(secret)} '
            'characters). It signs every session cookie.'
        )


class _RequestContextFilter(logging.Filter):
    """
    Attach request identity to every record.

    A traceback without the tenant and the path it came from is close to
    useless on a multi-tenant deployment — you know something broke, but not
    for whom or where. These attributes are always set (to '-' outside a
    request) so the formatter never raises on a background or startup record.

    THERE IS NO ip FIELD HERE, unlike the sibling products. The survey routes
    are reached by anonymous respondents, and a formatter that stamps a client
    IP onto every record would write an identifier into the one place it is
    hardest to notice and easiest to retain: the log file. Spec §13.2 is data
    minimisation, and a log line is data.
    """

    def filter(self, record):
        record.method = '-'
        record.path = '-'
        record.user = '-'
        record.org = '-'

        if has_request_context():
            record.method = request.method
            record.path = request.path
            # Imported lazily and defensively: this filter runs on records
            # emitted before login_manager is ready, and during teardown after
            # a failed request, where current_user can raise rather than simply
            # being anonymous.
            try:
                from flask_login import current_user
                if current_user and current_user.is_authenticated:
                    record.user = str(current_user.id)
                    record.org = str(getattr(current_user, 'org_id', '-') or '-')
            except Exception:                                # pragma: no cover
                pass

        return True


def _configure_logging(app):
    """
    Give the application a logging destination it owns.

    Without this, records fall through to the stdlib last-resort handler:
    WARNING and above only, unformatted, on stderr — so whether a production
    traceback survives depends entirely on how gunicorn was invoked.

    Handlers go on the root logger so third-party records land in the same
    place. Root stays at WARNING and only the 'app' tree is opened up to
    LOG_LEVEL, so raising the app to DEBUG doesn't drown the log in library
    chatter.
    """
    level = getattr(logging, app.config.get('LOG_LEVEL', 'INFO'), logging.INFO)

    formatter = logging.Formatter(
        '%(asctime)s %(levelname)-8s [%(name)s] %(message)s '
        '| %(method)s %(path)s user=%(user)s org=%(org)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    context_filter = _RequestContextFilter()

    root = logging.getLogger()

    # create_app runs once per test in the suite; without this the same file
    # gets a new handler each time and records multiply.
    for handler in list(root.handlers):
        if getattr(handler, '_leadto', False):
            root.removeHandler(handler)
            handler.close()

    handlers = []

    if app.config.get('LOG_TO_FILE', True):
        log_dir = app.config['LOG_DIR']
        try:
            os.makedirs(log_dir, exist_ok=True)
            handlers.append(logging.handlers.RotatingFileHandler(
                os.path.join(log_dir, app.config['LOG_FILENAME']),
                maxBytes=app.config['LOG_MAX_BYTES'],
                backupCount=app.config['LOG_BACKUP_COUNT'],
                encoding='utf-8',
            ))
        except OSError as exc:
            # A read-only or missing log volume must not stop the app booting;
            # stderr below still carries the records.
            print(f'WARNING: file logging disabled ({exc})', file=sys.stderr)

    if app.config.get('LOG_TO_STDERR', True) or not handlers:
        handlers.append(logging.StreamHandler(sys.stderr))

    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(context_filter)
        handler._leadto = True
        root.addHandler(handler)

    root.setLevel(logging.WARNING)
    logging.getLogger('app').setLevel(level)

    # Flask consults this to decide whether to install its own default handler.
    app.logger.setLevel(level)


def _register_error_handlers(app):
    """
    Friendly 404/500 pages plus server-side logging of the traceback.

    Dormant while DEBUG=True: Flask propagates exceptions to the interactive
    debugger instead of calling the 500 handler. It exists so that turning
    DEBUG off doesn't leave the app without diagnostics.
    """
    from flask import render_template

    @app.errorhandler(404)
    def _not_found(_error):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    @app.errorhandler(Exception)
    def _server_error(error):
        # Re-raise HTTP errors (403, 405 ...) so their own handlers and status
        # codes still apply — only genuine faults reach the 500 page.
        from werkzeug.exceptions import HTTPException
        if isinstance(error, HTTPException):
            return error

        # In debug, hand the exception back so the interactive debugger still
        # opens. Flask consults a registered Exception handler BEFORE
        # PROPAGATE_EXCEPTIONS, so without this the mere existence of this
        # handler would silently replace the traceback with the 500 page.
        if app.debug:
            raise error

        # A failed request usually leaves the session in a broken transaction;
        # without this rollback every later query on this connection fails too.
        try:
            db.session.rollback()
        except Exception:                                    # pragma: no cover
            app.logger.exception('Rollback failed during 500 handling')

        app.logger.exception('Unhandled exception: %s', error)
        return render_template('errors/500.html'), 500
