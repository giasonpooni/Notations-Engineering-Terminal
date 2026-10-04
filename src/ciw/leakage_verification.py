"""Offline binding checks and an independent integrated-amount balance audit.

No native provider is imported or executed here. Integer conservation operators
are derived from the retained support labels. The numerical oracle uses exact
rationals of the supplied binary64 inputs, including every cross-covariance.
Its comparison allowance comes from source magnitudes and arithmetic roundoff,
never from a claimed result or a generic absolute tolerance.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import math
from pathlib import PurePosixPath, PureWindowsPath
import re

from .control_contracts import content_ref, detached, keys, number, text
from .leakage_contract import AUTHORITY, COVARIANCE_UNITS, LIMITATIONS as CALCULATION_LIMITATIONS, RESULT_SCHEMA, validate_request
from .operations.runner import check_seal, digest, seal

REPORT_SCHEMA = "ciw.leakage-numerical-verification.v1"
SCOPE = "finite_binary64_linear_balance_consistency_not_physical_validation"
CHECK_NAMES = ("retained_contract_and_source_binding", "interval_residuals", "interval_covariance",
               "cumulative_residuals", "cumulative_covariance")
LIMITATIONS = [
    "Exact rational audit of the binary64 input declarations and fixed integrated-amount conservation operators only.",
    "Every interval and cumulative covariance entry is checked; no diagonal substitution, covariance repair or independence inference.",
    "Source-scaled binary64 rounding allowances account for cancellation and final covariance symmetrization, not model discrepancy.",
    "Declared uncertainty bands and thresholds do not establish calibrated probabilities, physical traceability or a causal leak diagnosis.",
    "Native runtime metadata is checked as a fixed declaration; execution occurrence binding remains the enclosing workflow's responsibility.",
    "No provider replay, state admission, physical validation or hardware action is performed.",
]
_REVISION = "09a756dd9cdd3a9bb6cb14b5cd498f6259937ac2"
_SOURCE_TREE = "c79c44f90d5ff3920e58a53e9c277104e7e07bd3"
_UNIT_ROUNDOFF = Fraction(1, 2 ** 53)
_HALF_SUBNORMAL = Fraction(1, 2 ** 1075)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _projection(request):
    """Build raw bindings and telescoping operators independently of the adapter."""
    edges, channels = request["edges_s"], request["channels"]
    intervals, count = len(edges) - 1, len(channels)
    readings = list(request["inventory"]["readings"])
    order = [f"inventory:{i}" for i in range(intervals + 1)]
    for i in range(intervals):
        for channel in channels:
            order.append(f"transfer:{channel['channel_id']}:{i}")
            readings.append(channel["readings"][i])
    size = len(readings)
    interval, cumulative = [], []
    for i in range(intervals):
        row = [0] * size
        row[i], row[i + 1] = -1, 1
        for j, channel in enumerate(channels):
            row[intervals + 1 + i * count + j] = -1 if channel["direction"] == "in" else 1
        interval.append(row)
        # Explicit endpoint cancellation, rather than summing producer rows.
        whole = [0] * size
        whole[0], whole[i + 1] = -1, 1
        for k in range(i + 1):
            for j, channel in enumerate(channels):
                whole[intervals + 1 + k * count + j] = -1 if channel["direction"] == "in" else 1
        cumulative.append(whole)
    groups = [list(dict.fromkeys(row["evidence_refs"] + [row["calibration_ref"], row["clock_ref"]]))
              for row in readings]
    return {"order": order, "readings": readings, "values": [row["value"] for row in readings],
            "groups": groups, "interval": interval, "cumulative": cumulative}


def _kernel(basis):
    api = ("set_lcm.measurement.balance_residuals" if basis == "volume" else
           "set_lcm.lcm.residual + set_lcm.fdi.residual_covariance")
    return {"provider": "fsrt", "revision": _REVISION, "module": "set_lcm.bridge.ciw",
            "profile": "volume_instant_integrated_totals" if basis == "volume" else "mass_exact_linear_conservation_spd",
            "interval_api": api, "cumulative_api": api,
            "residual_sign": "storage_change_minus_net_inflow", "coefficient_uncertainty": "declared_exact",
            "covariance_policy": "full_declared_psd" if basis == "volume" else "full_declared_spd",
            "maximum_condition_number": None if basis == "volume" else 1e12}


def _runtime(value):
    """Check retained metadata only; never consult native files or an interpreter."""
    keys(value, {"schema", "adapter_version", "repository_root", "revision", "source_tree", "module",
                 "source_root", "python_executable", "python_sha256", "python_version", "dependencies"})
    _require(value["schema"] == "ciw.subprocess-runtime.v1" and
             value["adapter_version"] == "ciw-pinned-subprocess-v1" and
             value["revision"] == _REVISION and value["module"] == "set_lcm.bridge.ciw" and
             value["source_root"] == "src" and value["source_tree"] == _SOURCE_TREE,
             "Leakage runtime differs from the approved native profile and source tree")
    _require(type(value["python_sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", value["python_sha256"]) is not None,
             "Malformed retained native interpreter digest")
    for field in ("repository_root", "python_executable"):
        text(value[field])
        _require(PurePosixPath(value[field]).is_absolute() or PureWindowsPath(value[field]).is_absolute(),
                 "Native operator binding paths must be absolute")
    version = value["python_version"]
    _require(type(version) is str and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is not None,
             "Malformed retained Python version")
    _require(tuple(map(int, version.split("."))) >= (3, 12, 0), "Leakage requires the declared Python >=3.12 profile")
    dependencies = value["dependencies"]
    _require(type(dependencies) is dict and set(dependencies) == {"numpy", "scipy"}, "Require the fixed retained native dependency declarations")
    for name, dependency in dependencies.items():
        if name == "scipy" and dependency is None:
            continue
        _require(type(dependency) is str and 1 <= len(dependency) <= 64 and
                 re.fullmatch(r"[0-9]+\.[0-9]+[A-Za-z0-9.+-]*", dependency) is not None,
                 "Malformed retained numerical dependency version")
    numpy_version = dependencies.get("numpy")
    _require(type(numpy_version) is str and re.match(r"([0-9]+)\.", numpy_version) is not None and
             int(numpy_version.split(".")[0]) >= 2, "Leakage requires the declared NumPy >=2 profile")


def _vector(value, size):
    _require(type(value) is list and len(value) == size, "Retained balance vector dimensions differ")
    for entry in value:
        number(entry)


def _matrix(value, rows, columns, *, covariance=False):
    _require(type(value) is list and len(value) == rows, "Retained balance matrix dimensions differ")
    for row in value:
        _vector(row, columns)
    if covariance:
        _require(rows == columns and all(value[i][i] >= 0 for i in range(rows)), "Retained variances must be nonnegative")
        _require(all(value[i][j] == value[j][i] for i in range(rows) for j in range(rows)),
                 "Retained full covariance must be exactly symmetric; no repair")


def _refs(value):
    _require(type(value) is list, "Require retained evidence references")
    for item in value:
        content_ref(item)
    _require(len(set(value)) == len(value), "Duplicate retained evidence reference")


def _strict_source_covariance(matrix):
    """Exact LDL pivots check the mass profile's declared raw SPD requirement."""
    rows = [[_fraction(entry) for entry in row] for row in matrix]
    for k in range(len(rows)):
        pivot = rows[k][k]
        _require(pivot > 0, "The mass native profile requires strictly positive definite raw covariance")
        for i in range(k + 1, len(rows)):
            for j in range(i, len(rows)):
                value = rows[i][j] - rows[i][k] * rows[k][j] / pivot
                rows[i][j] = rows[j][i] = value


