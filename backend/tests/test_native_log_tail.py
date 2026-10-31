import subprocess

import pytest

from kleos import native_runner as runner


@pytest.mark.parametrize('timeout', [False, True])
def test_long_build_log_preserves_final_error_even_on_timeout(tmp_path, monkeypatch, timeout):
    payload = b'INITIAL BUILD HEADER\n' + b'x' * 240000 + b'\nACTUAL COMPILER ERROR AT END\n'
    class Process:
        pid = 12345
        waits = 0
        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1 and timeout is not None and fail_timeout:
                raise subprocess.TimeoutExpired('fixture', timeout)
            return 1
    fail_timeout = timeout
    def popen(args, **kwargs):
        kwargs['stdout'].write(payload)
        kwargs['stdout'].flush()
        return Process()
    monkeypatch.setattr(runner.subprocess, 'Popen', popen)
    monkeypatch.setattr(runner.shutil, 'which', lambda *args, **kwargs: '/fixture')
    monkeypatch.setattr(runner.os, 'chown', lambda *args: None)
    monkeypatch.setattr(runner.os, 'killpg', lambda *args: None)
    monkeypatch.setattr(runner, 'reap_generated_processes', lambda: None)
    if timeout:
        with pytest.raises(runner.NativeTimeoutError) as exc:
            runner.run_command(['fixture'], tmp_path, 1)
        output = exc.value.logs
    else:
        status, output = runner.run_command(['fixture'], tmp_path, 1)
        assert status == 1
    assert output.encode() == payload[-100000:]
    assert 'ACTUAL COMPILER ERROR AT END' in output and 'INITIAL BUILD HEADER' not in output
