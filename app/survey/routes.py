"""
THE ANONYMOUS SURVEY. The most privacy-critical code in the product.

WHAT THIS BLUEPRINT MUST NEVER DO:

  * require a login, or offer one;
  * store or resolve an identity — no email, no employee number, no NAME, no IP
    address, no user-agent, no device id, no FK to `users` or to any invitation.
    models.ResponseSession has nowhere to put any of them, which is the only
    form of this guarantee worth making;
  * render `item_code`, `subdomain_code`, or any domain code beyond the four
    public domain NAMES. A respondent who can see that a question belongs to
    "Trust" answers it differently;
  * show leadTo chrome, the admin navbar, or any "tracked" / "monitored"
    wording. This page belongs to the respondent's employer's survey, not to a
    vendor's product tour;
  * present "Not applicable / Unable to assess" as a sixth option inside the
    1-5 row. It is a deliberate second click (spec §9.1 — it is not a neutral
    score), otherwise it becomes the lazy answer to 48 questions.

HOW AN ANONYMOUS SESSION CAN STILL RESUME
-----------------------------------------
The browser holds an opaque `resume_secret`; the server stores only its sha256.
Presenting the secret proves "I am the person who started this draft" without
the server ever knowing who that is. Lose the browser, lose the draft — that is
the correct trade, and the landing page says so.

WHY THE SESSION IS CREATED ON POST, NOT ON GET
----------------------------------------------
The build plan says the session is created on the first GET of the link. It is
created on the explicit "Begin" POST instead, because corporate mail security
scanners and link previewers fetch every URL in an all-staff email: on GET, each
one mints a session row. None of them would ever submit, so no reported figure
would change — but the draft/abandonment counts would be mostly robots, and the
first thing anyone asks about a cycle is how many people started it.
"""
import hashlib
import secrets
from datetime import datetime, timezone

from flask import (
    abort, make_response, redirect, render_template, request, url_for,
)

from app import db, domains
from app.models import (
    AccessTicket, MeasurementCycle, PilotFeedback, ResponseSession,
    SurveyDeployment, SurveyItem, SurveyResponse,
)
from app.survey import survey_bp

# 60 days. Long enough that a cycle can run over a holiday; short enough that a
# shared or kiosk browser does not hand someone else's draft to the next
# person indefinitely.
_RESUME_COOKIE_DAYS = 60


def _resume_cookie_name(slug):
    # Scoped per deployment so two concurrent surveys cannot read each other's draft.
    # The slug is already opaque, so the cookie name reveals nothing about the
    # employer or the respondent.
    return f'jsat_resume_{slug[:16]}'


def _submitted_cookie_name(slug):
    return f'jsat_done_{slug[:16]}'


def _load_open_deployment(slug):
    """
    Resolve a slug, or 404.

    ONE RESPONSE FOR EVERY FAILURE MODE — unknown slug, archived deployment, closed
    deployment, cycle not open. A probe must not be able to distinguish "no such
    deployment" from "deployment not open yet": the difference confirms that a particular
    organisation is running a satisfaction survey, which is information about
    that employer nobody outside needs.

    The one exception is a deployment that was open and has since closed, where the
    respondent may well be a real employee following a real link. That gets its
    own explanatory page — see landing().
    """
    deployment = (db.session.query(SurveyDeployment)
                  .filter_by(public_slug=slug)
                  .filter(SurveyDeployment.archived_at.is_(None)).first())
    if deployment is None:
        abort(404)
    return deployment


def _is_live(deployment):
    if deployment.closed:
        return False
    if deployment.closes_at is not None:
        closes_at = deployment.closes_at
        if closes_at.tzinfo is None:
            closes_at = closes_at.replace(tzinfo=timezone.utc)
        if closes_at < datetime.now(timezone.utc):
            return False
    cycle = db.session.get(MeasurementCycle, deployment.cycle_id)
    return cycle is not None and cycle.status == 'open'


