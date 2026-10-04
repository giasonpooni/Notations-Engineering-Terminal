"""Actual subprocess coverage of the pipe reader used on every platform."""
import sys
import threading
import time

import pytest

from ciw import system_execution as execution


def _run(tmp_path, script, *, timeout_s=5, arguments=()):
    return execution._run_bounded([sys.executable, "-c", script, *map(str, arguments)],
                                  cwd=tmp_path, env=execution._worker_environment(),
                                  timeout_s=timeout_s, output_path=tmp_path / "candidate.json")


def test_portable_reader_drains_both_pipes_at_the_exact_shared_limit(tmp_path, monkeypatch):
    # Nonblocking file descriptors are unavailable for the Windows pipe path.
    # This path intentionally has no reliance on os.set_blocking on any OS.
    monkeypatch.setattr(execution.os, "set_blocking", lambda *_args: pytest.fail("nonportable pipe setup"), raising=False)
    script = """import os, threading
threads = [threading.Thread(target=os.write, args=(descriptor, value * 32768))
           for descriptor, value in ((1, b'x'), (2, b'y'))]
for thread in threads: thread.start()
for thread in threads: thread.join()
"""
    output = _run(tmp_path, script)
    assert len(output) == execution.MAX_DIAGNOSTIC_BYTES
    assert output.count(b"x") == output.count(b"y") == 32768


def test_combined_pipe_output_one_byte_over_limit_is_refused(tmp_path):
    script = """import os, threading
threads = [threading.Thread(target=os.write, args=(1, b'x' * 32768)),
           threading.Thread(target=os.write, args=(2, b'y' * 32769))]
for thread in threads: thread.start()
for thread in threads: thread.join()
"""
    with pytest.raises(execution.SystemExecutionError, match="64 KiB diagnostic"):
        _run(tmp_path, script)


def test_deadline_terminates_and_reaps_worker_and_reader_threads(tmp_path, monkeypatch):
    original = execution.subprocess.Popen
    processes = []
    readers_before = set(threading.enumerate())
    def track(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(execution.subprocess, "Popen", track)
    started = time.monotonic()
    with pytest.raises(execution.SystemExecutionError, match="timeout after"):
        _run(tmp_path, "import time; time.sleep(30)", timeout_s=0.1)
    assert time.monotonic() - started < 5
    assert len(processes) == 1 and processes[0].poll() is not None
    assert not {thread for thread in threading.enumerate() if thread not in readers_before
                and thread.name == "net-system-diagnostic-reader"}


def test_candidate_growth_is_checked_while_worker_is_still_running(tmp_path, monkeypatch):
    original = execution.subprocess.Popen
    processes = []
    def track(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(execution.subprocess, "Popen", track)
    script = """import sys, time
with open(sys.argv[1], 'wb') as stream: stream.truncate(16 * 1024 * 1024 + 1)
time.sleep(30)
"""
    with pytest.raises(execution.SystemExecutionError, match="16 MiB byte budget"):
        _run(tmp_path, script, arguments=(tmp_path / "candidate.json",))
    assert processes[0].poll() is not None


def test_nonzero_exit_retains_only_a_bounded_diagnostic_tail(tmp_path):
    script = "import sys; sys.stderr.buffer.write(b'x'*65532+b'END!'); sys.stderr.flush(); sys.exit(9)"
    with pytest.raises(execution.SystemExecutionError, match="exit code 9") as raised:
        _run(tmp_path, script)
    assert str(raised.value).endswith("END!")
    assert len(str(raised.value)) < 4200


def test_empty_output_success_reaps_worker_without_waiting_for_pipe_events(tmp_path):
    assert _run(tmp_path, "pass") == b""
