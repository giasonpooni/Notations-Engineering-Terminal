"""Independent retained-balance checks, with no native provider execution."""
from copy import deepcopy
from fractions import Fraction
import builtins
import math
from pathlib import Path

import numpy as np
import pytest

from ciw import leakage_contract as contract
from ciw import leakage_native as native
from ciw import leakage_verification as audit
from ciw import leakage_workflow as workflow
from ciw.operations.runner import digest, seal


def _request(basis="volume", intervals=2, channels=2):
    request = contract.example_request(basis)
    request["edges_s"] = request["edges_s"][:intervals + 1]
    request["clock"]["end_s"] = request["edges_s"][-1]
    request["inventory"]["readings"] = request["inventory"]["readings"][:intervals + 1]
    request["channels"] = request["channels"][:channels]
    for channel in request["channels"]:
        channel["readings"] = channel["readings"][:intervals]
    order = contract.raw_order(request)
    request["covariance"]["raw_order"] = order
    request["covariance"]["matrix"] = (np.eye(len(order)) * 2 ** -20).tolist()
    return contract.validate_request(request)


def _runtime():
    return {"schema": "ciw.subprocess-runtime.v1", "adapter_version": "ciw-pinned-subprocess-v1",
            "repository_root": "/retained/unavailable-flowstate", "revision": audit._REVISION,
            "source_tree": audit._SOURCE_TREE, "module": "set_lcm.bridge.ciw", "source_root": "src",
            "python_executable": "/retained/unavailable-python", "python_sha256": "b" * 64,
            "python_version": "3.12.10", "dependencies": {"numpy": "2.2.6", "scipy": None}}


def _calculation(request):
    """Independent fixture using a difference block and directed transfer block."""
    n = len(request["edges_s"]) - 1
    rows = contract.raw_readings(request)
    values = np.asarray([row["value"] for row in rows], dtype=float)
    covariance = np.asarray(request["covariance"]["matrix"], dtype=float)
    storage = np.eye(n, n + 1, k=1) - np.eye(n, n + 1)
    direction = [-1 if channel["direction"] == "in" else 1 for channel in request["channels"]]
    operator = np.hstack((storage, np.kron(np.eye(n), [direction])))
    groups = [contract.evidence_group(row) for row in rows]
    result = {"schema": contract.RESULT_SCHEMA, "request_ref": digest(request), "basis": request["basis"],
              "unit": request["unit"], "covariance_unit": contract.COVARIANCE_UNITS[request["basis"]],
              "kernel": audit._kernel(request["basis"]), "runtime": _runtime(),
              "raw": {"order": contract.raw_order(request), "values": [row["value"] for row in rows],
                      "covariance": deepcopy(request["covariance"]["matrix"]),
                      "evidence_refs": [row["evidence_refs"] for row in rows],
                      "calibration_refs": [row["calibration_ref"] for row in rows],
                      "clock_refs": [row["clock_ref"] for row in rows],
                      "covariance_evidence_refs": request["covariance"]["evidence_refs"]},
              "authority": deepcopy(contract.AUTHORITY), "limitations": deepcopy(contract.LIMITATIONS)}
    for label, H in (("interval", operator), ("cumulative", np.tril(np.ones((n, n))) @ operator)):
        C = (H @ covariance) @ H.T
        result[label] = {"operator": H.tolist(), "residual": (H @ values).tolist(),
                         "covariance": (0.5 * C + 0.5 * C.T).tolist(),
                         "support": [{"start_s": request["edges_s"][i] if label == "interval" else request["edges_s"][0],
                                      "end_s": request["edges_s"][i + 1]} for i in range(n)],
                         "evidence_refs": [list(dict.fromkeys(ref for j, coefficient in enumerate(row)
                                                             if coefficient for ref in groups[j])) for row in H]}
    return result


def _checks(report):
    return {row["name"]: row["passed"] for row in report["checks"]}


@pytest.mark.parametrize("root,executable", [
    ("/retained/unavailable-flowstate", "/retained/unavailable-python"),
    (r"C:\retained\unavailable-flowstate", r"C:\retained\python.exe"),
    (r"\\archive\retained\flowstate", r"\\archive\retained\python.exe"),
])
def test_retained_runtime_paths_from_either_platform_are_metadata_only(monkeypatch, root, executable):
    request = _request()
    calculation = _calculation(request)
    calculation["runtime"].update(repository_root=root, python_executable=executable)
    original = deepcopy(calculation)
    retained_runtime = {"provider": "ciw.leakage.assessment", "version": "1", "code_sha256": "c" * 64,
                        "source_normalization": "utf8_lf",
                        "scope": "offline_declared_boundary_balance_no_cause_or_actuation",
                        "environment": {"python": "3.12.10", "floating_point": "binary64"},
                        "native": calculation["runtime"]}

    def forbidden(*args, **kwargs):
        raise AssertionError("Retained runtime metadata consulted a filesystem path")

    with monkeypatch.context() as offline:
        for method in ("resolve", "exists", "is_dir", "is_file"):
            offline.setattr(Path, method, forbidden)
        native.check_runtime(calculation["runtime"])
        workflow.validate_runtime(workflow.ASSESS, retained_runtime)
        audit.validate_calculation(request, calculation)
        assert audit.verify(request, calculation)["status"] == "PASS"
    assert calculation == original