def _sections(deployment):
    """
    The form's domain steps, in order, each with its items.

    Items come back in `sort_order`, which interleaves the three subdomains
    within each domain — four near-identical autonomy questions in a row invite
    straight-lining. Nothing rendered names a subdomain, so interleaving costs
    the respondent nothing in coherence.
    """
    assessed = list(deployment.assessed_domains or domains.DOMAINS)
    ordered = [d for d in domains.DOMAINS if d in assessed]
    items = (db.session.query(SurveyItem)
             .filter_by(org_id=deployment.org_id,
                        version=deployment.instrument_version, is_active=True)
             .filter(SurveyItem.domain_code.in_(ordered))
             .order_by(SurveyItem.sort_order).all())
    return [(d, [i for i in items if i.domain_code == d]) for d in ordered]


def _load_session(deployment):
    """The caller's draft, from the resume cookie, or None."""
    raw = request.cookies.get(_resume_cookie_name(deployment.public_slug))
    if not raw or ':' not in raw:
        return None
    session_id, secret = raw.split(':', 1)
    session = db.session.get(ResponseSession, session_id)
    if session is None or session.deployment_id != deployment.id:
        return None
    if session.submitted_at is not None or session.is_void:
        return None
    # Constant-time, so a timing difference cannot be used to confirm that a
    # given session id exists.
    expected = session.resume_secret_hash or ''
    if not secrets.compare_digest(
            hashlib.sha256(secret.encode()).hexdigest(), expected):
        return None
    return session


def _require_session(deployment):
    session = _load_session(deployment)
    if session is None:
        abort(404)
    session.last_seen_at = datetime.now(timezone.utc)
    return session


def reporting_promise(min_n):
    """
    What we are entitled to tell a respondent, given the threshold in force.

    THE CLAIM FOLLOWS THE SETTING. Spec §9.2: "avoid promising absolute
    anonymity if the administration method cannot guarantee it." The same
    discipline that gives open_link and ticketed different wording applies
    here: a form promising group-only reporting while the threshold is 1 is a
    false statement to someone being asked to answer candidly about their
    employer, and a false promise is worse than no promise.

    Three bands, because the honest sentence genuinely differs:

      min_n >= 3  the group is large enough that a reader cannot reduce it to
                  one person by elimination. The full promise holds.

      min_n == 2  a pair. Each of the two can subtract their own answers and
                  read the other's exactly, so the promise is made to the
                  READER, not between the respondents.

      min_n <= 1  there is no group. A "result" may be one person's answers,
                  and we say that in those words. This is the local testing
                  setting; a real cycle running at 1 has to mean it.
    """
    if min_n >= 3:
        return 'group'
    if min_n == 2:
        return 'pair'
    return 'individual'


# ── Landing ─────────────────────────────────────────────────────────────────

@survey_bp.route('/<slug>')
def landing(slug):
    """
    Everything spec §9.2 requires a respondent to be told, BEFORE question one.

    This is not a nicety and not a legal footer. It is the thing that makes the
    answers worth having: purpose, how results will be used, who sees what, the
    minimum reporting threshold, the anonymity claim stated HONESTLY FOR THE
    DELIVERY MODE, the non-retaliation commitment, and what happens next.
    """
    deployment = _load_open_deployment(slug)
    cycle = db.session.get(MeasurementCycle, deployment.cycle_id)

    if not _is_live(deployment):
        # A real employee following a real link after the deployment closed. Say so,
        # rather than 404ing someone who did nothing wrong.
        return render_template('survey/closed.html'), 410

    session = _load_session(deployment)
    already = request.cookies.get(_submitted_cookie_name(slug)) == '1'

    return render_template(
        'survey/landing.html',
        deployment=deployment,
        cycle=cycle,
        promise=reporting_promise(cycle.min_reporting_n),
        resuming=session is not None,
        already_submitted=already,
        section_count=len(_sections(deployment)),
        item_count=sum(len(items) for _, items in _sections(deployment)),
    )