def validate_calculation(request: dict, data: dict) -> None:
    """Validate all declaration bindings without recalculating residuals/covariance."""
    request = validate_request(request)
    detached(data)
    keys(data, {"schema", "request_ref", "basis", "unit", "covariance_unit", "kernel", "runtime",
                "raw", "interval", "cumulative", "authority", "limitations"})
    _require(data["schema"] == RESULT_SCHEMA and data["request_ref"] == digest(request),
             "Retained leakage calculation has a different request binding")
    _require(data["basis"] == request["basis"] and data["unit"] == request["unit"] and
             data["covariance_unit"] == COVARIANCE_UNITS[request["basis"]], "Retained balance basis or units differ")
    _require(data["authority"] == AUTHORITY and data["limitations"] == CALCULATION_LIMITATIONS,
             "Retained leakage authority or limitations differ")
    _require(data["kernel"] == _kernel(request["basis"]), "Retained native conservation kernel/profile differs")
    _runtime(data["runtime"])
    source = _projection(request)
    size, intervals = len(source["order"]), len(request["edges_s"]) - 1
    raw = data["raw"]
    keys(raw, {"order", "values", "covariance", "evidence_refs", "calibration_refs", "clock_refs", "covariance_evidence_refs"})
    _vector(raw["values"], size)
    _matrix(raw["covariance"], size, size, covariance=True)
    expected_raw = {"order": source["order"], "values": source["values"],
                    "covariance": request["covariance"]["matrix"],
                    "evidence_refs": [row["evidence_refs"] for row in source["readings"]],
                    "calibration_refs": [row["calibration_ref"] for row in source["readings"]],
                    "clock_refs": [row["clock_ref"] for row in source["readings"]],
                    "covariance_evidence_refs": request["covariance"]["evidence_refs"]}
    _require(digest(raw) == digest(expected_raw), "Retained raw values, full covariance, order or evidence differ from request")
    if request["basis"] == "mass":
        _strict_source_covariance(raw["covariance"])
    # The approved native input path checks two half-scaled copies. Odd
    # subnormal entries would change that input; the native adapter refuses them.
    _require(all(0.5 * float(x) + 0.5 * float(x) == float(x) for row in raw["covariance"] for x in row),
             "Source covariance would change in the declared native input profile")
    for kind in ("interval", "cumulative"):
        series, operator = data[kind], source[kind]
        keys(series, {"operator", "residual", "covariance", "support", "evidence_refs"})
        _matrix(series["operator"], intervals, size)
        _require(series["operator"] == operator, "Retained conservation operator differs from declared transfers")
        _vector(series["residual"], intervals)
        _matrix(series["covariance"], intervals, intervals, covariance=True)
        expected_support = [{"start_s": request["edges_s"][i] if kind == "interval" else request["edges_s"][0],
                             "end_s": request["edges_s"][i + 1]} for i in range(intervals)]
        _require(type(series["support"]) is list and len(series["support"]) == intervals,
                 "Retained support coverage differs")
        for support in series["support"]:
            keys(support, {"start_s", "end_s"})
            number(support["start_s"])
            number(support["end_s"])
        _require(series["support"] == expected_support, "Retained interval or cumulative supports differ")
        evidence = [list(dict.fromkeys(ref for j, coefficient in enumerate(row) if coefficient != 0
                                      for ref in source["groups"][j])) for row in operator]
        _require(series["evidence_refs"] == evidence, "Retained contributing/cancelled measurement evidence differs")
        for group in series["evidence_refs"]:
            _refs(group)


