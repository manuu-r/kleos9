"""Parsing, advisory checks and envelope construction against closed-form solver output."""
import asyncio
import math

import pytest
from simulation_fixtures import RC_CIRCUIT, RC_SIMULATION, analytic_output, ready_plan, variant

from kleos.projects.store import fingerprint
from kleos.validation.simulation.plan import STEP_SECONDS
from kleos.validation.simulation.results import (
    SAMPLES,
    DeckOutput,
    SimulationDataError,
    parse_deck,
    tool_version,
)
from kleos.validation.simulation.run import ExecutorUnavailable, simulate

BANNER = '** ngspice-39 : Circuit level simulation program'


class Analytic:
    def __init__(self, plan, *, led_ma=1.0, tamper=None, banner=BANNER):
        self.plan, self.led_ma, self.tamper, self.banner, self.expected = plan, led_ma, tamper, banner, []

    async def run(self, deck, project_id=None):
        index = [d.sha256 for d in self.plan.decks].index(deck.sha256)
        output, expected = analytic_output(self.plan, index, led_ma=self.led_ma)
        self.expected.append(expected)
        return (self.tamper(output) if self.tamper else output), self.banner


def run(plan, executor):
    return asyncio.run(simulate(plan, executor))


def check(result, name):
    return next(c for c in result['checks'] if c['name'] == name)


def test_closed_form_divider_passes_all_checks_with_independent_expected_values():
    plan = ready_plan()
    outcome = run(plan, Analytic(plan))
    assert outcome.status == 'available'
    result = outcome.result
    assert [c['status'] for c in result['checks']] == ['PASS', 'PASS', 'PASS', 'PASS']
    nominal = next(m for m in check(result, 'circuit_dc_rails')['details']['measurements'] if m['corner'] == [0, 0, 0])
    assert nominal['voltageV'] == pytest.approx(3.3 * 2000 / 3000, rel=1e-6)
    startup = next(m for m in check(result, 'circuit_startup')['details']['measurements'] if m['corner'] == [0, 0, 0])
    tau = (1000 * 2000 / 3000) * 100e-9
    assert startup['settled'] and startup['rise90Seconds'] == pytest.approx(tau * math.log(10), abs=STEP_SECONDS)
    assert check(result, 'circuit_total_current')['details']['peakCurrentMa'] == pytest.approx(3.3 / (1000 * .95) * 1e3, rel=1e-6)  # worst corner: R1 at -5%


def test_rail_outside_limits_fails_at_some_corner_even_if_nominal_is_inside():
    circuit, simulation = variant(RC_CIRCUIT, RC_SIMULATION)
    simulation['loads'][0].update(minVoltageV=2.15, maxVoltageV=2.25)  # nominal 2.2 V, corners 2.125..2.272
    plan = ready_plan(circuit, simulation)
    result = run(plan, Analytic(plan)).result
    rails = check(result, 'circuit_dc_rails')
    assert rails['status'] == 'FAIL'
    inside = [m['minVoltageV'] <= m['voltageV'] <= m['maxVoltageV'] for m in rails['details']['measurements']]
    assert inside[0] and not all(inside)


def test_led_over_current_fails_and_unrated_circuits_are_not_applicable():
    plan = ready_plan()
    assert check(run(plan, Analytic(plan, led_ma=6.5)).result, 'circuit_led_current')['status'] == 'FAIL'
    circuit, simulation = variant(RC_CIRCUIT, RC_SIMULATION)
    rc = ready_plan(circuit, simulation)
    assert check(run(rc, Analytic(rc)).result, 'circuit_led_current')['status'] == 'NOT_APPLICABLE'


def test_supply_draw_above_the_declared_budget_fails():
    circuit, simulation = variant()
    simulation['maxCurrentMa'] = 2
    plan = ready_plan(circuit, simulation)
    assert check(run(plan, Analytic(plan)).result, 'circuit_total_current')['status'] == 'FAIL'


def test_unresolved_startup_is_unknown_not_pass_or_fail():
    circuit, simulation = variant()
    next(r for r in circuit['references'] if r['reference'] == 'C1')['value'] = '100u'  # tau ~ 67 ms
    plan = ready_plan(circuit, simulation)
    result = run(plan, Analytic(plan)).result
    assert check(result, 'circuit_startup')['status'] == 'UNKNOWN'
    assert check(result, 'circuit_dc_rails')['status'] == 'PASS'


