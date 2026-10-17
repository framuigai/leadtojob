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


def for_priority(db_session, priority_view, limit_cells=5, limit_courses=3):
    """
    Recommendations for the cells a priority view says to look at first.

    Follows the ORDER THE ENGINE PRODUCED — by size of contrast — rather than
    re-ranking here. Two places deciding what matters most is two places to
    disagree.

    The `investigate_first` cells lead, then the rest of the priority order
    fills the remaining slots. Taking ONLY that quadrant used to hide the
    catalogue entirely on a cycle whose worst findings were all Reward: the
    three PIE subdomains carry no course by §8.5, so two of them consumed
    every slot and the report named no course at all while trainable cells sat
    just below the cut. The reading order is unchanged — a Reward finding
    still comes first when it ranks first, and still shows its §8.5 guidance
    rather than a course.
    """
    if not priority_view or not getattr(priority_view, 'entries', None):
        return []
    entries = list(priority_view.entries)
    leading = [e for e in entries if e.quadrant == 'investigate_first']
    rest = [e for e in entries if e not in leading]
    ordered = (leading + rest)[:limit_cells]
    return [recommendations(db_session, *entry.diagnostic, limit=limit_courses)
            | {'score': getattr(entry, 'score', None),
               'n': getattr(entry, 'n', None)}
            for entry in ordered]


def for_scores(db_session, subdomain_rows, limit_cells=5, limit_courses=3):
    """
    THE FALLBACK WHEN THERE IS NO CONTRAST TO RANK BY.

    `for_priority()` orders by the gap between the least and most satisfied
    thirds, which needs association.contrast_floor() respondents — at least
    12. Below that the priority view is empty, and the catalogue silently
    produced NOTHING: a report that named no course at all, not because no
    course applied but because the ranking input was missing. That is the
    worst available reading of a small cycle, and it is what the client sees
    first.

    So where a contrast cannot be computed, the cells are ordered by SCORE,
    lowest first, from the subdomain results the report already shows. It is
    a weaker basis and the report says which basis it used — §10.5 permits
    the relative statement "this scored lowest for this group" without any
    band label, and permits nothing stronger.

    `subdomain_rows` is build_context's list of ((domain, sub), GroupResult or
    Suppressed). Suppressed cells are skipped: an unreportable figure must not
    become a recommendation by the back door.
    """
    scored = [(pair, result) for pair, result in (subdomain_rows or [])
              if getattr(result, 'mean', None) is not None]
    if not scored:
        return []
    scored.sort(key=lambda row: (row[1].mean, row[0]))
    return [recommendations(db_session, pair[0], pair[1], limit=limit_courses)
            | {'score': result.mean, 'n': result.n}
            for pair, result in scored[:limit_cells]]


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
