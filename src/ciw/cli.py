"""Headless calculations and terminal access to the same live session as Godot."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import tempfile
import uuid
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from .adapters.protocol import AdapterRefusal
from .instruments import make_demo_run, validate_run
from .server import run_server
from .session import Session, loads_json, read_json, write_json


def print_json(value) -> None:
    print(json.dumps(value, indent=2, allow_nan=False))


def _write_new_proof_report(path: Path, report: dict) -> None:
    """Publish a complete report without replacing a concurrent writer's file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ciw-proof-report-", dir=path.parent) as directory:
        staged = write_json(Path(directory) / "report.json", report)
        # Same-filesystem hard-link creation is atomic and fails if the target
        # exists. The staging link is removed on either success or refusal.
        os.link(staged, path)


def _print_matrix(name: str, matrix: list, order: list | None = None, unit: object = None) -> None:
    """Render every covariance entry; never substitute marginal sigmas."""
    print(f"Covariance: {name}")
    if order:
        print("  Axis order: " + ", ".join(map(str, order)))
    if unit:
        if isinstance(unit, list):
            print("  Axis units: " + json.dumps(unit) + "; entry (i, j) uses unit[i] * unit[j]")
        else:
            print("  Units: " + (json.dumps(unit) if isinstance(unit, dict) else str(unit)))
    for row in matrix:
        print("  [" + ", ".join(format(value, ".12g") for value in row) + "]")


def print_investigation(summary: dict) -> None:
    """Present retained states without interpreting or calculating domain values."""
    print(f"Investigation: {summary['status']}")
    if summary.get("workspace_file"):
        print(f"Workspace: {summary['workspace_file']}")
    if summary.get("run_id"):
        print(f"Run: {summary['run_id']}")
    rows = [["State", "Quantity", "Value", "Unit"]]
    for row in summary.get("terminal_rows", []):
        value = row.get("value")
        rendered = (format(value, ".12g") if type(value) in (int, float)
                    else json.dumps(value, allow_nan=False, ensure_ascii=False))
        rows.append([str(row.get("state", "")), str(row.get("quantity", "")),
                     rendered, str(row.get("unit", ""))])
    if len(rows) > 1:
        widths = [max(len(row[index]) for row in rows) for index in range(4)]
        print()
        for row in rows:
            print("  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip())
    for status in summary.get("calibration", []):
        acquisition, serving = status["acquisition"], status["serving"]
        print(f"Calibration: {status['source']} / {status['calibration_id']}")
        print(f"  Acquisition applicable: {str(acquisition['applicable_at_acquisition']).lower()}"
              f" at {acquisition['observed_at']} ({acquisition['provenance']})")
        print(f"  Current applicable: {str(serving['current_applicability']).lower()}; "
              f"expired: {str(serving['expired']).lower()}; "
              f"not yet valid: {str(serving['not_yet_valid']).lower()}")
        print(f"  Evaluated at: {serving['evaluated_at']}; valid interval: "
              f"[{status['valid_from']}, {status['valid_until']})")
    covariances = summary.get("covariances", [])
    for covariance in covariances:
        _print_matrix(covariance.get("name", covariance.get("covariance_id", "retained covariance")),
                      covariance.get("matrix", covariance.get("covariance", [])),
                      covariance.get("quantity_ids", covariance.get("order", covariance.get("state_order", covariance.get("parameter_order")))),
                      covariance.get("units", covariance.get("unit")))
        for field in ("frame", "method", "covariance_id", "result_id"):
            if field in covariance:
                print(f"  {field}: {covariance[field]}")
    if not covariances:
        for result in summary.get("results", []):
            data = result.get("data", {})
            estimate = data.get("estimate", {})
            if isinstance(estimate, dict) and isinstance(estimate.get("covariance"), list):
                sources = data.get("calibrated_observation", {}).get("source_ids")
                _print_matrix("estimated state / " + result["result_id"], estimate["covariance"],
                              sources, str(estimate.get("unit", "")) + "²")
    for refusal in summary.get("refusals", []):
        detail = refusal.get("refusal", refusal)
        print(f"Refusal: {detail.get('code', 'refused')}: {detail.get('message', '')}")
    if summary.get("note"):
        print(summary["note"])
    print("Use --json to inspect full identities, covariance and provenance.")


def load_run(path: Path | None) -> dict:
    run = read_json(path) if path else make_demo_run()
    validate_run(run)
    return run


async def request_remote(url: str, kind: str, payload: dict, *, timeout_s: float = 15) -> dict:
    request_id = uuid.uuid4().hex
    async with asyncio.timeout(timeout_s):
        async with connect(url, max_size=33_554_432, open_timeout=min(5, timeout_s), close_timeout=1,
                           proxy=None) as socket:
            await socket.send(json.dumps({"protocol_version": 1, "request_id": request_id,
                                          "type": kind, "payload": payload}, allow_nan=False))
            async for raw in socket:
                if not isinstance(raw, str):
                    raise ValueError("Protocol v1 requires a text JSON response")
                response = loads_json(raw)
                if not isinstance(response, dict):
                    raise ValueError("Service returned a non-object response")
                if response.get("request_id") == request_id:
                    return response
    raise RuntimeError("Service disconnected without returning a response")


async def health_remote(url: str) -> dict:
    """Probe session.get with a three-second request budget and bounded close."""
    try:
        response = await request_remote(url, "session.get", {}, timeout_s=3)
    except TimeoutError as exc:
        raise RuntimeError("Health probe timed out waiting for session.get") from exc
    if (type(response.get("protocol_version")) is not int or response["protocol_version"] != 1
            or response.get("type") != "response"):
        raise ValueError("Health probe did not receive a protocol v1 session response")
    payload = response.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("session_id"), str) or not payload["session_id"]:
        raise ValueError("Health probe received an invalid session snapshot")
    run, selection = payload.get("run"), payload.get("selection")
    if not isinstance(run, dict) or not isinstance(selection, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("Health probe received an incomplete session snapshot")
    if not all(isinstance(run.get(key), str) and run[key] for key in ("run_id", "evidence_id", "instrument")):
        raise ValueError("Health probe received invalid recording identity")
    metadata, channels = run.get("metadata"), run.get("channels")
    if not isinstance(metadata, dict) or not isinstance(channels, dict) or not channels:
        raise ValueError("Health probe received invalid recording metadata")
    if (selection.get("run_id") != run["run_id"]
            or not isinstance(selection.get("channel"), str) or selection["channel"] not in channels
            or not isinstance(metadata.get("coordinate_frame"), str)
            or selection.get("coordinate_frame") != metadata["coordinate_frame"]
            or type(selection.get("revision")) is not int or selection["revision"] < 0):
        raise ValueError("Health probe received an invalid shared selection")
    try:
        duration, cursor = metadata["duration_s"], selection["cursor_s"]
        interval = selection["interval_s"]
        count = metadata["sample_count"]
        oscillator = run["instrument"] == "analytic-damped-oscillator.v1"
        if (not isinstance(interval, list) or len(interval) != 2
                or type(count) is not int or count < (2 if oscillator else 1)):
            raise ValueError
        numbers = (duration, cursor, *interval)
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in numbers):
            raise ValueError
        if not (duration > 0 and 0 <= cursor <= duration
                and 0 <= interval[0] < interval[1] <= duration):
            raise ValueError
        rate = metadata.get("sample_rate_hz")
        if oscillator or rate is not None:
            if type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0:
                raise ValueError
        if oscillator:
            if (not math.isclose(duration, count / rate, rel_tol=1e-8)
                    or cursor > (count - 1) / rate):
                raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Health probe received invalid recording bounds") from exc
    return {"status": "healthy", "session_id": payload["session_id"], "run_id": run["run_id"]}


