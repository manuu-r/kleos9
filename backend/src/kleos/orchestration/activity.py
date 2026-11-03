"""Public progress steps for the workbench timeline.

A step says what the run is doing: planning, a model deciding, a tool call and its
observed result. It carries public summaries and short argument values only, never
private reasoning, raw file contents, credentials or provider payloads. Steps are
advisory display state; losing one never changes a task result.
"""
import asyncio
import logging
import re
from datetime import UTC, datetime

log = logging.getLogger(__name__)

STEP_LIMIT = 80
_TEXT_LIMIT = 600
_VALUE_LIMIT = 120
_SECRET = re.compile(r'key|token|secret|password|credential|authorization|cookie', re.IGNORECASE)
_HIDDEN = {'actionId', 'action_id'}


def _short(text, limit=_TEXT_LIMIT):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def summarize_arguments(arguments):
    """Show which inputs a tool received without echoing bulky or sensitive values."""
    summary = {}
    for key, value in list((arguments or {}).items())[:12]:
        if key in _HIDDEN:
            continue
        if _SECRET.search(key):
            summary[key] = '[redacted]'
        elif isinstance(value, str):
            summary[key] = value if len(value) <= _VALUE_LIMIT else f'{len(value)} characters'
        elif isinstance(value, (bool, int, float)) or value is None:
            summary[key] = value
        elif isinstance(value, (list, tuple)):
            summary[key] = f'{len(value)} items'
        elif isinstance(value, dict):
            summary[key] = f'{len(value)} fields'
        else:
            summary[key] = type(value).__name__
    return summary


def step(kind, phase, *, task=None, turn=None, lane=None, tool=None, summary='', **details):
    """One timeline entry. The id is stable, so a replayed or retried step replaces itself."""
    ident = ':'.join(str(part) for part in (task or 'objective', turn if turn is not None else '-', kind,
                                            tool or '-', phase))
    entry = {'id': ident, 'kind': kind, 'phase': phase, 'at': datetime.now(UTC).isoformat()}
    for key, value in (('taskId', task), ('turn', turn), ('lane', lane), ('tool', tool)):
        if value is not None:
            entry[key] = value
    if summary:
        entry['summary'] = _short(summary)
    entry.update({key: value for key, value in details.items() if value not in (None, [], {})})
    return entry


def observations(items):
    return [{'name': _short(item.name, 120), 'status': str(item.status)} for item in list(items)[:20]]


async def report(repository, scope, entry):
    """Record a step if the repository supports it. Progress never fails the work it describes."""
    record = getattr(repository, 'record_activity', None)
    if record is None:
        return
    try:
        await record(scope, entry)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - display state must not change task outcomes
        log.debug('Activity step was not recorded', exc_info=True)
