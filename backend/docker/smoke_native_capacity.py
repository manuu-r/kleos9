"""Measure an existing offline fixture and its fresh task cgroup, not product evidence."""
import atexit
import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from kleos.native_usage import capture_usage

SAMPLE_SECONDS = .1


def sample(directory):
    root = Path(directory)
    previous, peak, samples = None, None, 0
    while not (root / 'stop').exists():
        measured, clock = capture_usage(), time.monotonic()
        cpu = measured.get('cpuUsageSeconds')
        if isinstance(cpu, (int, float)):
            if previous is not None and cpu >= previous[0] and clock > previous[1]:
                cores = (cpu - previous[0]) / (clock - previous[1])
                peak = cores if peak is None else max(peak, cores)
                samples += 1
            previous = cpu, clock
        time.sleep(SAMPLE_SECONDS)
    (root / 'samples.json').write_text(json.dumps({'cpuSamplePeakCores': peak, 'cpuSamples': samples,
        'sampleIntervalSeconds': SAMPLE_SECONDS, 'kind': 'sampled_cgroup_cpu_delta_not_kernel_peak'}))


def limits():
    result = {}
    for name in ('memory.max', 'cpu.max', 'pids.max'):
        try:
            raw = (Path('/sys/fs/cgroup') / name).read_text()
            if len(raw) <= 4096:
                result[name] = raw.strip()
        except OSError:
            pass
    return result


def measure_fixture(fixture, arguments):
    helper_path = Path(__file__)
    helper_hash = hashlib.sha256(helper_path.read_bytes()).hexdigest()
    started = time.monotonic()
    baseline = capture_usage()
    observed_limits = limits()
    monitor = tempfile.TemporaryDirectory(prefix='native-capacity-monitor-')
    directory = Path(monitor.name)
    sampler = subprocess.Popen([sys.executable, '-I', str(helper_path), '--sample', str(directory)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state = {'status': 'failed', 'fixture': fixture, 'fixtureArguments': arguments}
    def finalize():
        (directory / 'stop').write_text('stop')
        try:
            sampler.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sampler.kill()
            sampler.wait(timeout=5)
        usage = capture_usage()
        samples_file = directory / 'samples.json'
        samples = json.loads(samples_file.read_text()) if samples_file.is_file() else {
            'cpuSamplePeakCores': None, 'cpuSamples': 0, 'reason': 'Sampler unavailable; no zero substituted'}
        elapsed = time.monotonic() - started
        cpu_delta = None
        if 'cpuUsageSeconds' in baseline and 'cpuUsageSeconds' in usage:
            cpu_delta = max(0, usage['cpuUsageSeconds'] - baseline['cpuUsageSeconds'])
        print(json.dumps({**state, 'measuredTaskSeconds': elapsed, 'usageBaseline': baseline, 'usage': usage,
            'cpuUsageDeltaSeconds': cpu_delta, 'cpuAverageCores': cpu_delta / elapsed if cpu_delta is not None else None,
            'sampling': samples, 'observedCgroupLimits': observed_limits,
            'usageScope': 'Fresh task cgroup, including trusted supervisor, sampler and every fixture child',
            'sourceHashes': {name: hashlib.sha256((Path('/usr/local/lib/python3.12/site-packages/kleos') / name).read_bytes()).hexdigest()
                             for name in ('native_usage.py', 'native_runner.py', 'native_network.py')},
            'fixtureSha256': hashlib.sha256(Path(fixture).read_bytes()).hexdigest(),
            'capacityHelperSha256': helper_hash, 'modelCalls': 0}), flush=True)
        monitor.cleanup()
    atexit.register(finalize)
    output = io.StringIO()
    fixture_started = time.monotonic()
    try:
        import runpy
        sys.argv = [fixture, *arguments]
        with contextlib.redirect_stdout(output):
            runpy.run_path(fixture, run_name='__main__')
        state['status'] = 'passed'
    except BaseException as exc:
        if isinstance(exc, SystemExit) and exc.code in (None, 0):
            state['status'] = 'passed'
        else:
            state['failure'] = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-20000:]
    state['fixtureWallSeconds'] = time.monotonic() - fixture_started
    tail = output.getvalue()[-20000:]
    try:
        state['fixtureResult'] = json.loads(tail.strip().splitlines()[-1])
    except (IndexError, ValueError):
        state['fixtureOutputTail'] = tail
    if state['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--sample']:
        sample(sys.argv[2])
    else:
        measure_fixture(sys.argv[1], sys.argv[2:])