def load_server_session(args: argparse.Namespace) -> Session:
    if args.workspace:
        return Session.from_workspace(args.workspace, args.output_dir)
    if args.resume:
        workspace = args.output_dir / "workspace.json"
        try:
            workspace.stat()
        except FileNotFoundError:
            pass
        else:
            # Corrupt or unreadable saved state is an error, never a reason to
            # silently replace the workspace with a freshly generated demo.
            return Session.from_workspace(workspace, args.output_dir)
    return Session(load_run(args.recording), args.output_dir)


async def watch_remote(url: str) -> None:
    async with connect(url, max_size=8_388_608, open_timeout=5, close_timeout=2,
                       proxy=None) as socket:
        async for raw in socket:
            if not isinstance(raw, str):
                raise ValueError("Protocol v1 requires a text JSON event")
            event = loads_json(raw)
            print(json.dumps(event, allow_nan=False), flush=True)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="ciw", description="Computational Instrumentation Workbench")
    commands = root.add_subparsers(dest="command", required=True)
    dependencies = commands.add_parser("dependencies", help="Inspect retained dependency and correction status without provider execution")
    dependencies.add_argument("path", type=Path, help="Saved workspace JSON")
    commands.add_parser("legibility", help="Compile and verify synchronized specimen representations")
    commands.add_parser("system", help="Compile, run, observe and verify scientific system configurations")
    from .doctor import PROFILES
    doctor = commands.add_parser("doctor", help="Inspect explicit local provider identities without running or installing them")
    doctor.add_argument("--profile", choices=PROFILES, default="core")
    doctor.add_argument("--stack-root", type=Path, help="Declared-workload role directories sra and scr")
    doctor.add_argument("--engine", type=Path, help="Explicit declared-workload execution engine")
    doctor.add_argument("--binding", type=Path, help="Existing native, reaction or interval runtime binding JSON")
    capabilities = commands.add_parser(
        "capabilities",
        help="Print the provider-free capability index; this does not execute or qualify",
    )
    capability_actions = capabilities.add_subparsers(dest="capabilities_command")
    capability_actions.add_parser("list", help="Print every indexed record")
    capability_show = capability_actions.add_parser(
        "show", help="Print one indexed record without executing it",
    )
    capability_show.add_argument("capability_id")
    from .learning import register_commands
    register_commands(commands)
    demo = commands.add_parser("demo", help="Save deterministic synthetic oscillator evidence")
    demo.add_argument("--output", type=Path, default=Path("recordings/demo.json"))
    export = commands.add_parser("export", help="Write a read-only inspect copy")
    export_actions = export.add_subparsers(dest="export_command", required=True)
    usda = export_actions.add_parser("usda", help="Write ASCII USDA from a retained recording or inspect view")
    usda.add_argument("path", type=Path)
    usda.add_argument("--output", type=Path, required=True)
    usda.add_argument("--compare", action="store_true",
                      help="Reload the written USDA points and match them to the source")
    usda.add_argument("--usd-bin", type=Path,
                      help="Pinned usdcat or usdview executable used to open the copy")
    bindings = commands.add_parser("bindings", help="Inspect registered language bindings without a language chain")
    bindings.add_argument("binding_id", nargs="?", help="python-session, julia-oscillator, or native-interop-scr")
    bindings.add_argument("--path", type=Path, help="Retained session or source to inspect without launching a runtime")
    bindings.add_argument("--create", action="store_true", help="Run create through the julia-oscillator binding")
    bindings.add_argument("--output", type=Path, help="Bundle output for --create")
    bindings.add_argument("--julia", type=Path, help="Pinned julia executable")
    bindings.add_argument("--runtime", type=Path, help="Pinned julia-oscillator runtime directory")
    chart = commands.add_parser("chart", help="Apply a declared design chart to finite coordinates")
    chart.add_argument("chart_id", choices=["identity", "scale"])
    chart.add_argument("--point", required=True, help="Comma-separated finite coordinates")
    chart.add_argument("--source-frame", required=True)
    chart.add_argument("--target-frame", required=True)
    chart.add_argument("--scales", help="Comma-separated factors for the scale chart")
    analyze = commands.add_parser("analyze", help="Run headless analysis and save a reopenable workspace")
    analyze.add_argument("operation", choices=["stats", "spectrum"])
    analyze.add_argument("--recording", type=Path)
    analyze.add_argument("--channel", help="Defaults to the recording's first channel")
    analyze.add_argument("--start", type=float, default=0)
    analyze.add_argument("--end", type=float)
    analyze.add_argument("--output-dir", type=Path, default=Path("results"))
    server = commands.add_parser("serve", help="Start the shared local instrument session")
    source = server.add_mutually_exclusive_group()
    source.add_argument("--recording", type=Path)
    source.add_argument("--workspace", type=Path)
    source.add_argument("--resume", action="store_true", help="Reopen output-dir/workspace.json if present")
    server.add_argument("--output-dir", type=Path, default=Path("results"))
    server.add_argument("--port", type=int, default=8765)
    server.add_argument("--bind", choices=["127.0.0.1", "0.0.0.0"], default="127.0.0.1",
                        help="Use 0.0.0.0 explicitly inside a container; default stays loopback")
    server.add_argument("--fsrt-repo", type=Path,
                        help="Explicit trusted checkout for pinned FSRT operations")
    server.add_argument("--jspt-repo", type=Path,
                        help="Explicit trusted checkout for pinned JSPT covariance operations")
    server.add_argument("--gte-repo", type=Path,
                        help="Explicit trusted checkout for pinned GTE circle projection")
    stack = server.add_mutually_exclusive_group()
    stack.add_argument("--calibrated-stack-root", type=Path,
                       help="Bind eight role-named pinned checkouts to the shared process workbench")
    stack.add_argument("--identified-stack-root", type=Path,
                       help="Bind eleven pinned checkouts for shared process fusion and observation design")
    server.add_argument("--esm-binding", type=Path,
                        help="Trusted local ESM executable, replay, policy and optional candidate-store configuration")
    server.add_argument("--esm-telemetry-binding", type=Path,
                        help="Separate trusted ESM configuration at the scalar telemetry provider pins")
    server.add_argument("--telemetry-stack-root", type=Path,
                        help="Bind role-named ppda/stfe/gsie/set/cbsr checkouts alongside the process stack")
    server.add_argument("--calibrated-window-stack-root", type=Path,
                        help="Bind tbrt/mcur/stfe/gsie/set for shared calibrated window estimation")
    server.add_argument("--schematic-repo", type=Path, help="Bind pinned SRA declared schematic assessment")
    server.add_argument("--schematic-companions-root", type=Path, help="Bind pinned sra/jspt/plsr directories for selected schematic companion calls")
    server.add_argument("--construction-repo", type=Path, help="Bind pinned CSE quantity conditioning and ledger replay")
    server.add_argument("--acquisition-repo", type=Path, help="Bind pinned PPDA and its scout gitlink for retained dataset acquisition")
    server.add_argument("--exchange-set-repo", type=Path,
                        help="Bind the exact State Estimation Evaluation Testbed checkout for typed exchange adaptation")
    server.add_argument("--acquired-stream-stack-root", type=Path,
                        help="Bind pinned ppda/tbrt/mcur/stfe/gsie/set/oit/fdir for acquired calibrated windows and residual monitoring")
    server.add_argument("--measurement-chain-stack-root", type=Path,
                        help="Bind pinned rci/fsrt/jspt directories for native measurement-chain investigations")
    server.add_argument("--geometry-repo", type=Path,
                        help="Bind pinned GTE for retained circle inspection in the shared workbench")
    server.add_argument("--stability-repo", type=Path,
                        help="Bind pinned PLSR for explicitly selected identified model/state assessment")
    server.add_argument("--flat-torus-repo", type=Path,
                        help="Bind pinned flat-torus geodesic reference provider")
    server.add_argument("--curved-surface-repo", type=Path,
                        help="Bind pinned constant-curvature Jacobi transfer provider")
    server.add_argument("--spatial-view-origin", action="append", default=[], help="Exact browser http(s) origin allowed on the read-only /spatial endpoint; repeat to allow more")
    server.add_argument("--computation-repo", type=Path, help="Bind pinned SCR numerical execution")
    server.add_argument("--computation-engine", type=Path, help="Host-built SCR execution-cli (required with --computation-repo)")
    server.add_argument("--free-energy-stack-root", type=Path, help="Bind exact csg/gsie/plsr checkouts for the synthetic variational inference demonstration")
    server.add_argument("--covariance-geometry-repo", type=Path, help="Bind pinned SPD covariance geometry provider")
    server.add_argument("--intrinsic-surface-repo", type=Path, help="Bind pinned mesh edge-path baseline provider")
    server.add_argument("--translation-surface-repo", type=Path, help="Bind pinned square-tiled translation-flow provider")
    server.add_argument("--sp1-prover", type=Path, help="Explicit SCR sp1-host binary; requires computation bindings and --sp1-heat-guest")
    server.add_argument("--sp1-heat-guest", type=Path, help="Exact registered SP1 heat guest ELF; requires --sp1-prover")
    server.add_argument("--python", dest="python_executable", type=Path,
                        help="Python for FSRT/JSPT/GTE adapters; workbench stacks use this running interpreter")
    server.add_argument("--julia-oscillator-runtime", type=Path,
                        help="Pinned checkout containing the instantiated Julia Tsit5 oscillator environment")
    server.add_argument("--julia-executable", type=Path,
                        help="Explicit Julia 1.10 executable paired with --julia-oscillator-runtime")
    health = commands.add_parser("health", help="Check a live session with a bounded read-only request")
    health.add_argument("--url", default="ws://127.0.0.1:8765")
    send = commands.add_parser("send", help="Send a structured request to a running session")
    send.add_argument("type", help="For example session.get, analysis.spectrum, selection.update")
    send.add_argument("--payload", default="{}", help="JSON object (or use --payload-file)")
    send.add_argument("--payload-file", type=Path)
    send.add_argument("--url", default="ws://127.0.0.1:8765")
    send.add_argument("--timeout", type=float, default=15,
                      help="Request deadline in seconds; allow longer for pinned provider workflows")
    watch = commands.add_parser("watch", help="Print shared selection and workbench change events as JSON lines")
    watch.add_argument("--url", default="ws://127.0.0.1:8765")
    inspect = commands.add_parser("inspect", help="Inspect a saved result/workspace without executing it")
    inspect.add_argument("path", type=Path)
    proof = commands.add_parser("proof", help="Freshly verify a retained registered SCR heat proof")
    proof_actions = proof.add_subparsers(dest="proof_command", required=True)
    proof_verify = proof_actions.add_parser("verify", help="Run the full registered-ELF verifier without producing a new proof")
    proof_verify.add_argument("path", type=Path, help="Retained ciw.proved-heat-session.v1 JSON bundle")
    proof_verify.add_argument("--computation-repo", type=Path, required=True)
    proof_verify.add_argument("--computation-engine", type=Path, required=True)
    proof_verify.add_argument("--sp1-prover", type=Path, required=True)
    proof_verify.add_argument("--sp1-heat-guest", type=Path, required=True)
    proof_verify.add_argument("--output", type=Path, required=True, help="New verification report file")
    exchange = commands.add_parser("exchange", help="Inspect external exchange artifacts without admission")
    exchange_actions = exchange.add_subparsers(dest="exchange_command", required=True)
    exchange_inspect = exchange_actions.add_parser("inspect", help="Read-only pinned contract conformance")
    exchange_inspect.add_argument("paths", type=Path, nargs="+")
    exchange_inspect.add_argument("--validator-repo", type=Path, required=True,
                                  help="Explicit checkout/export of the pinned testbed validator")
    telemetry = commands.add_parser("telemetry", help="Retained scalar telemetry operation script and numerical replay")
    telemetry_actions = telemetry.add_subparsers(dest="telemetry_command", required=True)
    telemetry_create = telemetry_actions.add_parser("create", help="Execute PPDA, STFE, GSIE and SET with a fresh replay")
    telemetry_create.add_argument("--source", type=Path, required=True)
    telemetry_create.add_argument("--configuration", type=Path, required=True)
    telemetry_inspect = telemetry_actions.add_parser("inspect", help="Inspect content bindings without running engines")
    telemetry_inspect.add_argument("path", type=Path)
    telemetry_replay = telemetry_actions.add_parser("replay", help="Recompute retained source and compare numerical outputs")
    telemetry_replay.add_argument("path", type=Path)
    for action in (telemetry_create, telemetry_replay):
        for role in ("ppda", "stfe", "gsie", "set"):
            action.add_argument("--" + role + "-repo", type=Path, required=True)
        action.add_argument("--cbsr-repo", type=Path)
        action.add_argument("--output-dir", type=Path, required=True)
    calibrated = commands.add_parser("calibrated-observable", help="Execute and replay the calibrated two-channel process experiment")
    calibrated_actions = calibrated.add_subparsers(dest="calibrated_command", required=True)
    calibrated_create = calibrated_actions.add_parser("create")
    calibrated_create.add_argument("--source", type=Path, required=True)
    calibrated_inspect = calibrated_actions.add_parser("inspect")
    calibrated_inspect.add_argument("path", type=Path)
    calibrated_replay = calibrated_actions.add_parser("replay")
    calibrated_replay.add_argument("path", type=Path)
    for action in (calibrated_create, calibrated_replay):
        for role in ("fsrt", "tbrt", "mcur", "oit", "gsie", "cbsr", "fdir", "set"):
            action.add_argument("--" + role + "-repo", type=Path, required=True)
        action.add_argument("--output-dir", type=Path, required=True)
    identified = commands.add_parser("identified-design", help="Identify dynamics and rank the next budgeted observation")
    identified_actions = identified.add_subparsers(dest="identified_command", required=True)
    identified_create = identified_actions.add_parser("create")
    identified_create.add_argument("--source", type=Path, required=True)
    identified_create.add_argument("--upstream", type=Path, required=True, help="Retained calibrated-observable session")
    identified_inspect = identified_actions.add_parser("inspect")
    identified_inspect.add_argument("path", type=Path)
    identified_replay = identified_actions.add_parser("replay")
    identified_replay.add_argument("path", type=Path)
    for action in (identified_create, identified_replay):
        for role in ("fsrt", "tbrt", "mcur", "oit", "gsie", "cbsr", "fdir", "set", "sidt", "edspt", "ywir"):
            action.add_argument("--" + role + "-repo", type=Path, required=True)
        action.add_argument("--output-dir", type=Path, required=True)
    plsr = commands.add_parser("plsr", help="Import, evaluate, inspect and replay pinned Lyapunov artifacts")
    actions = plsr.add_subparsers(dest="plsr_command", required=True)
    import_model = actions.add_parser("import", help="Validate and retain a sealed model artifact")
    import_model.add_argument("model", type=Path)
    import_model.add_argument("--output", type=Path, required=True)
    evaluate = actions.add_parser("evaluate", help="Evaluate explicit JSON sample inputs and save evidence")
    evaluate.add_argument("--model", type=Path, required=True)
    evaluate.add_argument("--sample", type=Path, required=True)
    evaluate.add_argument("--output-dir", type=Path, required=True)
    inspect_plsr = actions.add_parser("inspect", help="Validate a saved PLSR run without reevaluating it")
    inspect_plsr.add_argument("path", type=Path)
    replay = actions.add_parser("replay", help="Reevaluate saved inputs; retain new evidence and comparison")
    replay.add_argument("path", type=Path)
    replay.add_argument("--output-dir", type=Path, required=True)
    investigation = commands.add_parser(
        "investigation", help="Calibrate RCI observations, evaluate FSRT and retain a shared workspace")
    investigation_actions = investigation.add_subparsers(dest="investigation_command", required=True)
    create = investigation_actions.add_parser("create", help="Create an RCI/FSRT investigation from explicit inputs")
    create.add_argument("--inputs", type=Path, required=True)
    inspect_investigation = investigation_actions.add_parser(
        "inspect", help="Inspect a saved investigation offline without executing its adapters")
    inspect_investigation.add_argument("path", type=Path)
    inspect_investigation.add_argument("--evaluated-at",
                                      help="Aware ISO timestamp for read-only calibration status")
    replay_investigation = investigation_actions.add_parser(
        "replay", help="Replay retained inputs with fresh execution and result identities")
    replay_investigation.add_argument("path", type=Path)
    for action in (create, replay_investigation):
        action.add_argument("--rci-repo", type=Path, required=True,
                            help="Explicit trusted RCI checkout at the pinned revision")
        action.add_argument("--fsrt-repo", type=Path, required=True,
                            help="Explicit trusted FSRT checkout at the pinned revision")
        action.add_argument("--output-dir", type=Path, required=True)
        action.add_argument("--python", dest="python_executable", type=Path,
                            help="Python for external adapters; defaults to this interpreter")
    for action in (create, inspect_investigation, replay_investigation):
        action.add_argument("--json", action="store_true", help="Print the complete machine-readable summary")
    covariance = commands.add_parser("covariance", help="Run pinned JSPT covariance operations on a saved investigation")
    covariance_replay = commands.add_parser("covariance-replay", help="Replay retained JSPT covariance inputs")
    covariance.add_argument("--parameters", type=Path, required=True,
                            help="JSON scientific operation parameters, never executable code")
    for action in (covariance, covariance_replay):
        action.add_argument("path", type=Path)
        action.add_argument("--jspt-repo", type=Path, required=True)
        action.add_argument("--output-dir", type=Path, required=True)
        action.add_argument("--adapter-python", "--python", dest="python_executable", type=Path)
        action.add_argument("--json", action="store_true")
    geodesic = commands.add_parser(
        "geodesic", help="Project declared circle telemetry and retain a shared investigation")
    geodesic_actions = geodesic.add_subparsers(dest="geodesic_command", required=True)
    geodesic_create = geodesic_actions.add_parser("create", help="Retain exact request bytes and execute GTE")
    geodesic_create.add_argument("--inputs", type=Path, required=True)
    geodesic_inspect = geodesic_actions.add_parser("inspect", help="Inspect retained GTE evidence without executing code")
    geodesic_inspect.add_argument("path", type=Path)
    geodesic_replay = geodesic_actions.add_parser("replay", help="Replay retained GTE inputs with fresh result identities")
    geodesic_replay.add_argument("path", type=Path)
    for action in (geodesic_create, geodesic_replay):
        action.add_argument("--gte-repo", type=Path, required=True)
        action.add_argument("--output-dir", type=Path, required=True)
        action.add_argument("--python", dest="python_executable", type=Path)
    for action in (geodesic_create, geodesic_inspect, geodesic_replay):
        action.add_argument("--json", action="store_true", help="Print covariance, identities and complete provenance")
    energy = commands.add_parser("energy", help="Capture GPU energy or replay retained energy/accuracy logs")
    energy_actions = energy.add_subparsers(dest="energy_command", required=True)
    energy_probe = energy_actions.add_parser("probe", help="Read an actual NVML counter without running a workload")
    energy_probe.add_argument("--gpu-index", type=int, default=0)
    energy_status = energy_actions.add_parser("status", help="Report GPU energy availability without treating it as a lab gateway")
    energy_status.add_argument("--gpu-index", type=int, default=0)
    energy_measure = energy_actions.add_parser("measure", help="One NVML counter read; measurement only")
    energy_measure.add_argument("--gpu-index", type=int, default=0)
    energy_replay_gate = energy_actions.add_parser("replay-log", help="Recompute a retained energy log without opening a device")
    energy_replay_gate.add_argument("path", type=Path)
    energy_record = energy_actions.add_parser("record", help="Run a bounded Gaussian GPU experiment into a new directory")
    energy_record.add_argument("--problem", type=Path, required=True)
    energy_record.add_argument("--output-dir", type=Path, required=True)
    energy_record.add_argument("--duration", type=float, default=3)
    energy_record.add_argument("--replicas", type=int, default=4096)
    energy_record.add_argument("--iterations", type=int)
    energy_record.add_argument("--gpu-index", type=int, default=0)
    energy_record.add_argument("--max-batches", type=int, default=2048)
    energy_record.add_argument("--warmup-batches", type=int, default=2)
    energy_record.add_argument("--idle-duration", type=float, default=1)
    energy_replay = energy_actions.add_parser("replay", help="Recompute analysis from raw logs; never acquire new measurements")
    energy_replay.add_argument("path", type=Path)
    energy_replay.add_argument("--output", type=Path)
    julia = commands.add_parser("julia-oscillator", help="Run, inspect or replay the pinned Julia Tsit5 oscillator")
    julia_actions = julia.add_subparsers(dest="julia_command", required=True)
    julia_create = julia_actions.add_parser("create", help="Execute a bounded data-only Julia request")
    julia_create.add_argument("--source", type=Path, required=True)
    julia_create.add_argument("--julia-oscillator-runtime", type=Path, required=True)
    julia_create.add_argument("--julia-executable", type=Path, required=True)
    julia_create.add_argument("--output", type=Path, required=True)
    julia_create.add_argument("--recording-output", type=Path,
                             help="Optional run.v1 trajectory projection for the read-only Godot viewport")
    julia_inspect = julia_actions.add_parser("inspect", help="Inspect a retained Julia session without Julia")
    julia_inspect.add_argument("path", type=Path)
    julia_preview = julia_actions.add_parser("preview", help="Run a bounded offline educational what-if preview")
    julia_preview.add_argument("--source", type=Path, required=True)
    julia_preview.add_argument("--set", dest="overrides", action="append", default=[],
                               metavar="PATH=VALUE",
                               help="Override model or initial-state value; repeat for independent what-if controls")
    julia_preview.add_argument("--output", type=Path, required=True)
    julia_sensitivity = julia_actions.add_parser("sensitivity", help="Run a bounded offline educational sensitivity sweep")
    julia_sensitivity.add_argument("--source", type=Path, required=True)
    julia_sensitivity.add_argument("--path", required=True, metavar="PATH",
                                  help="Declared model or initial-state path to vary")
    julia_sensitivity.add_argument("--value", dest="values", action="append", required=True, type=float,
                                  help="Sweep value; repeat for two to nine distinct values")
    julia_sensitivity.add_argument("--output", type=Path, required=True)
    julia_replay = julia_actions.add_parser("replay", help="Replay a retained Julia session with a fresh occurrence")
    julia_replay.add_argument("path", type=Path)
    julia_replay.add_argument("--julia-oscillator-runtime", type=Path, required=True)
    julia_replay.add_argument("--julia-executable", type=Path, required=True)
    julia_replay.add_argument("--output", type=Path, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    command_line = list(sys.argv[1:] if argv is None else argv)
    if command_line and command_line[0] == "system":
        from .system_cli import main as system_main
        return system_main(command_line[1:])
    if command_line and command_line[0] == "legibility":
        from .legibility_cli import main as legibility_main
        return legibility_main(command_line[1:])
    args = parser().parse_args(argv)
    try:
        if args.command == "dependencies":
            # Session restore validates offline, but normally exports records.
            # Use disposable output so inspecting never writes beside the source.
            import tempfile
            with tempfile.TemporaryDirectory(prefix="ciw-dependencies-") as temporary:
                retained = Session.from_workspace(args.path, Path(temporary))
                print_json(retained.dependency_status())
            return 0
        if args.command == "doctor":
            from .doctor import diagnose
            report = diagnose(args.profile, stack_root=args.stack_root, engine=args.engine, binding=args.binding)
            print_json(report)
            return 0 if report["status"] == "preflight_passed" else 2
        if args.command == "capabilities":
            from .capabilities import catalog, get
            if args.capabilities_command in (None, "list"):
                print_json(catalog())
            else:
                print_json(get(args.capability_id))
            return 0
        if args.command == "math":
            from .learning import run_cli
            return run_cli(args)
        if args.command == "energy":
            if args.energy_command == "probe":
                from .energy_bench import probe
                print_json(probe(args.gpu_index))
            elif args.energy_command == "status":
                from .energy_gateway import status
                print_json(status(args.gpu_index))
            elif args.energy_command == "measure":
                from .energy_gateway import measure
                print_json(measure(args.gpu_index))
            elif args.energy_command == "replay-log":
                from .energy_gateway import replay_log
                print_json(replay_log(args.path))
            elif args.energy_command == "record":
                from .energy_bench import capture
                log, report = capture(read_json(args.problem), args.output_dir,
                    minimum_duration_s=args.duration, replicas=args.replicas, iterations=args.iterations,
                    device_index=args.gpu_index, max_batches=args.max_batches,
                    warmup_batches=args.warmup_batches, idle_duration_s=args.idle_duration)
                print_json({"log_file": str(args.output_dir / "log.json"), "log_digest": log["log_digest"],
                            "measurement": report["measurement"], "comparison": report["comparison"]})
            else:
                from .energy_records import analyze, MAX_BYTES
                from .adapters.subprocess import _json
                with args.path.open("rb") as stream:
                    raw = stream.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError("Energy source exceeds its exact-byte size bound")
                report = analyze(_json(raw))
                if args.output:
                    _write_new_proof_report(args.output, report)
                print_json(report)
        elif args.command == "julia-oscillator":
            from .julia_oscillator import workflow as julia_workflow
            if args.julia_command == "inspect":
                julia_workflow._validate(json.loads(args.path.read_bytes().decode("utf-8")))
                print_json({"status": "inspectable", "schema": "ciw.julia-oscillator-session.v1"})
            elif args.julia_command == "preview":
                from .julia_oscillator import validate_source
                from .model_education import parse_overrides, preview_oscillator
                raw = args.source.read_bytes()
                source = validate_source(raw)
                preview = preview_oscillator(source, parse_overrides(args.overrides))
                write_json(args.output, preview)
                print_json({"status": "preview_only", "preview_file": str(args.output),
                            "preview_id": preview["preview_id"],
                            "base_source_digest": preview["base_source_digest"],
                            "retention": preview["authority"]["retention"]})
            elif args.julia_command == "sensitivity":
                from .julia_oscillator import validate_source
                from .model_education import sensitivity_oscillator
                source = validate_source(args.source.read_bytes())
                sweep = sensitivity_oscillator(source, args.path, args.values)
                write_json(args.output, sweep)
                print_json({"status": "sensitivity_preview_only", "sweep_file": str(args.output),
                            "sweep_id": sweep["sweep_id"], "swept_path": sweep["swept_path"],
                            "case_count": len(sweep["cases"]),
                            "retention": sweep["authority"]["retention"]})
            else:
                bindings = {"julia_runtime": args.julia_oscillator_runtime, "julia": args.julia_executable}
                if args.julia_command == "create":
                    bundle = julia_workflow.create_session(args.source.read_bytes(), bindings)
                    write_json(args.output, bundle)
                    if args.recording_output is not None:
                        write_json(args.recording_output, julia_workflow.to_run(bundle["steps"][0]["result"]["data"]))
                    print_json({"status": "completed", "bundle_file": str(args.output),
                                "bundle_id": bundle["bundle_digest"], "operation_id": julia_workflow.operation,
                                **({"recording_file": str(args.recording_output)} if args.recording_output is not None else {})})
                else:
                    original = json.loads(args.path.read_bytes().decode("utf-8"))
                    replay = julia_workflow.replay_session(original, bindings)
                    write_json(args.output, replay["session"])
                    print_json({"status": "completed", "bundle_file": str(args.output),
                                "bundle_id": replay["session"]["bundle_digest"],
                                "replay_id": replay["replay_receipt"]["replay_id"]})
        elif args.command == "demo":
            run = make_demo_run()
            write_json(args.output, run)
            print_json({"recording_file": str(args.output), "run_id": run["run_id"],
                        "evidence_id": run["evidence_id"], "sample_count": len(run["time_s"])})
        elif args.command == "export":
            from .usda_export import SCHEMA, export_payload, write_usda
            text = export_payload(read_json(args.path))
            write_usda(args.output, text)
            result = {"usda_file": str(args.output), "schema": SCHEMA,
                      "authority": "inspection_copy_not_observation"}
            if args.compare:
                from .usda_export import compare_export
                result["compare"] = compare_export(read_json(args.path), text)
            if args.usd_bin is not None:
                from .usda_export import open_with_runtime
                result["open"] = open_with_runtime(args.output, args.usd_bin)
            print_json(result)
        elif args.command == "bindings":
            from .language_bindings import catalog, create, inspect_binding
            if args.create:
                print_json(create(args.binding_id or "julia-oscillator", args.path, args.output,
                                  julia=args.julia, runtime=args.runtime))
            else:
                print_json(catalog() if args.binding_id is None else inspect_binding(args.binding_id, args.path))
        elif args.command == "chart":
            from .design_manifold import apply_chart
            point = [float(item) for item in args.point.split(",")]
            scales = None if args.scales is None else [float(item) for item in args.scales.split(",")]
            print_json(apply_chart(args.chart_id, point, source_frame=args.source_frame,
                                   target_frame=args.target_frame, scales=scales))
        elif args.command == "analyze":
            run = load_run(args.recording)
            session = Session(run, args.output_dir)
            interval = [args.start, args.end if args.end is not None else run["metadata"]["duration_s"]]
            response = session.handle({"protocol_version": 1, "request_id": uuid.uuid4().hex,
                                       "type": "analysis." + args.operation,
                                       "payload": {"channel": (session.selection["channel"] if args.channel is None
                                                               else args.channel),
                                                   "interval_s": interval}})
            print_json(response)
            if response["type"] == "error":
                return 2
            session.save_workspace(args.output_dir / "workspace.json")
        elif args.command == "serve":
            if not 1 <= args.port <= 65535:
                raise ValueError("port must be between 1 and 65535")
            session = load_server_session(args)
            if args.fsrt_repo is not None:
                from .investigation import bind_fsrt
                bind_fsrt(session, args.fsrt_repo, python_executable=args.python_executable)
            if args.jspt_repo is not None:
                from .covariance_workflow import bind_jspt
                bind_jspt(session, args.jspt_repo, python_executable=args.python_executable)
            if args.gte_repo is not None:
                from .geodesic import bind_gte
                bind_gte(session, args.gte_repo, python_executable=args.python_executable)
            if (args.julia_oscillator_runtime is None) != (args.julia_executable is None):
                raise ValueError("--julia-oscillator-runtime and --julia-executable must be supplied together")
            if args.julia_oscillator_runtime is not None:
                session.workbench.bind_workflow("julia-oscillator", {
                    "julia_runtime": args.julia_oscillator_runtime, "julia": args.julia_executable})
            if args.calibrated_stack_root is not None or args.identified_stack_root is not None:
                from .calibrated_observable import ROLES
                stack_root = args.calibrated_stack_root or args.identified_stack_root
                session.workbench.bind_workflow("calibrated-observable", {
                    role: stack_root / role for role in ROLES})
                if args.identified_stack_root is not None:
                    from .identified_design import ROLES as DESIGN_ROLES
                    session.workbench.bind_workflow("identified-design", {
                        role: stack_root / role for role in DESIGN_ROLES})
            if args.esm_binding is not None:
                session.workbench.bind_candidate_adapter(read_json(args.esm_binding))
            if args.telemetry_stack_root is not None:
                from .telemetry import ROLES as TELEMETRY_ROLES
                session.workbench.bind_workflow("telemetry", {role: args.telemetry_stack_root / role for role in TELEMETRY_ROLES})
            if args.calibrated_window_stack_root is not None:
                from .calibrated_window import ROLES as WINDOW_ROLES
                session.workbench.bind_workflow("calibrated-window", {role: args.calibrated_window_stack_root / role for role in WINDOW_ROLES})
            if args.schematic_repo is not None:
                session.workbench.bind_workflow("schematic-assessment", {"sra": args.schematic_repo})
            if args.schematic_companions_root is not None:
                root = args.schematic_companions_root
                session.workbench.bind_workflow("schematic-assessment", {"sra": root / "sra"})
                session.workbench.bind_workflow("schematic-companions", {role: root / role for role in ("sra", "jspt", "plsr")})
            if args.construction_repo is not None:
                session.workbench.bind_workflow("bim-quantity", {"cse": args.construction_repo})
            if args.acquisition_repo is not None:
                session.workbench.bind_workflow("acquired-dataset", {"ppda": args.acquisition_repo})
            if args.exchange_set_repo is not None:
                session.workbench.bind_workflow("instrument-exchange", {"set": args.exchange_set_repo})
            if args.acquired_stream_stack_root is not None:
                root = args.acquired_stream_stack_root
                from .calibrated_window import ROLES as WINDOW_ROLES
                session.workbench.bind_workflow("acquired-dataset", {"ppda": root / "ppda"})
                for kind in ("calibrated-window", "acquired-calibrated-window"):
                    session.workbench.bind_workflow(kind, {role: root / role for role in WINDOW_ROLES})
                session.workbench.bind_workflow("residual-monitor", {role: root / role for role in ("oit", "fdir")})
            if args.measurement_chain_stack_root is not None:
                session.workbench.bind_workflow("measurement-chain", {
                    role: args.measurement_chain_stack_root / role for role in ("rci", "fsrt", "jspt")})
            if args.geometry_repo is not None:
                session.workbench.bind_workflow("geometric-circle", {"gte": args.geometry_repo})
            if args.stability_repo is not None:
                session.workbench.bind_workflow("identified-stability", {"plsr": args.stability_repo})
            if args.flat_torus_repo is not None:
                session.workbench.bind_workflow("flat-torus-reference", {"ftr": args.flat_torus_repo})
            if args.curved_surface_repo is not None:
                session.workbench.bind_workflow("curved-path-transfer", {"csg": args.curved_surface_repo})
            if args.free_energy_stack_root is not None:
                session.workbench.bind_workflow("variational-free-energy", {role: args.free_energy_stack_root / role for role in ("csg", "gsie", "plsr")})
            if args.covariance_geometry_repo is not None:
                session.workbench.bind_workflow("covariance-geometry", {"cggt": args.covariance_geometry_repo})
            if args.intrinsic_surface_repo is not None:
                session.workbench.bind_workflow("mesh-path", {"isgt": args.intrinsic_surface_repo})
            if args.translation_surface_repo is not None:
                session.workbench.bind_workflow("translation-flow", {"tsde": args.translation_surface_repo})
            if (args.computation_repo is None) != (args.computation_engine is None):
                raise ValueError("--computation-repo and --computation-engine must be supplied together")
            if args.computation_repo is not None:
                session.workbench.bind_workflow("numerical-heat", {"scr": args.computation_repo, "engine": args.computation_engine})
            if (args.sp1_prover is None) != (args.sp1_heat_guest is None):
                raise ValueError("--sp1-prover and --sp1-heat-guest must be supplied together")
            if args.sp1_prover is not None:
                if args.computation_repo is None:
                    raise ValueError("SP1 requires --computation-repo and --computation-engine")
                session.workbench.bind_workflow("proved-heat", {"scr": args.computation_repo,
                    "engine": args.computation_engine, "prover": args.sp1_prover, "guest": args.sp1_heat_guest})
            if args.esm_telemetry_binding is not None:
                configuration = read_json(args.esm_telemetry_binding)
                if "ppda" not in configuration.get("runtime", {}).get("repositories", {}):
                    raise ValueError("--esm-telemetry-binding must declare the telemetry provider set")
                session.workbench.bind_candidate_adapter(configuration)
            asyncio.run(run_server(session, args.port, args.bind, spatial_view_origins=args.spatial_view_origin))
        elif args.command == "health":
            print_json(asyncio.run(health_remote(args.url)))
        elif args.command == "send":
            payload = read_json(args.payload_file) if args.payload_file else loads_json(args.payload)
            if not isinstance(payload, dict):
                raise ValueError("payload must be a JSON object")
            if not math.isfinite(args.timeout) or not 0 < args.timeout <= 3600:
                raise ValueError("timeout must be finite, positive and at most 3600 seconds")
            response = asyncio.run(request_remote(args.url, args.type, payload, timeout_s=args.timeout))
            print_json(response)
            return 0 if response["type"] == "response" else 2
        elif args.command == "watch":
            asyncio.run(watch_remote(args.url))
        elif args.command == "inspect":
            print_json(read_json(args.path))
        elif args.command == "proof":
            from .adapters.subprocess import _json
            from .exchange import _read
            from .proved_heat import ProvedHeatWorkflow, MAX_BYTES
            if args.output.exists():
                raise ValueError("Use a new verification report path to preserve previous occurrences")
            report = ProvedHeatWorkflow().verify_session(_json(_read(args.path, MAX_BYTES)), {
                "scr": args.computation_repo, "engine": args.computation_engine,
                "prover": args.sp1_prover, "guest": args.sp1_heat_guest})
            _write_new_proof_report(args.output, report)
            print_json({"verification_file": str(args.output), "verification": report})
        elif args.command == "exchange":
            from .exchange import inspect_exchange
            print_json(inspect_exchange(args.paths, validator_repo=args.validator_repo))
        elif args.command == "telemetry":
            from .telemetry import create_session, inspect_session, replay_session, read_session, save_session
            if args.telemetry_command == "inspect":
                print_json(inspect_session(read_session(args.path)))
            else:
                repositories = {role: getattr(args, role + "_repo") for role in ("ppda", "stfe", "gsie", "set")}
                if args.cbsr_repo is not None:
                    repositories["cbsr"] = args.cbsr_repo
                if args.telemetry_command == "create":
                    from .exchange import _read
                    from .telemetry import MAX_BYTES
                    bundle = create_session(_read(args.source, MAX_BYTES), read_json(args.configuration), repositories)
                    path = save_session(bundle, args.output_dir)
                    print_json({"session_file": str(path), "inspection": inspect_session(bundle)})
                else:
                    result = replay_session(read_session(args.path), repositories)
                    path = save_session(result["session"], args.output_dir)
                    print_json({"session_file": str(path), "replay_receipt": result["replay_receipt"]})
        elif args.command == "calibrated-observable":
            from .calibrated_observable import (ROLES, MAX_BYTES, create_session, inspect_session,
                                               replay_session, read_session, save_session)
            if args.calibrated_command == "inspect":
                print_json(inspect_session(read_session(args.path)))
            else:
                repositories = {role: getattr(args, role + "_repo") for role in ROLES}
                if args.calibrated_command == "create":
                    from .exchange import _read
                    bundle = create_session(_read(args.source, MAX_BYTES), repositories)
                    path = save_session(bundle, args.output_dir)
                    print_json({"session_file": str(path), "inspection": inspect_session(bundle)})
                else:
                    result = replay_session(read_session(args.path), repositories)
                    path = save_session(result["session"], args.output_dir)
                    print_json({"session_file": str(path), "replay_receipt": result["replay_receipt"]})
        elif args.command == "identified-design":
            from .identified_design import (ROLES, MAX_BYTES, create_session, inspect_session,
                                            replay_session, read_session, save_session)
            if args.identified_command == "inspect":
                print_json(inspect_session(read_session(args.path)))
            else:
                repositories = {role: getattr(args, role + "_repo") for role in ROLES}
                if args.identified_command == "create":
                    from .exchange import _read
                    bundle = create_session(_read(args.source, MAX_BYTES), read_session(args.upstream), repositories)
                    path = save_session(bundle, args.output_dir)
                    print_json({"session_file": str(path), "inspection": inspect_session(bundle)})
                else:
                    result = replay_session(read_session(args.path), repositories)
                    path = save_session(result["session"], args.output_dir)
                    print_json({"session_file": str(path), "replay_receipt": result["replay_receipt"]})
        elif args.command == "investigation":
            from .investigation import create_investigation, inspect_investigation, replay_investigation
            if args.investigation_command == "inspect":
                result = inspect_investigation(args.path, evaluated_at=args.evaluated_at)
            else:
                arguments = {"rci_repo": args.rci_repo, "fsrt_repo": args.fsrt_repo,
                             "output_dir": args.output_dir, "python_executable": args.python_executable}
                if args.investigation_command == "create":
                    inputs = read_json(args.inputs)
                    if not isinstance(inputs, dict):
                        raise ValueError("investigation inputs must be a JSON object")
                    result = create_investigation(inputs, **arguments)
                else:
                    result = replay_investigation(args.path, **arguments)
            if args.json:
                print_json(result)
            else:
                print_investigation(result)
        elif args.command in {"covariance", "covariance-replay"}:
            from .covariance_workflow import execute_covariance, replay_covariance
            arguments = {"jspt_repo": args.jspt_repo, "output_dir": args.output_dir,
                         "python_executable": args.python_executable}
            if args.command == "covariance":
                parameters = read_json(args.parameters)
                if not isinstance(parameters, dict):
                    raise ValueError("covariance parameters must be a JSON object")
                result = execute_covariance(args.path, parameters=parameters, **arguments)
            else:
                result = replay_covariance(args.path, **arguments)
            if args.json:
                print_json(result)
            else:
                print_investigation(result)
        elif args.command == "geodesic":
            from .geodesic import create_investigation, inspect_investigation, replay_investigation
            if args.geodesic_command == "inspect":
                result = inspect_investigation(args.path)
            else:
                arguments = {"gte_repo": args.gte_repo, "output_dir": args.output_dir,
                             "python_executable": args.python_executable}
                if args.geodesic_command == "create":
                    result = create_investigation(args.inputs, **arguments)
                else:
                    result = replay_investigation(args.path, **arguments)
            if args.json:
                print_json(result)
            else:
                print_investigation(result)
        elif args.command == "plsr":
            # The optional engine is loaded only through this terminal boundary.
            from .plsr import evaluate_run, import_model, inspect_run, replay_run
            if args.plsr_command == "import":
                result = import_model(args.model, args.output)
            elif args.plsr_command == "evaluate":
                result = evaluate_run(args.model, args.sample, args.output_dir)
            elif args.plsr_command == "inspect":
                result = inspect_run(args.path)
            else:
                result = replay_run(args.path, args.output_dir)
            print_json(result)
            if args.plsr_command == "replay" and not result["bundle"]["replay_of"]["record_digest_matches"]:
                return 3
        return 0
    except KeyboardInterrupt:
        return 130 if args.command == "energy" and args.energy_command == "record" else 0
    except AdapterRefusal as exc:
        print(f"ciw: {exc.code}: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError, TimeoutError, WebSocketException) as exc:
        print(f"ciw: {exc}", file=sys.stderr)
        return 2