@pytest.mark.parametrize("field", ["repository_root", "python_executable"])
@pytest.mark.parametrize("path", ["relative/path", r"C:drive-relative", r"\rooted-without-drive"])
def test_retained_runtime_paths_still_require_an_absolute_location(field, path):
    runtime = _runtime()
    runtime[field] = path
    with pytest.raises(ValueError, match="paths must be absolute"):
        native.check_runtime(runtime)
    with pytest.raises(ValueError, match="paths must be absolute"):
        audit._runtime(runtime)


@pytest.mark.parametrize("basis", ["volume", "mass"])
def test_finite_audit_has_exact_references_and_no_occurrence_authority(basis):
    request = _request(basis)
    calculation = _calculation(request)
    before = deepcopy(calculation)
    audit.validate_calculation(request, calculation)
    report = audit.verify(request, calculation)
    assert report["status"] == "PASS"
    assert all(_checks(report).values())
    assert report["request_ref"] == digest(request)
    assert report["calculation_ref"] == digest(calculation)
    assert report["decision_policy_ref"] == digest(request["decision_policy"])
    assert report["authority"]["causal_leak_identification"] == "not_established"
    assert report["scope"] == audit.SCOPE
    assert "verification_id" not in report
    assert calculation == before
    audit.validate_report(report)


def test_operator_signs_support_and_telescoping_evidence():
    request = _request()
    calculation = _calculation(request)
    assert calculation["interval"]["operator"] == [[-1, 1, 0, -1, 1, 0, 0], [0, -1, 1, 0, 0, -1, 1]]
    assert calculation["cumulative"]["operator"][-1] == [-1, 0, 1, -1, 1, -1, 1]
    middle = request["inventory"]["readings"][1]
    assert middle["evidence_refs"][0] in calculation["interval"]["evidence_refs"][0]
    assert middle["evidence_refs"][0] not in calculation["cumulative"]["evidence_refs"][-1]
    assert middle["calibration_ref"] not in calculation["cumulative"]["evidence_refs"][-1]
    assert audit.verify(request, calculation)["status"] == "PASS"


def test_temporal_covariance_and_cumulative_endpoint_cancellation_are_preserved():
    request = _request(intervals=2, channels=1)
    scale = 2 ** -100
    request["covariance"]["matrix"] = (np.eye(5) * scale).tolist()
    calculation = _calculation(request)
    assert calculation["interval"]["covariance"] == [[3 * scale, -scale], [-scale, 3 * scale]]
    assert calculation["cumulative"]["covariance"] == [[3 * scale, 2 * scale], [2 * scale, 4 * scale]]
    assert calculation["cumulative"]["covariance"][-1][-1] != sum(
        calculation["interval"]["covariance"][i][i] for i in range(2))
    assert audit.verify(request, calculation)["status"] == "PASS"


@pytest.mark.parametrize("basis", ["volume", "mass"])
def test_shared_reference_cross_interval_covariance_is_used(basis):
    request = _request(basis, intervals=2, channels=1)
    P = np.eye(5)
    P[3, 4] = P[4, 3] = 0.5
    request["covariance"]["matrix"] = P.tolist()
    shared = digest({"shared_calibration": "feed_total"})
    for row in request["channels"][0]["readings"]:
        row["calibration_ref"] = shared
    calculation = _calculation(request)
    assert calculation["interval"]["covariance"] == [[3, -0.5], [-0.5, 3]]
    assert calculation["cumulative"]["covariance"][-1][-1] == 5
    assert calculation["cumulative"]["evidence_refs"][-1].count(shared) == 1
    assert audit.verify(request, calculation)["status"] == "PASS"


