"""Both scientific families retain their source and occurrence contracts."""
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from ciw import atmosphere_workflow as atmosphere, system_workflow as system
from ciw.atmosphere_contract import example_request
from ciw.session import Session
from ciw.system_spec import demo_spec


def _registry():
    registry = atmosphere.registry()
    described = {row["operation_id"] for row in registry.describe()}
    assert set(system.OPERATION_IDS) | {atmosphere.COMPILE, atmosphere.VERIFY} <= described
    return registry


def _session(tmp_path, family):
    source = (system.source_run([demo_spec()]) if family == "system"
              else atmosphere.make_source(example_request()))
    return Session(source, tmp_path / family, operations=_registry())


@pytest.mark.parametrize("family", ["system", "atmosphere"])
def test_combined_registry_restores_exact_occurrences_without_numerical_replay(tmp_path, family):
    session = _session(tmp_path, family)
    if family == "system":
        system.run_specification(session, demo_spec())
    else:
        candidate = atmosphere._execute(session, atmosphere.COMPILE, {})
        assert candidate["status"] == "completed"
        checked = atmosphere._execute(session, atmosphere.VERIFY, {"candidate": candidate["result"]})
        assert checked["status"] == "completed"
    path = session.save_workspace(tmp_path / "workspace.json")
    with ExitStack() as stack:
        for provider in ("ciw.system_models.simulate", "ciw.system_models.verify_simulation",
                         "ciw.atmosphere_compiler.compile_atmosphere", "ciw.atmosphere_verification.verify"):
            stack.enter_context(patch(provider, side_effect=AssertionError("numerical replay")))
        system_dependencies = stack.enter_context(patch(
            "ciw.system_workflow.validate_saved_dependencies", wraps=system.validate_saved_dependencies))
        atmosphere_dependencies = stack.enter_context(patch(
            "ciw.atmosphere_workflow.validate_result_dependencies", wraps=atmosphere.validate_result_dependencies))
        restored = Session.from_workspace(path, tmp_path / "restored")
    system_dependencies.assert_called_once()
    atmosphere_dependencies.assert_called_once()
    assert restored.run == session.run
    assert restored.results == session.results
    assert restored.executions == session.executions
    assert restored.workbench.retained_sources() == session.workbench.retained_sources()


@pytest.mark.parametrize("family", ["system", "atmosphere"])
def test_combined_registry_refuses_foreign_source_before_entering_either_provider(tmp_path, family):
    session = _session(tmp_path, family)
    operation, parameters = ((atmosphere.COMPILE, {}) if family == "system"
                             else (system.COMPILE, {"specification": demo_spec()}))
    with patch("ciw.system_spec.compile_spec", side_effect=AssertionError("system compiler entered")), \
            patch("ciw.atmosphere_compiler.compile_atmosphere", side_effect=AssertionError("atmosphere compiler entered")):
        reply = session.handle({"protocol_version": 1, "request_id": "cross-family",
                                "type": "operation.execute", "payload": {
                                    "operation_id": operation, "parameters": parameters}})
    assert reply["type"] == "response"
    assert reply["payload"]["status"] == "refused"
    assert reply["payload"]["result"] is None
    assert not session.results and len(session.executions) == 1
    path = session.save_workspace(tmp_path / "refused.json")
    restored = Session.from_workspace(path, tmp_path / "restored")
    assert restored.executions == session.executions and not restored.results
