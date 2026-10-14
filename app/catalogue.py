"""
Intervention catalogue helpers. Pure Python — no Flask, session injected.

THE ONE THING THIS MODULE EXISTS TO ENFORCE
-------------------------------------------
Specification §8.5:

    "Training should not be treated as a substitute for addressing
     substantiated compensation inequities or structural reward problems."

So `for_cell()` returns NOTHING for a Reward Satisfaction subdomain, and
returns the §8.5-aligned guidance instead. That filter runs BEFORE a row
reaches a report or the narrative payload — the model cannot recommend a course
it was never shown, which is a stronger guarantee than instructing it not to.

The supplied curriculum reached the same conclusion independently: it maps no
course at all to the three Reward subdomains, offering a note for each about
pay-equity review, market benchmarking or compensation review.

────────────────────────────────────────────────────────────────────────────────
AND §11'S CATEGORIES TRAVEL WITH EVERY RECOMMENDATION
────────────────────────────────────────────────────────────────────────────────
§11 closes: "Training is one intervention category among several." A report
that lists three courses and nothing else has quietly made training the answer.
`recommendations()` therefore returns the §11 response categories for the cell
ALONGSIDE any courses, and the templates render both.
"""
from app import domains

# What a Reward Satisfaction finding gets instead of a course. Taken from the
# supplied curriculum's own notes, which match §8.5 and §11's Reward rows.
REWARD_GUIDANCE = {
    (domains.PIE, 'P'): 'Address via an internal pay-equity review, not training.',
    (domains.PIE, 'I'): 'Address via market benchmarking, not training.',
    (domains.PIE, 'E'): ('Address via a compensation review and transparent '
                         'communication about pay, not training.'),
}


def for_cell(db_session, domain_code, subdomain_code, limit=None):
    """
    Active catalogue topics mapped to one (domain, subdomain) cell.

    THE PAIR IS THE JOIN KEY, never a label — subdomain letters repeat across
    domains, so 'I' alone names Purpose and Impact, Positive Interactions AND
    Industry Comparison. A renamed label cannot move a topic to a different
    cell, which is the failure trainiq's free-text `sub_module` join produced.

    Returns [] for any Reward subdomain, before touching the database.
    """
    if not domains.allows_training(domain_code, subdomain_code):
        return []

    from app.models import InterventionCatalogue, InterventionCell  # noqa: PLC0415

    query = (db_session.query(InterventionCatalogue)
             .join(InterventionCell,
                   InterventionCell.intervention_id == InterventionCatalogue.id)
             .filter(InterventionCell.domain_code == domain_code,
                     InterventionCell.subdomain_code == subdomain_code,
                     InterventionCatalogue.is_active.is_(True))
             # DETERMINISTIC ORDER. Two runs of the same report must list the
             # same courses in the same order, or a client comparing a PDF they
             # saved with one they regenerate finds a difference that means
             # nothing.
             .order_by(InterventionCatalogue.sort_order,
                       InterventionCatalogue.intervention_code))
    rows = query.all()
    return rows[:limit] if limit else rows


def recommendations(db_session, domain_code, subdomain_code, limit=None):
    """
    Everything a report should show for one cell.

    Always returns the §11 response categories. Returns courses only where
    §8.5 permits them, and the reward guidance where it does not — so a Reward
    finding is never silently empty, which would read as "nothing to do here".
    """
    entry = domains.intervention_entry(domain_code, subdomain_code)
    return {
        'domain_code': domain_code,
        'subdomain_code': subdomain_code,
        'subdomain': domains.subdomain_label(domain_code, subdomain_code),
        'finding': entry.get('finding', ''),
        'areas_to_investigate': entry.get('areas', ''),
        'response_categories': entry.get('categories', ''),
        'training_permitted': domains.allows_training(domain_code, subdomain_code),
        'courses': for_cell(db_session, domain_code, subdomain_code, limit),
        'guidance': REWARD_GUIDANCE.get((domain_code, subdomain_code)),
    }


def for_priority(db_session, priority_view, limit_cells=3, limit_courses=3):
    """
    Recommendations for the cells a priority view says to look at first.

    Follows the ORDER THE ENGINE PRODUCED — by size of contrast — rather than
    re-ranking here. Two places deciding what matters most is two places to
    disagree.
    """
    if not priority_view or not getattr(priority_view, 'entries', None):
        return []
    entries = [e for e in priority_view.entries
               if e.quadrant == 'investigate_first'] or priority_view.entries
    return [recommendations(db_session, *entry.diagnostic, limit=limit_courses)
            for entry in entries[:limit_cells]]


def coverage(db_session):
    """
    Which cells have a course and which do not. For the L1 catalogue screen.

    An empty cell is a PRODUCT QUESTION, not a bug: a finding there produces no
    course, and the report must say so rather than printing a short list — an
    empty development section reads exactly like "no development needed", which
    is the worst available reading of a real finding. The sibling product hit
    this with one uncovered cell and had to name the gap explicitly in its
    reports.
    """
    out = []
    for domain_code, subdomain_code in domains.subdomain_pairs():
        permitted = domains.allows_training(domain_code, subdomain_code)
        courses = for_cell(db_session, domain_code, subdomain_code)
        out.append({
            'domain_code': domain_code,
            'subdomain_code': subdomain_code,
            'label': domains.subdomain_label(domain_code, subdomain_code),
            'training_permitted': permitted,
            'count': len(courses),
            'courses': courses,
            'guidance': REWARD_GUIDANCE.get((domain_code, subdomain_code)),
            # Covered means: has a course, OR is deliberately course-free.
            'covered': bool(courses) or not permitted,
        })
    return out