def _fraction(value):
    return Fraction.from_float(float(value))


def _gamma(additions):
    return additions * _UNIT_ROUNDOFF / (1 - additions * _UNIT_ROUNDOFF)


def _oracle(operator, values, covariance):
    """Exact signed sums plus source-scaled bounds for two binary64 matmuls.

    The operator is restricted to 0,+1,-1. Sign changes/products are exact, so
    only q-1 additions per nonzero dot can round. Subnormal addition is exact;
    half-scaling in the native final symmetrization has its own underflow bound.
    """
    y = [_fraction(x) for x in values]
    matrix = [[_fraction(x) for x in row] for row in covariance]
    active = [[(j, coefficient) for j, coefficient in enumerate(row) if coefficient]
              for row in operator]
    residuals, residual_bounds, weighted, weighted_bounds = [], [], [], []
    for terms in active:
        gamma = _gamma(len(terms) - 1)
        residuals.append(sum((coefficient * y[j] for j, coefficient in terms), Fraction()))
        residual_bounds.append(gamma * sum((abs(y[j]) for j, _ in terms), Fraction()))
        weighted.append([sum((coefficient * matrix[j][k] for j, coefficient in terms), Fraction())
                         for k in range(len(values))])
        weighted_bounds.append([gamma * sum((abs(matrix[j][k]) for j, _ in terms), Fraction())
                                for k in range(len(values))])
    exact, directed_error, source_scales = [], [], []
    for i, terms_i in enumerate(active):
        exact_row, error_row, scale_row = [], [], []
        for terms_j in active:
            exact_row.append(sum((coefficient * weighted[i][k] for k, coefficient in terms_j), Fraction()))
            propagated = sum((weighted_bounds[i][k] for k, _ in terms_j), Fraction())
            summands = sum((abs(weighted[i][k]) + weighted_bounds[i][k] for k, _ in terms_j), Fraction())
            error_row.append(propagated + _gamma(len(terms_j) - 1) * summands)
            scale_row.append(sum((abs(matrix[j][k]) for j, _ in terms_i for k, _ in terms_j), Fraction()))
        exact.append(exact_row)
        directed_error.append(error_row)
        source_scales.append(scale_row)
    bounds = []
    for i, row in enumerate(exact):
        bounds.append([Fraction() if source_scales[i][j] == 0 else
                       (1 + _UNIT_ROUNDOFF) * (directed_error[i][j] + directed_error[j][i]) / 2 +
                       _UNIT_ROUNDOFF * abs(entry) + (3 + 2 * _UNIT_ROUNDOFF) * _HALF_SUBNORMAL
                       for j, entry in enumerate(row)])
    return residuals, residual_bounds, exact, bounds