@pytest.mark.parametrize("label", ["interval", "cumulative"])
def test_small_cross_covariance_cannot_be_discarded_with_an_absolute_floor(label):
    request = _request(intervals=2, channels=1)
    request["covariance"]["matrix"] = (np.eye(5) * 2 ** -100).tolist()
    calculation = _calculation(request)
    calculation[label]["covariance"][0][1] = calculation[label]["covariance"][1][0] = 0.0
    audit.validate_calculation(request, calculation)  # Structural reader does not re-run the oracle.
    report = audit.verify(request, calculation)
    assert report["status"] == "FAIL"
    assert _checks(report)["retained_contract_and_source_binding"]
    assert not _checks(report)[label + "_covariance"]


@pytest.mark.parametrize("label", ["interval", "cumulative"])
def test_tiny_covariance_inflation_uses_source_scale_not_claimed_result(label):
    request = _request(intervals=2, channels=1)
    request["covariance"]["matrix"] = (np.eye(5) * 2 ** -100).tolist()
    calculation = _calculation(request)
    calculation[label]["covariance"][0][0] += 2 ** -80
    assert not _checks(audit.verify(request, calculation))[label + "_covariance"]


def test_exact_zero_source_covariance_has_no_blanket_subnormal_allowance():
    request = _request(intervals=1, channels=1)
    request["covariance"]["matrix"] = np.zeros((3, 3)).tolist()
    calculation = _calculation(request)
    assert audit.verify(request, calculation)["status"] == "PASS"
    calculation["interval"]["covariance"][0][0] = math.ulp(0.0)
    assert not _checks(audit.verify(request, calculation))["interval_covariance"]


def test_lawful_severe_residual_cancellation_passes_source_scaled_envelope():
    request = _request(intervals=1, channels=2)
    # H=[-1,+1,-1,+1]; exact binary64 sum is -2^-40. A BLAS
    # accumulation may lose the first term while adding the large opposing pair.
    request["inventory"]["readings"][0]["value"] = 2 ** -40
    request["inventory"]["readings"][1]["value"] = 2 ** 40
    request["channels"][0]["readings"][0]["value"] = 2 ** 40
    request["channels"][1]["readings"][0]["value"] = 0.0
    calculation = _calculation(request)
    for label in ("interval", "cumulative"):
        calculation[label]["residual"] = [0.0]
    assert sum(
        Fraction.from_float(float(coefficient)) * Fraction.from_float(float(value))
        for coefficient, value in zip(
            calculation["interval"]["operator"][0], calculation["raw"]["values"])
    ) == -Fraction(1, 2 ** 40)
    assert audit.verify(request, calculation)["status"] == "PASS"
    calculation["interval"]["residual"][0] = 1.0
    assert not _checks(audit.verify(request, calculation))["interval_residuals"]


def test_small_nonzero_residual_tamper_is_detected_at_small_source_scale():
    request = _request(intervals=1, channels=1)
    for row in contract.raw_readings(request):
        row["value"] = 2 ** -100
    calculation = _calculation(request)
    calculation["interval"]["residual"][0] += 2 ** -80
    assert not _checks(audit.verify(request, calculation))["interval_residuals"]


def test_structural_reader_does_not_recompute_and_audit_does_not_load_provider(monkeypatch):
    request = _request()
    calculation = _calculation(request)
    original_import = builtins.__import__
    def blocked_import(name, *args, **kwargs):
        if "leakage_native" in name or name == "set_lcm" or name.startswith("set_lcm."):
            raise AssertionError("Native provider imported during retained audit")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", blocked_import)
    with monkeypatch.context() as structural:
        structural.setattr(audit, "_oracle", lambda *a, **k: (_ for _ in ()).throw(AssertionError("oracle recomputed")))
        audit.validate_calculation(request, calculation)
    assert audit.verify(request, calculation)["status"] == "PASS"


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(request_ref=digest({"another": "request"})),
    lambda d: d.update(unit="kg"),
    lambda d: d["raw"]["values"].__setitem__(0, 99.0),
    lambda d: d["raw"]["order"].reverse(),
    lambda d: d["raw"]["covariance"][0].__setitem__(0, 0.5),
    lambda d: d["raw"]["covariance_evidence_refs"].__setitem__(0, digest({"altered": "covariance reference"})),
    lambda d: d["raw"]["evidence_refs"][0].__setitem__(0, digest({"altered": "reading reference"})),
    lambda d: d["raw"]["calibration_refs"].__setitem__(0, digest({"altered": "calibration"})),
    lambda d: d["raw"]["clock_refs"].__setitem__(0, digest({"altered": "clock"})),
    lambda d: d["interval"]["operator"][0].__setitem__(0, 1),
    lambda d: d["cumulative"]["operator"][1].__setitem__(1, 1),
    lambda d: d["interval"]["support"][0].update(start_s=0.1),
    lambda d: d["cumulative"]["support"][1].update(start_s=2.5),
    lambda d: d["interval"]["support"][0].update(start_s=False),
    lambda d: d["interval"]["evidence_refs"][0].pop(),
    lambda d: d["cumulative"]["evidence_refs"][1].append(digest({"cancelled": "inventory"})),
    lambda d: d["kernel"].update(provider="local-emulator"),
    lambda d: d["kernel"].update(coefficient_uncertainty="estimated"),
    lambda d: d["kernel"].update(residual_sign="net_inflow_minus_storage_change"),
    lambda d: d["runtime"].update(revision="c" * 40),
    lambda d: d["runtime"].update(source_tree="c" * 40),
    lambda d: d["runtime"].update(module="set_lcm.local.emulator"),
    lambda d: d["runtime"]["dependencies"].update(unknown="1.0"),
    lambda d: d["runtime"]["dependencies"].update(numpy="1.26.4"),
    lambda d: d["runtime"].update(python_executable="relative/python"),
    lambda d: d["runtime"].update(python_version="3.11.9"),
    lambda d: d["authority"].update(physical_validation="established"),
    lambda d: d["limitations"].pop(),
])
def test_raw_support_kernel_and_runtime_tampering_fails_before_numerical_audit(mutate):
    request = _request()
    calculation = _calculation(request)
    mutate(calculation)
    with pytest.raises(ValueError):
        audit.validate_calculation(request, calculation)
    report = audit.verify(request, calculation)
    assert report["status"] == "FAIL"
    assert not _checks(report)["retained_contract_and_source_binding"]


