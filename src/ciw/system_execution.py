"""Bounded process and OCI adapters for the system reference worker.

These adapters produce a candidate, never an admitted scientific result.  The
parent session must verify the returned candidate and retain its own execution
and verification identities.  Only code-owned calculation is supported here;
there is no physical-action interface, retry policy, or backend fallback.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import queue
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
import uuid


MAX_SPEC_BYTES = 1024 * 1024
MAX_CANDIDATE_BYTES = 16 * 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 64 * 1024
CANDIDATE_SCHEMA = "ciw.system-simulation.v1"
_IMAGE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[a-f0-9]{64}\Z")


class SystemExecutionError(ValueError):
    """Worker launch, resource envelope, or response-boundary failure."""


def _json_value(value: Any, depth: int = 0) -> None:
    if depth > 64:
        raise SystemExecutionError("System specification exceeds JSON nesting budget")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise SystemExecutionError("System specification contains nonfinite JSON number")
        return
    if type(value) is list:
        for item in value:
            _json_value(item, depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise SystemExecutionError("System JSON object keys must be strings")
            _json_value(item, depth + 1)
        return
    raise SystemExecutionError("System specification must contain only strict JSON values")


def _json_bytes(value: Any) -> bytes:
    _json_value(value)
    try:
        raw = json.dumps(value, allow_nan=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise SystemExecutionError("System specification cannot be serialized as strict JSON") from exc
    if not 1 <= len(raw) <= MAX_SPEC_BYTES:
        raise SystemExecutionError("System specification exceeds 1 MiB byte budget")
    return raw


def _strict_json(raw: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SystemExecutionError(f"Worker returned duplicate JSON key: {key}")
            result[key] = value
        return result

    def number(text):
        result = float(text)
        if not math.isfinite(result):
            raise SystemExecutionError("Worker returned nonfinite JSON number")
        return result

    def constant(_text):
        raise SystemExecutionError("Worker returned nonfinite JSON number")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_float=number, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise SystemExecutionError("Worker returned invalid strict JSON") from exc


def _read_candidate(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise SystemExecutionError("System worker did not produce a candidate file") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise SystemExecutionError("System worker candidate must be a regular file")
    if not 1 <= metadata.st_size <= MAX_CANDIDATE_BYTES:
        raise SystemExecutionError("System worker candidate exceeds 16 MiB byte budget")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(MAX_CANDIDATE_BYTES + 1)
    except OSError as exc:
        raise SystemExecutionError("System worker candidate could not be read safely") from exc
    if len(raw) > MAX_CANDIDATE_BYTES:
        raise SystemExecutionError("System worker candidate exceeds 16 MiB byte budget")
    candidate = _strict_json(raw)
    if type(candidate) is not dict or candidate.get("schema") != CANDIDATE_SCHEMA:
        raise SystemExecutionError("System worker returned a mismatched candidate schema")
    return candidate


def _terminate(process: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _run_bounded(command: list[str], *, cwd: Path, env: dict[str, str],
                 timeout_s: float, output_path: Path) -> bytes:
    """Drain both pipes portably without selecting Windows pipe handles.

    Each reader holds at most one 8 KiB chunk; the shared queue holds at most
    eight.  The parent retains no more than the declared diagnostic budget,
    polls the candidate size, and terminates the task when any envelope fails.
    Pipe reads occur only in daemon readers so they cannot block the deadline.
    """
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   shell=False, start_new_session=os.name == "posix")
    except OSError as exc:
        raise SystemExecutionError(f"System worker launch failed: {exc.strerror}") from exc
    chunks = queue.Queue(maxsize=8)
    cancelled = threading.Event()
    diagnostic = bytearray()
    deadline = time.monotonic() + timeout_s

    def publish(kind, chunk=None):
        while not cancelled.is_set():
            try:
                chunks.put((kind, chunk), timeout=0.05)
                return True
            except queue.Full:
                continue
        return False

    def read_stream(stream):
        try:
            while not cancelled.is_set():
                chunk = stream.read1(8192)
                if not chunk:
                    publish("eof")
                    return
                if not publish("data", chunk):
                    return
        except (OSError, ValueError):
            publish("error")
        finally:
            stream.close()

    def check_candidate_budget():
        try:
            size = output_path.stat().st_size
        except FileNotFoundError:
            return
        if size > MAX_CANDIDATE_BYTES:
            raise SystemExecutionError("System worker candidate exceeds 16 MiB byte budget")

    readers = []
    try:
        for stream in (process.stdout, process.stderr):
            assert stream is not None
            reader = threading.Thread(target=read_stream, args=(stream,), daemon=True,
                                      name="net-system-diagnostic-reader")
            reader.start()
            readers.append(reader)
        finished = 0
        while finished < 2 or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SystemExecutionError(f"System worker timeout after {timeout_s:g} seconds")
            check_candidate_budget()
            try:
                kind, chunk = chunks.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                continue
            if kind == "eof":
                finished += 1
            elif kind == "error":
                raise SystemExecutionError("System worker diagnostic pipe could not be read safely")
            else:
                if len(diagnostic) + len(chunk) > MAX_DIAGNOSTIC_BYTES:
                    raise SystemExecutionError("System worker exceeds 64 KiB diagnostic byte budget")
                diagnostic.extend(chunk)
        returncode = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        check_candidate_budget()
        if returncode:
            # Keep error reporting bounded independently of the retained pipe budget.
            detail = bytes(diagnostic[-4096:]).decode("utf-8", errors="replace").strip()
            raise SystemExecutionError(f"System worker failed with exit code {returncode}: {detail}")
        return bytes(diagnostic)
    except BaseException:
        _terminate(process)
        raise
    finally:
        cancelled.set()
        for reader in readers:
            reader.join(timeout=0.2)
        # Each started reader owns closing its stream.  Closing a BufferedReader
        # from another thread can wait on its blocking read lock, defeating the
        # deadline if a descendant inherited a pipe.  Close only unowned pipes.
        for stream in (process.stdout, process.stderr)[len(readers):]:
            if stream is not None:
                stream.close()


def _worker_environment() -> dict[str, str]:
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")
           if key in os.environ}
    # The selected interpreter is the parent's interpreter; the selected package
    # is the parent's source/install root, not a user-selected program or path.
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]),
               PYTHONHASHSEED="0", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    return env


def execute_worker(spec: dict[str, Any], *, engine: str = "subprocess",
                   image: str | None = None, timeout_s: float = 60) -> dict[str, Any]:
    """Execute once and return a candidate plus a separate runtime receipt.

    ``container`` (also ``oci``) requires a locally available immutable Docker
    image.  The adapter never pulls an image, retries a task, or falls back to a
    different backend.  The subprocess backend has process/byte/time limits,
    but does not claim OCI isolation.
    """
    raw = _json_bytes(spec)
    from .system_spec import validate_spec
    validate_spec(spec)
    resources = dict(spec["execution"]["resources"])
    if type(timeout_s) not in (int, float) or not 0 < timeout_s <= 300 or not math.isfinite(timeout_s):
        raise SystemExecutionError("System worker timeout must be finite and in (0, 300] seconds")
    if engine not in ("subprocess", "container", "oci"):
        raise SystemExecutionError("System worker engine must be subprocess or container")
    if engine == "subprocess" and image is not None:
        raise SystemExecutionError("An image may only be specified for container execution")
    docker = None
    if engine != "subprocess":
        if type(image) is not str or len(image) > 512 or not _IMAGE_RE.fullmatch(image):
            raise SystemExecutionError("Container execution requires an immutable image name@sha256:64hex")
        docker = shutil.which("docker")
        if not docker:
            raise SystemExecutionError("Container execution requires Docker; no fallback was attempted")
        if not hasattr(os, "getuid") or not hasattr(os, "getgid"):
            raise SystemExecutionError("Container execution requires explicit POSIX uid and gid")
    env = _worker_environment()
    semantic_command = ["python", "-m", "ciw", "system", "worker", "spec.json", "--output", "candidate.json"]
    with tempfile.TemporaryDirectory(prefix="net-system-worker-") as name:
        folder = Path(name)
        input_path, output_path = folder / "spec.json", folder / "candidate.json"
        input_path.write_bytes(raw)
        container_name = None
        if engine == "subprocess":
            command = [sys.executable, "-m", "ciw", "system", "worker",
                       str(input_path), "--output", str(output_path)]
            runtime = {"engine": "subprocess", "python": sys.version.split()[0],
                       "python_implementation": sys.implementation.name,
                       "command": semantic_command, "environment_attestation": "not_established",
                       "resources": resources, "resource_limits_enforced": False,
                       "timeout_s": float(timeout_s)}
        else:
            assert docker is not None and image is not None
            container_name = "net-system-worker-" + uuid.uuid4().hex
            command = [docker, "run", "--rm", "--pull", "never", "--name", container_name,
                       "--network", "none", "--read-only", "--cap-drop", "ALL",
                       "--security-opt", "no-new-privileges", "--pids-limit", "64",
                       "--cpus", str(resources["cpu"]), "--memory", f"{resources['memory_mb']}m",
                       "--user", f"{os.getuid()}:{os.getgid()}",
                       "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
                       "--mount", f"type=bind,source={folder},target=/work",
                       "--workdir", "/work", "--entrypoint", "python", image, "-m", "ciw", "system", "worker",
                       "/work/spec.json", "--output", "/work/candidate.json"]
            runtime = {"engine": "container", "image_reference": image,
                       "image_digest": image.split("@", 1)[1], "command": semantic_command,
                       "environment_attestation": "not_established", "resources": resources,
                       "resource_limits_enforced": True, "timeout_s": float(timeout_s)}
        try:
            _run_bounded(command, cwd=folder, env=env, timeout_s=float(timeout_s), output_path=output_path)
            candidate = _read_candidate(output_path)
        except BaseException:
            if container_name is not None:
                # Killing a Docker client is insufficient to terminate its
                # container.  Remove only the unique task-owned container.
                try:
                    subprocess.run([docker, "rm", "--force", container_name], env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, shell=False, timeout=5, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            raise
    return {"candidate": candidate, "execution_runtime": runtime}
