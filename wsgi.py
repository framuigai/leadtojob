"""
Alternative production WSGI target.

    python -m gunicorn wsgi:app --timeout 120

Kept because some hosts and process managers expect a module named `wsgi`.
`run:app` is the one actually configured in render.yaml, matching the other two
leadTo services so one operator can move between them without re-reading the
runbook.

This module is never a development entrypoint — it has no `app.run()` — so it
defaults to production unconditionally. `run.py` carries the reasoning; the
short version is that a missing FLASK_ENV must never serve the public internet
with DEBUG=True.
"""
import os

from app import create_app

app = create_app(os.environ.get('FLASK_ENV', 'production'))