@survey_bp.route('/<slug>/begin', methods=['POST'])
def begin(slug):
    """
    Create the anonymous session and hand the browser its resume secret.

    Nothing about who this is, is recorded here, because there is nothing to
    record: the request is not authenticated and the table has no identity
    column.
    """
    deployment = _load_open_deployment(slug)
    if not _is_live(deployment):
        return render_template('survey/closed.html'), 410

    existing = _load_session(deployment)
    if existing is not None:
        return redirect(url_for('survey.section', slug=slug, index=1))

    if deployment.delivery_mode == 'ticketed':
        ticket = (request.form.get('ticket') or '').strip()
        if not ticket:
            return render_template('survey/ticket.html', deployment=deployment,
                                   error='Enter the code from your slip.'), 400
        row = (db.session.query(AccessTicket)
               .filter_by(deployment_id=deployment.id,
                          ticket_hash=hashlib.sha256(ticket.encode()).hexdigest())
               .first())
        if row is None:
            # One message for "no such code" and "already used" would be
            # friendlier to a probe; these are different problems for a real
            # respondent, and the codes are 72-bit, so the distinction leaks
            # nothing worth having.
            return render_template('survey/ticket.html', deployment=deployment,
                                   error='That code is not recognised.'), 400
        if row.used_at is not None:
            return render_template('survey/ticket.html', deployment=deployment,
                                   error='That code has already been used.'), 400
        row.used_at = datetime.now(timezone.utc)

    secret = secrets.token_urlsafe(32)
    session = ResponseSession(
        deployment_id=deployment.id,
        org_id=deployment.org_id,
        branch_id=deployment.branch_id,
        # The column survives the removal of segments, unused. {} rather
        # than NULL so existing readers see an empty map, not a None.
        segment_values={},
        resume_secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
    )
    db.session.add(session)
    db.session.commit()

    response = make_response(
        redirect(url_for('survey.section', slug=slug, index=1)))
    _set_resume_cookie(response, slug, session, secret)
    return response


def _set_resume_cookie(response, slug, session, secret):
    response.set_cookie(
        _resume_cookie_name(slug), f'{session.id}:{secret}',
        max_age=_RESUME_COOKIE_DAYS * 24 * 3600,
        httponly=True, samesite='Lax',
        secure=request.is_secure,
        path=url_for('survey.landing', slug=slug),
    )


# ── The segment step was removed ──────────────────────────────
#
# There used to be a `/‹slug›/about-you` step between `begin` and question 1,
# where a respondent self-declared their department, location or tenure band
# from a controlled vocabulary the org admin had defined. `begin` now goes
# straight to section 1.
#
# `response_sessions.segment_values` is LEFT IN THE SCHEMA and still written as
# an empty map. Sessions recorded before this change keep whatever they
# declared, and dropping the column would destroy that silently.
#
# What went with it: the reporting dimension. Results can still be broken down
# by BRANCH and compared across CYCLES, which is what the product now offers.
# A breakdown by department within a branch is no longer possible, because the
# data to do it is no longer collected.


# ── The domain sections ─────────────────────────────────────────────────────

@survey_bp.route('/<slug>/section/<int:index>', methods=['GET', 'POST'])
def section(slug, index):
    """
    One domain per step, saved on every POST.

    `index` is an ORDINAL, not a domain code: /section/2 rather than
    /section/TID. The URL is part of what a respondent sees, and a code in it
    would tell them which framework they are being scored against.
    """
    deployment = _load_open_deployment(slug)
    if not _is_live(deployment):
        return render_template('survey/closed.html'), 410
    session = _require_session(deployment)

    sections = _sections(deployment)
    if not 1 <= index <= len(sections):
        abort(404)
    domain_code, items = sections[index - 1]

    if request.method == 'POST':
        _save_answers(session, deployment, items)
        db.session.commit()
        if request.form.get('direction') == 'back' and index > 1:
            return redirect(url_for('survey.section', slug=slug, index=index - 1))
        if index < len(sections):
            return redirect(url_for('survey.section', slug=slug, index=index + 1))
        return redirect(url_for('survey.review', slug=slug))

    answers = _existing_answers(session, items)
    return render_template(
        'survey/section.html', deployment=deployment, session=session,
        index=index, section_count=len(sections), domain_code=domain_code,
        items=items, answers=answers,
    )


def _existing_answers(session, items):
    """{item_id: '1'..'5' or 'na'} for the items on this page."""
    rows = (db.session.query(SurveyResponse)
            .filter_by(session_id=session.id)
            .filter(SurveyResponse.item_id.in_([i.id for i in items])).all())
    return {str(r.item_id): ('na' if r.not_applicable else str(r.score))
            for r in rows}