def _compare(values, expected, bounds, *, matrix=False):
    cells = ((i, j, values[i][j], expected[i][j], bounds[i][j]) for i in range(len(expected))
             for j in range(len(expected))) if matrix else (
                 (i, None, values[i], expected[i], bounds[i]) for i in range(len(expected)))
    for i, j, value, exact, allowance in cells:
        difference = abs(_fraction(value) - exact)
        if difference > allowance:
            location = f"[{i},{j}]" if matrix else f"[{i}]"
            return False, (f"Entry {location} exceeds source-scaled binary64 rounding: "
                           f"error={float(difference):.17g}, allowance={float(allowance):.17g}")
    return True, "Every retained entry agrees within source-scaled binary64 rounding"


def _upward_float(value):
    """Enclose an exact nonnegative bound; never round positive underflow to zero."""
    _require(type(value) is Fraction and value >= 0, "Require an exact nonnegative arithmetic bound")
    if value == 0:
        return 0.0
    candidate = float(value)
    _require(math.isfinite(candidate), "Arithmetic guard exceeds the finite numerical profile")
    if Fraction.from_float(candidate) < value:
        candidate = math.nextafter(candidate, math.inf)
    number(candidate)
    _require(Fraction.from_float(candidate) >= value, "Cannot retain an enclosing finite arithmetic guard")
    return candidate


def rounding_guards(request: dict, data: dict) -> dict:
    """Project source-scaled roundoff bounds for threshold decisions.

    These are arithmetic envelopes, separate from the caller's measurement
    uncertainty. They have no probabilistic interpretation. The independent
    verification still checks all covariance entries, including off-diagonals.
    """
    request = validate_request(request)
    validate_calculation(request, data)
    source = _projection(request)
    result = {}
    for kind in ("interval", "cumulative"):
        _, residual_bounds, _, covariance_bounds = _oracle(source[kind], source["values"], request["covariance"]["matrix"])
        result[kind] = [{"residual_error_bound": _upward_float(residual_bounds[i]),
                         "covariance_diagonal_error_bound": _upward_float(covariance_bounds[i][i])}
                        for i in range(len(residual_bounds))]
    return result


def verify(request: dict, data: dict) -> dict:
    """Audit retained arithmetic; the workflow supplies occurrence identities."""
    request = validate_request(request)
    detached(data)
    checks = [{"name": name, "passed": False, "detail": "Not evaluated because the retained binding check failed"}
              for name in CHECK_NAMES]
    try:
        validate_calculation(request, data)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        checks[0]["detail"] = str(exc)[:512] or "Invalid retained leakage calculation"
    else:
        checks[0].update(passed=True, detail="Exact request, raw covariance, support, operator, evidence and fixed runtime bindings")
        source = _projection(request)
        for offset, kind in ((1, "interval"), (3, "cumulative")):
            residual, residual_bounds, covariance, covariance_bounds = _oracle(source[kind], source["values"], request["covariance"]["matrix"])
            passed, detail = _compare(data[kind]["residual"], residual, residual_bounds)
            checks[offset].update(passed=passed, detail=detail)
            passed, detail = _compare(data[kind]["covariance"], covariance, covariance_bounds, matrix=True)
            checks[offset + 1].update(passed=passed, detail=detail)
    report = seal({"schema": REPORT_SCHEMA, "status": "PASS" if all(row["passed"] for row in checks) else "FAIL",
                   "scope": SCOPE, "request_ref": digest(request), "calculation_ref": digest(data),
                   "decision_policy_ref": digest(request["decision_policy"]), "checks": checks,
                   "authority": deepcopy(AUTHORITY), "limitations": deepcopy(LIMITATIONS)})
    validate_report(report)
    return report


def validate_report(report: dict) -> None:
    detached(report)
    keys(report, {"schema", "status", "scope", "request_ref", "calculation_ref", "decision_policy_ref",
                  "checks", "authority", "limitations", "record_digest"})
    check_seal(report)
    _require(report["schema"] == REPORT_SCHEMA and report["scope"] == SCOPE and
             report["authority"] == AUTHORITY and report["limitations"] == LIMITATIONS,
             "Leakage numerical audit scope or authority differs")
    for field in ("request_ref", "calculation_ref", "decision_policy_ref"):
        content_ref(report[field])
    rows = report["checks"]
    _require(type(rows) is list and len(rows) == len(CHECK_NAMES), "Leakage numerical audit check coverage differs")
    for row, name in zip(rows, CHECK_NAMES):
        keys(row, {"name", "passed", "detail"})
        _require(row["name"] == name and type(row["passed"]) is bool, "Leakage numerical audit check identity differs")
        text(row["detail"])
    _require(report["status"] == ("PASS" if all(row["passed"] for row in rows) else "FAIL"),
             "Leakage numerical audit status differs from its checks")