def test_envelope_matches_the_viewer_contract_and_extrema_enclose_the_nominal_trace():
    plan = ready_plan()
    result = run(plan, Analytic(plan)).result
    assert result['schemaVersion'] == 'circuit-waveform-v1' and result['evidenceKind'] == 'digital'
    assert result['physicalAcceptance'] is False and result['metadata']['physicalAcceptance'] is False
    wave = result['waveform']
    assert len(wave['timeSeconds']) == 501 and wave['timeSeconds'][-1] == pytest.approx(.1)
    assert {s['name'] for s in wave['signals']} == {'load', 'Supply current', 'D1 current', 'Net OUT', 'Net VIN'}
    for signal in wave['signals']:
        assert signal['unit'] in {'V', 'mA'} and len(signal['values']) == len(signal['low']) == len(signal['high']) == 501
        assert all(lo <= v <= hi for lo, v, hi in zip(signal['low'], signal['values'], signal['high']))
        assert set(signal['limits']) <= {'min', 'max'}
    load = next(s for s in wave['signals'] if s['name'] == 'load')
    assert load['limits'] == {'min': 1.8, 'max': 3.4} and min(load['low']) < min(load['values'][20:]) + 1
    for entry in result['checks']:
        details = entry['details']
        assert details['advisory'] and not details['required'] and details['scopeExcluded'] and not details['physicalAcceptance']
    assert result['resultsSha256'] == fingerprint({k: v for k, v in result.items() if k != 'resultsSha256'})
    assert [d['sha256'] for d in result['decks']] == [d.sha256 for d in plan.decks]


def test_results_are_reproducible_and_tampering_changes_the_hash():
    plan = ready_plan()
    first, second = run(plan, Analytic(plan)).result, run(plan, Analytic(plan)).result
    assert first['resultsSha256'] == second['resultsSha256'] or first['metadata']['durationSeconds'] != second['metadata']['durationSeconds']
    first['waveform']['signals'][0]['values'][3] += 1e-6
    assert first['resultsSha256'] != fingerprint({k: v for k, v in first.items() if k != 'resultsSha256'})


def _corrupt(kind):
    def tamper(output):
        op, tran = output.op_text, output.tran_text
        lines = tran.splitlines()
        if kind == 'header':
            tran = tran.replace('i(v1)', 'i(v9)', 1)
        elif kind == 'partial':
            tran = '\n'.join(lines[:-40]) + '\n'
        elif kind == 'nan':
            tran = tran.replace(lines[5].split()[1], 'nan', 1)
        elif kind == 'absurd':
            tran = tran.replace(lines[7].split()[1], '9e99', 1)
        elif kind == 'duplicate_time':
            lines[9] = lines[8]
            tran = '\n'.join(lines) + '\n'
        elif kind == 'sparse':
            tran = '\n'.join(lines[:1] + lines[1::2]) + '\n'
        elif kind == 'extra_column':
            tran = tran.replace(lines[3], lines[3] + ' 1.0', 1)
        elif kind == 'oversized':
            tran = tran + 'x' * 2_100_000
        elif kind == 'empty_op':
            op = ''
        elif kind == 'two_op_rows':
            op = op + op.splitlines()[-1] + '\n'
        return DeckOutput(op, tran)
    return tamper


@pytest.mark.parametrize('kind', ['header', 'partial', 'nan', 'absurd', 'duplicate_time', 'sparse', 'extra_column',
                                  'oversized', 'empty_op', 'two_op_rows'])
def test_malformed_solver_output_is_error_never_a_pass(kind):
    plan = ready_plan()
    outcome = run(plan, Analytic(plan, tamper=_corrupt(kind)))
    assert outcome.status == 'ERROR' and outcome.result is None and 'rejected' in outcome.reason


def test_direct_parser_rejects_out_of_grid_time():
    plan = ready_plan()
    output, _ = analytic_output(plan, 0)
    lines = output.tran_text.splitlines()
    parts = lines[10].split()
    parts[0] = f'{float(parts[0]) + 3e-5:.8e}'
    lines[10] = ' '.join(parts)
    with pytest.raises(SimulationDataError):
        parse_deck((0, 0, 0), DeckOutput(output.op_text, '\n'.join(lines) + '\n'), plan.metadata['vectors'])
    assert len(lines) == SAMPLES + 1


