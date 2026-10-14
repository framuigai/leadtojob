"""
Application entrypoint — development server AND production WSGI target.

    python run.py                              # local development
    python -m gunicorn run:app --timeout 120   # production (Render)

Both work, and they pick different defaults safely.

WHY THE DEFAULT IS CONDITIONAL
------------------------------
`FLASK_ENV` normally decides the config. But if it is ever missing in the
hosting environment — unset by hand, lost in a service migration, typo'd — a
plain `os.environ.get('FLASK_ENV', 'development')` would serve the PUBLIC
INTERNET with DevelopmentConfig: DEBUG=True, the interactive Werkzeug debugger
on every exception, and full tracebacks in the browser. On this product that
debugger sits in front of anonymous employee survey responses. It is a
one-character mistake away at all times, and nothing about the running service
would look wrong until someone triggered an error.

So the default depends on HOW this module was loaded:

    python run.py       __name__ == '__main__'   -> development
    gunicorn run:app    __name__ == 'run'        -> production

A WSGI server importing this module is by definition not a developer at a
terminal, so production is the only safe assumption. An explicit FLASK_ENV
still wins in both cases.

Production additionally refuses to start without DATABASE_URL, a non-default
FLASK_SECRET_KEY and a real SURVEY_BASE_URL — see
app/__init__.py::_check_required_config.

--timeout 120 matters: report generation is synchronous, inside the request
(there is no background worker, by design), so a large PDF is built while the
request is open. Gunicorn's 30s default would kill the worker mid-render and
return a 502.
"""
import os

from app import create_app

_DEFAULT_ENV = 'development' if __name__ == '__main__' else 'production'

app = create_app(os.environ.get('FLASK_ENV', _DEFAULT_ENV))

if __name__ == '__main__':
    app.run()