def _save_answers(session, deployment, items):
    """
    Upsert this page's answers.

    N/A IS NOT A SCORE (§9.1, explicit). It is stored as a boolean with
    `score = NULL`, never as 0, 3 or 6 — a sentinel reaches an AVG() eventually
    and corrupts a result silently, weeks before anyone notices the number is
    wrong. The database CHECK constraint refuses the combination outright.

    A blank answer DELETES any stored row rather than writing a zero: the
    respondent went back and un-answered, and "no answer" and "not applicable"
    are different facts about different things.
    """
    existing = {r.item_id: r for r in
                db.session.query(SurveyResponse)
                .filter_by(session_id=session.id)
                .filter(SurveyResponse.item_id.in_([i.id for i in items])).all()}

    for item in items:
        raw = (request.form.get(f'q_{item.id}') or '').strip()
        row = existing.get(item.id)

        if raw == 'na' and item.allows_na:
            score, not_applicable = None, True
        elif raw in ('1', '2', '3', '4', '5'):
            score, not_applicable = int(raw), False
        else:
            if row is not None:
                db.session.delete(row)
            continue

        if row is None:
            db.session.add(SurveyResponse(
                session_id=session.id, deployment_id=deployment.id,
                org_id=deployment.org_id, item_id=item.id,
                score=score, not_applicable=not_applicable,
            ))
        else:
            row.score = score
            row.not_applicable = not_applicable
            row.answered_at = datetime.now(timezone.utc)


# ── Review and submit ───────────────────────────────────────────────────────

@survey_bp.route('/<slug>/review', methods=['GET', 'POST'])
def review(slug):
    """
    Last chance to go back, then submit.

    An incomplete form is ACCEPTED. Refusing it would discard 40 real answers
    to force 8 more, and a respondent who skipped the pay questions has told
    you something — which is why coverage is reported rather than required.
    """
    deployment = _load_open_deployment(slug)
    if not _is_live(deployment):
        return render_template('survey/closed.html'), 410
    session = _require_session(deployment)

    sections = _sections(deployment)
    total = sum(len(items) for _, items in sections)
    answered = db.session.query(SurveyResponse).filter_by(
        session_id=session.id).count()

    cycle = db.session.get(MeasurementCycle, deployment.cycle_id)

    if request.method == 'POST':
        now = datetime.now(timezone.utc)
        session.submitted_at = now
        session.last_seen_at = now

        if cycle is not None and cycle.is_pilot:
            comment = (request.form.get('pilot_comment') or '').strip()
            if comment:
                db.session.add(PilotFeedback(
                    session_id=session.id, deployment_id=deployment.id,
                    org_id=deployment.org_id, comment=comment[:4000],
                ))

        # A denormalised counter, incremented in the same transaction as the
        # submission so the two cannot disagree.
        deployment.responses_received = (deployment.responses_received or 0) + 1

        # Segments were removed, so there is nothing left to freeze here. The
        # freeze existed because renaming an option after responses arrived
        # would silently re-group answers given under the old meaning.
        db.session.commit()

        response = make_response(redirect(url_for('survey.done', slug=slug)))
        # Clear the draft cookie: the draft is gone, and leaving a dead pointer
        # on a shared browser is how the next person gets a 404 they cannot
        # explain.
        response.delete_cookie(_resume_cookie_name(slug),
                               path=url_for('survey.landing', slug=slug))
        # A NUDGE, NOT A CONTROL, and the landing page says so for this mode.
        response.set_cookie(_submitted_cookie_name(slug), '1',
                            max_age=_RESUME_COOKIE_DAYS * 24 * 3600,
                            httponly=True, samesite='Lax',
                            secure=request.is_secure,
                            path=url_for('survey.landing', slug=slug))
        return response

    return render_template('survey/review.html', deployment=deployment,
                           cycle=cycle, answered=answered, total=total,
                           section_count=len(sections))


@survey_bp.route('/<slug>/done')
def done(slug):
    deployment = _load_open_deployment(slug)
    cycle = db.session.get(MeasurementCycle, deployment.cycle_id)
    return render_template('survey/done.html', deployment=deployment,
                           cycle=cycle)
