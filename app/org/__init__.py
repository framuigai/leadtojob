from flask import Blueprint

org_bp = Blueprint('org', __name__)

from app.org import routes  # noqa: E402, F401