@pytest.mark.parametrize("field,value", [("residual", [True, 0.0]), ("residual", [1.0]),
                                         ("covariance", [[1.0, 0.1], [0.2, 1.0]]),
                                         ("covariance", [[-1.0, 0.0], [0.0, 1.0]])])
def test_invalid_result_shape_boolean_as_number_asymmetry_and_negative_variance(field, value):
    request = _request()
    calculation = _calculation(request)
    calculation["interval"][field] = value
    with pytest.raises(ValueError):
        audit.validate_calculation(request, calculation)


@pytest.mark.parametrize("basis", ["volume", "mass"])
def test_native_source_symmetrization_cannot_silently_drop_odd_subnormal(basis):
    request = _request(basis, intervals=1, channels=1)
    request["covariance"]["matrix"] = (np.eye(3) * math.ulp(0.0)).tolist()
    calculation = _calculation(request)
    with pytest.raises(ValueError, match="Source covariance would change"):
        audit.validate_calculation(request, calculation)


def test_mass_profile_cannot_accept_singular_raw_covariance():
    request = _request("mass", intervals=1, channels=1)
    request["covariance"]["matrix"] = np.ones((3, 3)).tolist()
    calculation = _calculation(request)
    with pytest.raises(ValueError, match="strictly positive definite"):
        audit.validate_calculation(request, calculation)


def test_report_seal_fixed_check_coverage_and_status_are_validated():
    request = _request()
    report = audit.verify(request, _calculation(request))
    report["status"] = "FAIL"
    with pytest.raises(ValueError):
        audit.validate_report(report)
    report = seal({key: value for key, value in report.items() if key != "record_digest"})
    with pytest.raises(ValueError, match="status differs"):
        audit.validate_report(report)
    report["checks"].pop()
    report = seal({key: value for key, value in report.items() if key != "record_digest"})
    with pytest.raises(ValueError, match="check coverage differs"):
        audit.validate_report(report)


def test_expanded_balanced_mass_and_volume_windows_remain_bounded():
    for basis in ("volume", "mass"):
        request = _request(basis, intervals=4, channels=2)
        for row in request["inventory"]["readings"]:
            row["value"] = 10.0
        for channel in request["channels"]:
            for row in channel["readings"]:
                row["value"] = 1.0
        calculation = _calculation(request)
        assert calculation["interval"]["residual"] == [0.0] * 4
        assert calculation["cumulative"]["residual"] == [0.0] * 4
        assert audit.verify(request, calculation)["status"] == "PASS"


def test_upward_arithmetic_guard_handles_zero_subnormal_and_rounding_direction():
    assert audit._upward_float(Fraction()) == 0.0
    assert audit._upward_float(Fraction(1, 2 ** 1075)) == math.ulp(0.0)
    for exact in (Fraction(1, 10), Fraction(1, 3), Fraction(7, 2 ** 1075)):
        bound = audit._upward_float(exact)
        assert math.isfinite(bound) and Fraction.from_float(bound) >= exact
        assert Fraction.from_float(math.nextafter(bound, -math.inf)) < exact
    with pytest.raises(ValueError, match="finite bounded number"):
        audit._upward_float(Fraction(10 ** 151))


