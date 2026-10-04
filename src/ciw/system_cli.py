"""One-terminal commands for configured scientific systems."""
import argparse
import base64
import json
from pathlib import Path
import sys
import tempfile


def _print(value):
    print(json.dumps(value, indent=2, allow_nan=False))


def summary(session, *, full=False):
    from .system_view import inspection_summary
    return inspection_summary(session, full=full)


def _review(destination, session, overview):
    from .system_view import write_system_review
    return write_system_review(Path(destination) / "review.html", session)


def _retain_specification(session, specification):
    """Import exact declared bytes before compiling the linked occurrence."""
    from .system_spec import validate_spec
    specification = validate_spec(specification)
    raw = json.dumps(specification, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return session.workbench.add_source({"kind": "system-specification",
        "label": specification["configuration_id"], "bytes_b64": base64.b64encode(raw).decode("ascii")})


def parser():
    root = argparse.ArgumentParser(prog="ciw system")
    commands = root.add_subparsers(dest="action", required=True)
    demo = commands.add_parser("demo")
    demo.add_argument("--output-dir", type=Path, required=True)
    demo.add_argument("--skip-worker", action="store_true", help="Omit subprocess parity run")
    compile_command = commands.add_parser("compile")
    compile_command.add_argument("specification", type=Path)
    compile_command.add_argument("--output", type=Path)
    run = commands.add_parser("run")
    run.add_argument("specification", type=Path)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--engine", choices=("local", "subprocess", "oci"), default="local")
    run.add_argument("--image")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("workspace", type=Path)
    inspect.add_argument("--full", action="store_true", help="Include retained trajectories and detailed reports")
    review = commands.add_parser("review")
    review.add_argument("workspace", type=Path)
    review.add_argument("--output", type=Path, required=True, help="Standalone HTML review destination")
    study = commands.add_parser("study")
    study.add_argument("workspace", type=Path)
    study.add_argument("--output-dir", type=Path, required=True)
    study.add_argument("--step-s", type=float, action="append", help="Base step then decreasing subdivisions; repeat 2 to 6 times")
    verify = commands.add_parser("verify")
    verify.add_argument("workspace", type=Path)
    verify.add_argument("--output-dir", type=Path, required=True)
    worker = commands.add_parser("worker")
    worker.add_argument("specification", type=Path)
    worker.add_argument("--output", type=Path, required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    from .session import Session, read_json, write_json
    from .system_spec import compile_spec, demo_spec
    from .system_workflow import COMPILE, RUN, VERIFY, COMPARE, STUDY, execute, source_run, run_specification
    try:
        if args.action == "compile":
            plan = compile_spec(read_json(args.specification))
            if args.output:
                write_json(args.output, plan)
            _print(plan)
            return 0
        if args.action == "worker":
            from .system_models import simulate
            write_json(args.output, simulate(compile_spec(read_json(args.specification))))
            return 0
        if args.action == "inspect":
            with tempfile.TemporaryDirectory(prefix="net-system-inspection-") as directory:
                _print(summary(Session.from_workspace(args.workspace, Path(directory)), full=args.full))
            return 0
        if args.action == "review":
            from .system_view import write_system_review
            with tempfile.TemporaryDirectory(prefix="net-system-review-") as directory:
                session = Session.from_workspace(args.workspace, Path(directory))
                write_system_review(args.output, session)
            _print({"review": str(args.output), "physical_validation_status": "not_assessed", "canonical_admission": False})
            return 0
        destination = args.output_dir
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("Use an empty output directory to preserve prior runs")
        destination.mkdir(parents=True, exist_ok=True)
        if args.action == "study":
            session = Session.from_workspace(args.workspace, destination)
            plans = [record for record in session.results.values() if record["operation_id"] == COMPILE]
            if not plans:
                raise ValueError("Workspace has no retained scientific system compilation")
            for plan in plans:
                parameters = {"plan": plan, "source_result_id": plan["result_id"]}
                if args.step_s is not None:
                    parameters["step_sizes_s"] = args.step_s
                execute(session, STUDY, parameters)
        elif args.action == "verify":
            session = Session.from_workspace(args.workspace, destination)
            candidates = [record for record in session.results.values() if record["operation_id"] == RUN]
            if not candidates:
                raise ValueError("Workspace has no scientific system simulation")
            for candidate in candidates:
                execute(session, VERIFY, {"candidate": candidate, "source_result_id": candidate["result_id"]})
        elif args.action == "run":
            spec = read_json(args.specification)
            session = Session(source_run([spec]), destination)
            source = _retain_specification(session, spec)
            run_specification(session, spec, engine=args.engine, image=args.image, source_id=source["source_id"])
        else:
            specs = [demo_spec(cells=n) for n in (128, 64, 4)] + [demo_spec(cells=16, boundary="fixed", force_n=0.0)]
            for target, label in ((64, "thermal-reduced-64"), (128, "thermal-identity")):
                specs[0]["representations"].append({"representation_id": label, "kind": "cell-average.v1",
                    "source_node": "thermal", "target_cells": target,
                    "assumptions": ["Equal-volume blocks; identity when target and source counts agree."]})
            session = Session(source_run(specs), destination)
            sources = [_retain_specification(session, spec) for spec in specs]
            results = [run_specification(session, spec, source_id=source["source_id"])
                       for spec, source in zip(specs, sources)]
            execute(session, STUDY, {"plan": results[0]["plan"], "source_result_id": results[0]["plan"]["result_id"]})
            for reduced in results[1:3]:
                left, right = results[0]["candidate"], reduced["candidate"]
                execute(session, COMPARE, {"left": left, "right": right, "source_result_id": left["result_id"],
                                           "source_result_ids": [left["result_id"], right["result_id"]]})
            if not args.skip_worker:
                worker = run_specification(session, specs[0], engine="subprocess", source_id=sources[0]["source_id"])
                left, right = results[0]["candidate"], worker["candidate"]
                execute(session, COMPARE, {"left": left, "right": right, "source_result_id": left["result_id"],
                                           "source_result_ids": [left["result_id"], right["result_id"]]})
            for spec in specs:
                write_json(destination / (spec["configuration_id"] + ".json"), spec)
        overview = summary(session)
        write_json(destination / "summary.json", overview)
        _review(destination, session, overview)
        _print({"directory": str(destination), "workspace": str(destination / "workspace.json"),
                "configurations": len(overview["configurations"]), "numerical_reports": len(overview["numerical_reports"]),
                "comparisons": len(overview["comparisons"]), "temporal_studies": len(overview["temporal_studies"]),
                "physical_validation_status": "not_assessed"})
        if args.action == "study":
            return 0 if all(item["report"].get("status") == "PASS" for item in overview["temporal_studies"]) else 2
        return 0 if all(item["report"].get("status") == "PASS" for item in overview["numerical_reports"]) else 2
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