@pytest.mark.parametrize('banner,expected', [(BANNER, 'ngspice-39'), ('** ngspice-39.3 : x', None), ('** ngspice-390 : x', None), ('** ngspice-47 : x', 'ngspice-47'),
    ('** ngspice-42 : x', None), ('no solver here', None), ('', None)])
def test_only_pinned_solver_versions_are_trusted(banner, expected):
    assert tool_version(banner) == expected


def test_unpinned_solver_and_missing_executor_are_unknown_and_version_drift_is_error():
    plan = ready_plan()
    assert run(plan, Analytic(plan, banner='** ngspice-42 : x')).status == 'UNKNOWN'
    assert run(plan, None).status == 'UNKNOWN'

    class Missing:
        async def run(self, deck, project_id=None):
            raise ExecutorUnavailable('Simulator image is not installed.')

    assert run(plan, Missing()).reason == 'Simulator image is not installed.'

    class Drift(Analytic):
        async def run(self, deck, project_id=None):
            output, _ = await super().run(deck)
            return output, BANNER if deck.corner == (0, 0, 0) else '** ngspice-47 : x'

    assert run(plan, Drift(plan)).status == 'ERROR'


def test_an_unready_plan_never_reaches_the_executor():
    circuit, simulation = variant()
    simulation['loads'] = []
    from kleos.validation.simulation.plan import plan_simulation

    class Forbidden:
        async def run(self, deck, project_id=None):
            raise AssertionError('must not run')

    outcome = asyncio.run(simulate(plan_simulation(circuit, simulation), Forbidden()))
    assert outcome.status == 'UNKNOWN' and outcome.result is None


def viewer_parse(result):
    """Python mirror of `CircuitWaveData.parse` in kleos-ui/lib/ui/features/project/circuit_waveform.dart."""
    assert result['schemaVersion'] == 'circuit-waveform-v1' and result['evidenceKind'] == 'digital'
    assert result['physicalAcceptance'] is False
    wave = result['waveform']

    def numbers(raw):
        assert isinstance(raw, list) and len(raw) <= 501
        assert all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and abs(v) <= 1e12 for v in raw)
        return raw
    times = numbers(wave['timeSeconds'])
    assert len(times) != 1 and all(0 <= t <= .100001 for t in times) and all(b > a for a, b in zip(times, times[1:]))
    assert isinstance(wave['signals'], list) and 0 < len(wave['signals']) <= 64
    for signal in wave['signals']:
        values, low, high = numbers(signal['values']), numbers(signal['low']), numbers(signal['high'])
        assert len(values) == len(low) == len(high) == len(times)
        assert signal['unit'] in ('V', 'mA') and isinstance(signal['name'], str) and len(signal['name']) <= 300
        assert all(lo <= v <= hi for lo, v, hi in zip(low, values, high))
        assert all(key in ('min', 'max') and isinstance(v, (int, float)) and math.isfinite(v) and abs(v) <= 1e12
                   for key, v in signal['limits'].items())
    for check in result['checks']:
        assert check['status'] in {'PASS', 'FAIL', 'UNKNOWN', 'ERROR', 'NOT_APPLICABLE'}
        for measurement in check['details'].get('measurements', []):
            assert all(isinstance(v, int) for v in measurement['corner'])
    return times, wave['signals']


def test_viewer_contract_accepts_the_recorded_fixture_and_every_generated_envelope():
    import json
    from pathlib import Path
    recorded = json.loads((Path(__file__).parents[1] / 'verification/ngspice-power-on-sample.json').read_text())
    viewer_parse(recorded)  # proves this validator is faithful to the frontend's own sample
    for led_ma in (1.0, 6.5):
        plan = ready_plan()
        viewer_parse(run(plan, Analytic(plan, led_ma=led_ma)).result)
    circuit, simulation = variant(RC_CIRCUIT, RC_SIMULATION)
    plan = ready_plan(circuit, simulation)
    viewer_parse(run(plan, Analytic(plan)).result)
