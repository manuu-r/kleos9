"""Opt-in: real ngspice against independent analytic references (KLEOS_TEST_NGSPICE=<binary or 1>).

The decks are compiler-owned text generated from structured contracts, never candidate SPICE.
This is a direct host solver run for development; it is NOT evidence about the isolated image.
"""
import asyncio
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from simulation_fixtures import RC_CIRCUIT, RC_SIMULATION, ready_plan, variant

from kleos.validation.simulation.plan import STEP_SECONDS
from kleos.validation.simulation.results import DeckOutput
from kleos.validation.simulation.run import ExecutorUnavailable, simulate

BINARY = os.environ.get('KLEOS_TEST_NGSPICE')
BINARY = shutil.which('ngspice') if BINARY == '1' else BINARY
pytestmark = pytest.mark.skipif(not BINARY, reason='Set KLEOS_TEST_NGSPICE=1 (or a path) to run the real solver')


class HostNgspice:
    async def run(self, deck, project_id=None):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / 'deck.cir').write_text(deck.text)
            try:
                done = subprocess.run([BINARY, '-b', '-o', 'log.txt', 'deck.cir'], cwd=work, capture_output=True,
                                      text=True, timeout=15, check=False)
            except subprocess.TimeoutExpired:
                raise ExecutorUnavailable('Solver timed out') from None
            if done.returncode != 0 or not (work / 'tran.txt').exists():
                raise ExecutorUnavailable('Solver failed: ' + done.stdout[-300:])
            return DeckOutput((work / 'op.txt').read_text(), (work / 'tran.txt').read_text()), done.stdout


def run(plan):
    version = subprocess.run([BINARY, '--version'], capture_output=True, text=True).stdout
    pin = next(line for line in version.splitlines() if 'ngspice-' in line)
    pin = pin[pin.index('ngspice-'):].split()[0]
    return asyncio.run(simulate(plan, HostNgspice(), allowed_versions=(pin,)))


def shockley_divider(vs, r1, r2, saturation, ideality, vt=0.025865):
    """Independent fixed point: (vs - v)/r1 = v/r2 + Is*(exp(v/(N*Vt)) - 1)."""
    low, high = 0.0, vs
    for _ in range(200):
        mid = (low + high) / 2
        residual = (vs - mid) / r1 - mid / r2 - saturation * math.expm1(mid / (ideality * vt))
        low, high = (mid, high) if residual > 0 else (low, mid)
    return (low + high) / 2


def test_real_solver_matches_closed_form_rc_response_in_every_corner():
    circuit, simulation = variant(RC_CIRCUIT, RC_SIMULATION)
    plan = ready_plan(circuit, simulation)
    outcome = run(plan)
    assert outcome.status == 'available', outcome.reason
    meta, by_id = outcome.result['metadata'], {p['componentId']: p for p in plan.metadata['passives']}
    rails = {tuple(m['corner']): m['voltageV'] for m in
             next(c for c in outcome.result['checks'] if c['name'] == 'circuit_dc_rails')['details']['measurements']}
    for deck in plan.decks:
        scale = {pid: 1 + deck.corner[i] * by_id[pid]['toleranceFraction'] for i, pid in enumerate(plan.metadata['passiveIds'])}
        r1, r2 = 1000 * scale['R1'], 2000 * scale['R2']
        assert rails[deck.corner] == pytest.approx(3.3 * r2 / (r1 + r2), rel=1e-6)
    nominal = next(s for s in outcome.result['waveform']['signals'] if s['name'] == 'load')
    tau = (1000 * 2000 / 3000) * 100e-9
    for index in (2, 5, 20, 100, 400):  # 200 us bins; the 1 us supply ramp is negligible after the first bin
        t = index * 2 * STEP_SECONDS
        assert nominal['values'][index] == pytest.approx(2.2 * (1 - math.exp(-t / tau)), abs=.02)
    assert meta['sampleIntervalSeconds'] == STEP_SECONDS and outcome.result['toolVersion'].startswith('ngspice-')


def test_real_solver_led_operating_point_matches_independent_shockley_solution():
    plan = ready_plan()
    outcome = run(plan)
    assert outcome.status == 'available', outcome.reason
    measurement = next(m for m in next(c for c in outcome.result['checks'] if c['name'] == 'circuit_dc_rails')
                       ['details']['measurements'] if m['corner'] == [0, 0, 0])
    expected = shockley_divider(3.3, 1000, 2000, 1e-20, 2)
    assert measurement['voltageV'] == pytest.approx(expected, rel=2e-3)
    led = next(m for m in next(c for c in outcome.result['checks'] if c['name'] == 'circuit_led_current')
               ['details']['measurements'] if m['corner'] == [0, 0, 0])
    dc_ma = 1e-20 * math.expm1(expected / (2 * 0.025865)) * 1e3
    settled = next(s for s in outcome.result['waveform']['signals'] if s['name'] == 'D1 current')['values'][-1]
    assert settled == pytest.approx(dc_ma, rel=.02)  # settled waveform equals the independent DC solution
    assert led['peakCurrentMa'] >= dc_ma  # the check reports the transient maximum, never less than DC
    assert [c['status'] for c in outcome.result['checks']] == ['PASS'] * 4


def test_real_solver_overdriven_led_is_a_fail_not_a_pass():
    circuit, simulation = variant()
    simulation['supplyVoltageV'] = 12
    simulation['loads'][0].update(maxVoltageV=60, minVoltageV=.1)
    next(r for r in circuit['references'] if r['reference'] == 'R1')['value'] = '100'
    simulation['maxCurrentMa'] = 500
    outcome = run(ready_plan(circuit, simulation))
    assert outcome.status == 'available', outcome.reason
    assert next(c for c in outcome.result['checks'] if c['name'] == 'circuit_led_current')['status'] == 'FAIL'
