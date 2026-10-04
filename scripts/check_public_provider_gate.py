"""Run unchanged public composition gates using exact retained provider history.

Only the four declared public compositions are accepted. Imported current
subdirectories never substitute for an execution pin, missing Git history is
an error, and this route neither reads nor exports private provider sources.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

if __package__:
    from .monorepo import ROOT, _constant, provider_worktrees
    from .provider_checkouts import validate_checkout
else:
    from monorepo import ROOT, _constant, provider_worktrees
    from provider_checkouts import validate_checkout


GATES = {
    "calibrated-window": ("check_calibrated_window.py", frozenset({"tbrt", "mcur", "stfe", "gsie", "set"})),
    "declared-workloads": ("check_declared_workloads.py", frozenset({"sra", "scr"})),
    "calibrated-observable": ("check_calibrated_observable.py", frozenset({"fsrt", "tbrt", "mcur", "oit", "gsie", "cbsr", "fdir", "set"})),
    "identified-design": ("check_identified_design.py", frozenset({"fsrt", "tbrt", "mcur", "oit", "gsie", "cbsr", "fdir", "set", "sidt", "edspt", "ywir"})),
}
OUTPUT_GATES = frozenset({"declared-workloads", "calibrated-window"})


def gate_pins(gate: str, root: Path = ROOT) -> dict[str, str]:
    """Read the existing execution declarations without importing providers."""
    if gate not in GATES:
        raise ValueError("Unknown public provider gate")
    package = Path(root) / "src/ciw"
    if gate == "declared-workloads":
        declarations = _constant(package / "declared_workload.py", "PINS")
        entries = [(pin["role"], pin["revision"]) for pin in declarations.values()]
    else:
        filenames = (["calibrated-observable-runtimes.json", "identified-design-runtimes.json"]
                     if gate == "identified-design" else [gate + "-runtimes.json"])
        entries = []
        for name in filenames:
            declarations = json.loads((package / name).read_text(encoding="utf-8"))
            entries.extend((role, pin["revision"]) for role, pin in declarations.items())
    pins = dict(entries)
    if len(pins) != len(entries) or set(pins) != GATES[gate][1]:
        raise ValueError("Public gate declarations must preserve their complete distinct provider set")
    return pins


def public_environment() -> dict[str, str]:
    """Do not pass inherited private Git credential transport into this gate."""
    return {key: value for key, value in os.environ.items()
            if not key.startswith(("GIT_", "GH_")) and key not in
            {"GITHUB_TOKEN", "CIW_PROVIDER_READ_TOKEN", "SCR_READ_TOKEN", "SSH_ASKPASS", "GCM_INTERACTIVE"}}


def run_gate(gate: str, output_dir: Path | None = None) -> int:
    if output_dir is not None and gate not in OUTPUT_GATES:
        raise ValueError("This unchanged gate does not accept an output directory")
    pins = gate_pins(gate)
    with provider_worktrees(ROOT, roles=sorted(pins), overrides=pins) as providers:
        if set(providers) != set(pins):
            raise ValueError("Retained provider bindings must exactly match the declared public gate")
        paths = {role: validate_checkout(path, pins[role]) for role, path in providers.items()}
        parents = {path.parent for path in paths.values()}
        if len(parents) != 1 or any(path.name != role for role, path in paths.items()):
            raise ValueError("Public gate requires standalone role-named checkouts under one stack root")
        command = [sys.executable, str(ROOT / "scripts" / GATES[gate][0]),
                   "--stack-root", str(parents.pop())]
        if output_dir is not None:
            command += ["--output-dir", str(output_dir.resolve())]
        try:
            result = subprocess.run(command, cwd=ROOT, env=public_environment(), check=False)
        finally:
            # A failed child also cannot hide a changed provider source.
            for role, path in paths.items():
                validate_checkout(path, pins[role])
        return result.returncode if result.returncode >= 0 else 128 - result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gate", choices=sorted(GATES))
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    return run_gate(args.gate, args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
