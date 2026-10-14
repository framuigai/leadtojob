"""
The anonymous survey. Phase 5 builds this; phase 1 reserves the shape.

WHAT THIS BLUEPRINT MUST NEVER DO, written down before there is any code to
break the rule:

  * never require a login, and never offer one;
  * never store or resolve an identity — no email, no employee number, no IP,
    no user-agent, no cookie that could be correlated back to a person. The
    session row it creates has nowhere to put any of those (see
    models.ResponseSession);
  * never render `item_code`, `subdomain_code`, `domain_code` or any other
    internal key into the HTML. The respondent must not be able to infer which
    construct an item serves — which changes how people answer;
  * never show leadTo chrome, the navbar, or any "tracked" / "monitored"
    wording. The page belongs to the respondent's employer's survey, not to a
    vendor's product tour;
  * never present "Not applicable / Unable to assess" as a sixth option inside
    the 1-5 radio row. It is a deliberate second click (spec §9.1: it is not a
    neutral score), otherwise it becomes the lazy answer to 48 questions.

The landing page carries the spec §9.2 disclosures BEFORE the first question:
purpose, how results will be used, who sees what, the minimum reporting
threshold, the anonymity claim stated honestly for the delivery mode, the
non-retaliation commitment, and what the organisation intends to do next. That
is a specification requirement, not a nicety — it is the thing that makes the
responses worth having.
"""
from flask import abort

from app.survey import survey_bp


@survey_bp.route('/<slug>')
def landing(slug):
    """
    The one anonymous link.

    404 for everything in phase 1 — deliberately the same response an unknown
    or closed slug will get in phase 5, so there is never a probe that
    distinguishes "no such wave" from "wave not open yet".
    """
    abort(404)
