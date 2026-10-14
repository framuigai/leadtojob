"""
Anthropic call + narrative orchestration. MANUAL TRIGGER ONLY.

Settled posture, inherited from trainiq and promo and not up for rediscovery:

  * A narrative is generated when a human presses Interpret on a report. It is
    NEVER auto-dispatched when a response threshold is crossed. A wave that
    fills up overnight must not spend money, or write a characterisation of a
    workforce's morale, while nobody is watching.

  * EVERY quantity — scores, distributions, dispersion, coverage, contrasts,
    the priority order — is computed deterministically in app/analytics/ and
    merely NARRATED here. The model is never asked to compare, rank or
    calculate. Two runs of the same report agree on every number.

  * The system prompt is loaded from app/prompts/system_prompt.txt as a file,
    never inlined as a long Python string.

  * Retry up to 3 times on HTTP 529 with exponential backoff (1s, 2s, 4s).

  * Log and RAISE on a JSON parse failure. Never fall back to an empty dict: a
    report that silently renders an empty narrative looks finished and is not.

────────────────────────────────────────────────────────────────────────────────
FOUR PROMPT RULES ARE LOAD-BEARING HERE, AND EACH IS ALSO ASSERTED IN TESTS
────────────────────────────────────────────────────────────────────────────────
  1. Never assert causation. §10.4, §17.8. No "causes", "drives", "because of",
     "leads to" about an association.
  2. Never apply a band or verdict label while bands are unvalidated (§10.5).
  3. Never read a low Appropriate Challenge score as stress, pressure or
     overload (§6.4).
  4. Never present training as the response to a compensation finding (§8.5),
     and never recommend training for a subdomain whose §11 categories exclude
     it.

The prompt states them, and app/analytics/ enforces them structurally — the
catalogue is type-filtered before a row reaches the model, and the band
function raises. A prompt instruction is the last line of defence, not the
first.

REPORTS NEVER MENTION AI, CLAUDE OR ANTHROPIC. The section is "Interpretation".
"""
import json
import logging
import os
import time
from decimal import Decimal
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

# The model this product runs on. Stated once, here. Confirm it rather than
# assuming it is unchanged — and never silently downgrade it to save money,
# which is the operator's decision and not this module's.
MODEL = 'claude-opus-5-5'

# Narratives are a few hundred words per section. 16k leaves room for the
# longest report (HR & OD, which covers all 48 items) without risking a
# truncated JSON object, which would fail the parse.
MAX_TOKENS = 16000

# Claude Opus 5.5 runs adaptive thinking and cannot have it disabled; its
# effort default is `medium`. Narrating pre-computed figures is not a
# reasoning-heavy task, so `medium` is stated explicitly rather than left
# implicit — if the default moves, this does not.
EFFORT = 'medium'

# Published prices per million tokens, for ai_cost_events. Recorded so the L1
# Usage screen can answer "what did this client cost" without anyone exporting
# a bill. Update alongside MODEL.
_USD_PER_MTOK_INPUT = Decimal('5')
_USD_PER_MTOK_OUTPUT = Decimal('25')

_PROMPT_PATH = Path(__file__).resolve().parent / 'prompts' / 'system_prompt.txt'

# 529 = overloaded. Backoff 1s, 2s, 4s.
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 1.0


class NarrativeError(RuntimeError):
    """Raised when a narrative cannot be produced. Never swallowed."""


def _system_prompt():
    """
    Read the prompt from disk on each call.

    Deliberately not cached at import: editing the prompt is a routine
    operational act, and a stale copy held in a long-lived worker is the kind
    of difference noticed only in finished client output.
    """
    try:
        return _PROMPT_PATH.read_text(encoding='utf-8')
    except OSError as exc:
        raise NarrativeError(
            f'The interpretation prompt is missing or unreadable at '
            f'{_PROMPT_PATH}. Reports cannot be narrated until it is restored.'
        ) from exc


def api_key_present():
    """
    Whether narration is configured at all.

    Used to disable the Interpret button rather than letting someone press it
    and receive a stack trace. The key itself is never returned or logged.
    """
    return bool(os.environ.get('ANTHROPIC_API_KEY'))


def _client():
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise NarrativeError(
            'ANTHROPIC_API_KEY is not set. Interpretation is unavailable; '
            'every other part of the report is unaffected.'
        )
    # max_retries=0 because the retry policy below is the ONE policy. The SDK
    # retries by default, and two layers would turn 3 attempts into 9 and the
    # documented 1/2/4s backoff into something nobody can predict from the code.
    return anthropic.Anthropic(api_key=api_key, max_retries=0)


