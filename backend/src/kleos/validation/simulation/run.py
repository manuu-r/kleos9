"""Plan execution through an injected isolated executor. This module never runs a solver itself."""
import time
from dataclasses import dataclass
from typing import Protocol

from kleos.validation.simulation.plan import MAX_DECKS, Deck, Plan
from kleos.validation.simulation.results import (
    DEFAULT_VERSIONS,
    DeckOutput,
    SimulationDataError,
    build_result,
    parse_deck,
    tool_version,
)


class ExecutorUnavailable(Exception):
    """The isolated solver is not installed or admitted; nothing was run."""


class DeckExecutor(Protocol):
    async def run(self, deck: Deck, project_id: str | None = None) -> tuple[DeckOutput, str]:
        """Run one platform-compiled deck in isolation; return its output files and solver banner."""


@dataclass(frozen=True)
class Outcome:
    status: str  # available | UNKNOWN | ERROR
    reason: str = ''
    result: dict | None = None


async def simulate(plan: Plan, executor: DeckExecutor | None, *, allowed_versions=DEFAULT_VERSIONS,
                   clock=time.monotonic, project_id=None) -> Outcome:
    if not plan.ready:
        return Outcome('UNKNOWN', ' '.join(plan.reasons))
    if executor is None:
        return Outcome('UNKNOWN', 'No isolated circuit simulator is installed.')
    if len(plan.decks) > MAX_DECKS:
        return Outcome('ERROR', 'The deck count exceeds its bound.')
    started, outputs, runs, version = clock(), [], [], None
    try:
        for deck in plan.decks:
            output, banner = await executor.run(deck, project_id)
            seen = tool_version(banner, allowed_versions)
            if seen is None:
                return Outcome('UNKNOWN', 'The solver version is not a trusted pinned installation.')
            if version not in (None, seen):
                return Outcome('ERROR', 'The solver version changed during one simulation.')
            version = seen
            outputs.append(output)
            runs.append(parse_deck(deck.corner, output, plan.metadata['vectors']))
    except ExecutorUnavailable as error:
        return Outcome('UNKNOWN', str(error) or 'The isolated simulator is unavailable.')
    except SimulationDataError as error:
        return Outcome('ERROR', f'Solver output rejected: {error}')
    return Outcome('available', '', build_result(plan, runs, outputs, version, round(clock() - started, 3)))
