"""Read-only navigation across the existing NET command and Session authorities."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .operator_commands import COMMANDS


def catalog() -> dict:
    from .control_plane import builtin_registry
    from .doctor import PROFILES
    from .operator_provision import catalog as provision_catalog
    from .scientific import catalog as scientific_catalog
    from .workbench import Workbench

    navigation = scientific_catalog()
    routes = {row["source_kind"]: row for row in navigation["operations"]}
    operations = {row["source_kind"]: row for row in Workbench().describe_operations()
                  if "source_kind" in row}
    workflows = []
    for requirement in provision_catalog()["workflows"]:
        kind = requirement["workflow"]
        workflows.append({**requirement, **operations[kind],
            "scientific_cli": kind in routes,
            "capabilities": routes.get(kind, {}).get("capabilities", []),
            "instrument_highlights": routes.get(kind, {}).get("instrument_highlights", []),
            "binding_status": "available_in_unbound_session" if operations[kind]["available"] else "explicit_bindings_required",
            "setup_command": f"net provision --monorepo ROOT --workflow {kind} --plan",
            "execution_entry": f"net science run --kind {kind} --help" if kind in routes else "ciw serve --help; existing Session operation.execute",
            "qualification": "not_performed_by_catalog"})
    commands = [{"command": "net " + name, "purpose": purpose, "help_command": f"net {name} --help"}
                for name, _module, purpose in COMMANDS]
    commands.extend({"command": "net " + name, "purpose": purpose, "help_command": f"net {name} --help"}
                    for name, purpose in (
                        ("polymer leakage", "Assess coolant volume and material mass balances with the explicitly pinned native provider"),
                        ("providers", "Read declared oscillator provider manifests"),
                        ("capabilities", "Filter declared oscillator operation capabilities"),
                        ("inspect", "Inspect retained records without execution"),
                        ("compare", "Compare retained observation streams"),
                        ("run", "Execute an explicitly declared NET experiment"),
                        ("demo", "Run the retained oscillator statistics and spectrum graph")))
    return {"schema": "ciw.operator-catalog.v1", "read_only": True,
            "authorizes_execution": False, "qualification": "not_performed_by_catalog",
            "commands": commands, "workflows": workflows,
            "builtin_providers": builtin_registry(bind=False).catalog(),
            "doctor_profiles": list(PROFILES),
            "first_use_command": "net start --output-dir NEW",
            "workbench_command": "net workbench --output-dir STATE",
            "binding_source": "explicit_host_configuration_only",
            "boundaries": navigation["other_boundaries"],
            "source_inventory": {"command": "python scripts/superrepo.py list",
                "qualification_command": "python scripts/superrepo.py check --output-dir NEW",
                "scope": "source_checkout_only; imports_do_not_install_or_bind_providers"}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="net catalog", description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--audit", action="store_true", help="Audit scientific review coverage; does not run qualification")
    parser.add_argument("--repository", type=Path, help="With --audit, check referenced source/test files in an explicit checkout")
    args = parser.parse_args(argv)
    if args.repository is not None and not args.audit:
        parser.error("--repository requires --audit")
    if args.audit:
        from .scientific_audit import audit
        value = audit(repository=args.repository)
        if args.json:
            print(json.dumps(value, indent=2, allow_nan=False))
        else:
            print("NET scientific coverage audit: " + value["status"])
            print("Commands: {commands}; workflows: {workflows}; ladder levels: {ladder_levels}".format(**value["counts"]))
            print("Declaration coverage only; referenced tests were not run and physical validation is not established.")
            for item in value["issues"]:
                print(json.dumps(item, sort_keys=True))
            for item in value["open_obligations"]:
                print(item["id"] + ": " + item["detail"])
        return 1 if value["issues"] else 0
    value = catalog()
    if args.json:
        print(json.dumps(value, indent=2, allow_nan=False))
        return 0
    print("NET instrument catalog — read-only; no scientific qualification performed")
    print("Start: " + value["first_use_command"])
    print("Persistent session: " + value["workbench_command"])
    print("Commands (append --help for usage):")
    for row in value["commands"]:
        print(f"  {row['command']}: {row['purpose']}")
    print("Existing Session workflows and required provider roles:")
    for row in value["workflows"]:
        roles = sorted(set(row["providers"]) | set(row["external_bindings"]))
        required = ", ".join(roles) if roles else "built-in; no external provider binding"
        print(f"  {row['workflow']}: {required}; {row['binding_status']}")
    print("Exact pin declarations: net provision --list")
    print("Local prerequisites: net provision --monorepo ROOT --workflow KIND --plan")
    print("Dependency profiles: " + ", ".join(value["doctor_profiles"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