def _call(system_prompt, user_message):
    """One call, with the documented retry policy. Returns (text, usage)."""
    client = _client()
    last_error = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                output_config={'effort': EFFORT},
                system=system_prompt,
                messages=[{'role': 'user', 'content': user_message}],
            )
        except anthropic.APIStatusError as exc:
            if exc.status_code != 529 or attempt == _MAX_ATTEMPTS:
                raise NarrativeError(
                    f'The interpretation service returned {exc.status_code}. '
                    f'The report is still available without its narrative.'
                ) from exc
            delay = _BACKOFF_BASE * (2 ** (attempt - 1))
            logger.warning(
                'Interpretation service overloaded (529). Retrying in %.0fs '
                '(attempt %d of %d).', delay, attempt, _MAX_ATTEMPTS)
            last_error = exc
            time.sleep(delay)
            continue
        except anthropic.APIConnectionError as exc:
            raise NarrativeError(
                'Could not reach the interpretation service. The report is '
                'still available without its narrative.'
            ) from exc

        # A safety decline is a flat refusal to narrate, not a transport error;
        # it must surface as itself rather than as a parse failure two lines on.
        if getattr(response, 'stop_reason', None) == 'refusal':
            details = getattr(response, 'stop_details', None)
            logger.error('Interpretation refused by the model (category=%s).',
                         getattr(details, 'category', None))
            raise NarrativeError(
                'The interpretation service declined to write this section. '
                'The report is still available without its narrative.'
            )
        if getattr(response, 'stop_reason', None) == 'max_tokens':
            # A truncated object will not parse. Say why, rather than reporting
            # invalid JSON and sending someone to look at the prompt.
            raise NarrativeError(
                'The interpretation was longer than the space allowed and was '
                'cut off. Nothing was saved.'
            )

        text = ''.join(block.text for block in response.content
                       if getattr(block, 'type', None) == 'text')
        usage = getattr(response, 'usage', None)
        return text, {
            'input_tokens': getattr(usage, 'input_tokens', 0) or 0,
            'output_tokens': getattr(usage, 'output_tokens', 0) or 0,
        }

    raise NarrativeError(
        'The interpretation service was overloaded on every attempt.'
    ) from last_error


def cost_usd(input_tokens, output_tokens):
    """Decimal, not float: this is money and it is summed on the Usage screen."""
    return ((Decimal(input_tokens) / 1_000_000) * _USD_PER_MTOK_INPUT
            + (Decimal(output_tokens) / 1_000_000) * _USD_PER_MTOK_OUTPUT)


def generate(payload, sections, context_note=None):
    """
    Narrate one report. Returns (parsed_dict, usage_dict).

    `payload` is the deterministic figures — already computed, already
    suppressed. `sections` names the JSON keys wanted back.

    WHAT IS IN `payload` MATTERS AS MUCH AS THE PROMPT. The caller passes
    figures that have ALREADY been through suppression, so a group below the
    reporting threshold is absent rather than present-and-caveated. The model
    cannot narrate what it was not given, and that is a stronger guarantee than
    asking it not to.

    Raises NarrativeError on anything that goes wrong. The caller shows the
    message and renders the report without its narrative, because every other
    part of a report stands on its own.
    """
    system_prompt = _system_prompt()

    requested = '\n'.join(f'  "{key}": {description}'
                          for key, description in sections.items())
    user_message = (
        'Here are the computed figures for this measurement wave. Every number '
        'is final — narrate it, do not recalculate it, and do not infer any '
        'figure that is not present.\n\n'
        f'{json.dumps(payload, indent=2, default=str)}\n\n'
        + (f'\n{context_note}\n\n' if context_note else '')
        + 'Return a single JSON object with exactly these keys:\n'
        + requested
        + '\n\nNo prose outside the JSON object. No markdown fence.'
    )

    raw, usage = _call(system_prompt, user_message)

    text = (raw or '').strip()
    # Strip a fence if one arrives anyway — a wrapper is a formatting slip, not
    # a content failure, and discarding an otherwise good narrative over three
    # backticks would be the wrong trade.
    if text.startswith('```'):
        text = '\n'.join(line for line in text.splitlines()
                         if not line.strip().startswith('```')).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        # Log the SHAPE, never the narrative text: it characterises a
        # workforce's morale and does not belong in an application log.
        logger.error(
            'Interpretation JSON parse failed at position %d (%d chars '
            'returned, keys requested: %s).', exc.pos, len(text), list(sections))
        raise NarrativeError(
            'The interpretation came back in a form this system could not '
            'read. Nothing was saved. The report is still available without it.'
        ) from exc

    if not isinstance(parsed, dict) or not parsed:
        raise NarrativeError(
            'The interpretation came back empty. Nothing was saved.')

    missing = [key for key in sections if key not in parsed]
    if missing:
        # Partial is usable — a report with 4 of 5 sections is better than
        # none, and the template renders only what is present. Logged so a
        # persistently missing section is noticed.
        logger.warning('Interpretation omitted section(s): %s', missing)

    return parsed, usage