@pytest.mark.parametrize("basis", ["volume", "mass"])
def test_public_rounding_guards_enclose_exact_oracle_bounds_without_provider(basis):
    request = _request(basis, intervals=2, channels=1)
    calculation = _calculation(request)
    guards = audit.rounding_guards(request, calculation)
    assert set(guards) == {"interval", "cumulative"}
    projection = audit._projection(request)
    for kind in guards:
        _, residual_bounds, _, covariance_bounds = audit._oracle(projection[kind], projection["values"],
                                                                 request["covariance"]["matrix"])
        assert len(guards[kind]) == 2
        for i, row in enumerate(guards[kind]):
            assert set(row) == {"residual_error_bound", "covariance_diagonal_error_bound"}
            assert Fraction.from_float(row["residual_error_bound"]) >= residual_bounds[i]
            assert Fraction.from_float(row["covariance_diagonal_error_bound"]) >= covariance_bounds[i][i]


def test_lawful_roundoff_near_threshold_has_a_separate_nonprobabilistic_guard():
    request = _request(intervals=1, channels=1)
    for row in request["inventory"]["readings"]:
        row["value"] = 10.0
    request["channels"][0]["readings"][0]["value"] = 0.001
    request["decision_policy"]["loss_threshold"] = 0.001
    request["covariance"]["matrix"] = np.zeros((3, 3)).tolist()
    calculation = _calculation(request)
    # This one-ULP loss increment is allowed arithmetic, not evidence that
    # the exact declared loss exceeds the caller's threshold.
    calculation["interval"]["residual"][0] = math.nextafter(-0.001, -math.inf)
    assert audit.verify(request, calculation)["status"] == "PASS"
    nominal_loss = -calculation["interval"]["residual"][0]
    assert nominal_loss > request["decision_policy"]["loss_threshold"]
    guard = audit.rounding_guards(request, calculation)["interval"][0]
    assert guard["residual_error_bound"] > nominal_loss - request["decision_policy"]["loss_threshold"]
    assert nominal_loss - guard["residual_error_bound"] < request["decision_policy"]["loss_threshold"]
    assert guard["covariance_diagonal_error_bound"] == 0.0


def test_raw_retention_distinguishes_signed_zero_and_integer_representation():
    request = _request(intervals=1, channels=1)
    request["inventory"]["readings"][0]["value"] = 10
    calculation = _calculation(request)
    audit.validate_calculation(request, calculation)
    calculation["raw"]["values"][0] = 10.0
    with pytest.raises(ValueError, match="Retained raw values"):
        audit.validate_calculation(request, calculation)
    calculation = _calculation(request)
    calculation["raw"]["covariance"][0][1] = -0.0
    with pytest.raises(ValueError, match="Retained raw values"):
        audit.validate_calculation(request, calculation)


@pytest.mark.parametrize("basis,n,m", [("volume", 16, 2), ("mass", 9, 6)])
def test_oracle_handles_maximum_intervals_and_maximum_raw_axes(basis, n, m):
    request = contract.example_request(basis)
    edges = [float(i) for i in range(n + 1)]
    request["edges_s"] = edges
    request["clock"].update(start_s=0.0, end_s=float(n), clock_ref=digest({"max_window_edges": edges}))
    clock_ref = request["clock"]["clock_ref"]
    def reading(label, **support):
        return {**support, "value": 10.0 if label.startswith("inventory") else 1.0,
                "evidence_refs": [digest({"max_window_reading": label})],
                "calibration_ref": digest({"max_window_calibration": label}), "clock_ref": clock_ref}
    request["inventory"]["readings"] = [reading(f"inventory:{i}", time_s=edge) for i, edge in enumerate(edges)]
    request["channels"] = [{"channel_id": f"channel{j}", "direction": "in" if j % 2 == 0 else "out",
                            "purpose": "other", "support": "integrated_total", "unit": request["unit"],
                            "readings": [reading(f"channel{j}:{i}", start_s=a, end_s=b)
                                         for i, (a, b) in enumerate(zip(edges, edges[1:]))]} for j in range(m)]
    order = contract.raw_order(request)
    request["covariance"].update(raw_order=order, matrix=np.eye(len(order)).tolist())
    assert n == contract.MAX_INTERVALS or len(order) == contract.MAX_RAW_SIZE
    calculation = _calculation(contract.validate_request(request))
    assert calculation["cumulative"]["covariance"][-1][-1] == 2 + n * m
    assert audit.verify(request, calculation)["status"] == "PASS"
    assert len(audit.rounding_guards(request, calculation)["interval"]) == n
