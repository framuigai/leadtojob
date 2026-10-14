"""
The anonymous survey blueprint.

No login, no leadTo chrome, no internal keys in the rendered HTML. Everything a
respondent ever sees is served from here, and it is the one blueprint whose
templates are covered by tests/test_privacy_and_wording.py on every push.
"""
from flask import Blueprint

survey_bp = Blueprint('survey', __name__)

from app.survey import routes  # noqa: E402, F401
