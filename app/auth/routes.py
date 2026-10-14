"""
Login, logout, and the route gates every blueprint uses.

The gates live here rather than in permissions.py so that module stays pure
Python with no Flask import — it must be unit-testable without an app context.
"""
from datetime import datetime, timezone
from functools import wraps

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app import db
from app.auth import auth
from app.models import User
from app.permissions import MASTER_ADMIN, can


def home_endpoint(user) -> str:
    """The dashboard a role lands on after login or a failed role check."""
    if getattr(user, 'role', None) == MASTER_ADMIN:
        return 'admin.dashboard'
    return 'org.dashboard'


def _reject(*, endpoint_is_home: bool):
    """
    Send a wrongly-roled user somewhere useful.

    Bounces to the user's own home dashboard — unless THIS route is that
    dashboard, in which case redirecting would loop forever. Then 403 instead.
    """
    if endpoint_is_home:
        abort(403)
    return redirect(url_for(home_endpoint(current_user)))


def _gate(check):
    """Shared body for the role/capability decorators below."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('auth.login'))
            if not check(current_user):
                return _reject(
                    endpoint_is_home=(request.endpoint == home_endpoint(current_user))
                )
            return f(*args, **kwargs)
        return decorated
    return decorator


def require_role(role):
    """Gate a route to a single role."""
    return _gate(lambda u: u.role == role)


def require_role_any(*roles):
    """Gate a route to any of the listed roles."""
    return _gate(lambda u: u.role in roles)


def require_capability(capability):
    """
    Gate a route on a capability from app/permissions.py.

    Prefer this over require_role for anything the three tiers share unevenly —
    it keeps the role matrix in one table instead of spreading role names
    through the routes.

    Note that a capability held by NO ROLE is a permanent 403 by construction.
    permissions.LOWER_MIN_N is exactly that, deliberately: see its definition.
    """
    return _gate(lambda u: can(u, capability))


@auth.route('/')
def root():
    """
    The application's front door.

    This app is deployed on its own subdomain and carries no marketing site, so
    `/` has no landing page: a signed-out visitor goes to the login form, a
    signed-in one straight to their dashboard. Kept as a redirect rather than
    rendering login at `/` so there is exactly one URL for the form and
    url_for('auth.login') stays the single reference to it.

    Note that a RESPONDENT never arrives here. They reach /survey/<slug>
    directly and are never asked to sign in or shown this page.
    """
    if current_user.is_authenticated:
        return redirect(url_for(home_endpoint(current_user)))
    return redirect(url_for('auth.login'))


@auth.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for(home_endpoint(current_user)))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = db.session.query(User).filter_by(email=email).first()
        if user and user.check_password(password):
            if not user.is_active:
                flash('This account has been deactivated. Contact your administrator.',
                      'error')
                return render_template('auth/login.html')
            login_user(user)
            user.last_login_at = datetime.now(timezone.utc)
            db.session.commit()
            return redirect(url_for(home_endpoint(user)))
        # Deliberately one message for both cases — never reveal whether an
        # address is registered.
        flash('Invalid email or password.', 'error')

    return render_template('auth/login.html')


@auth.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))
