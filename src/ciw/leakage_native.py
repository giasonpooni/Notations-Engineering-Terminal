"""Pinned FSRT conservation kernels; operator-selected paths never come from evidence.

Volume uses the provider's support-aware BalanceRecord unchanged. Mass uses its
public typed exact linear-constraint residual and covariance APIs, with kg row
units and explicitly constructed support coefficients. No reconciliation runs.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import sys

from .adapters.subprocess import AdapterRefusal, PinnedSubprocessAdapter, _json
from .core.identities import canonical_json
from .leakage_contract import (AUTHORITY, COVARIANCE_UNITS, LIMITATIONS, RESULT_SCHEMA,
                               evidence_group, raw_order, raw_readings, validate_request)
from .operations.runner import digest

PIN = "09a756dd9cdd3a9bb6cb14b5cd498f6259937ac2"
SOURCE_TREE = "c79c44f90d5ff3920e58a53e9c277104e7e07bd3"
MODULE = "set_lcm.bridge.ciw"
SOURCE_ROOT = "src"
MAX_CONDITION_NUMBER = 1e12


def kernel_profile(basis: str) -> dict:
    if basis not in {"volume", "mass"}:
        raise ValueError("Unsupported leakage conservation basis")
    api = ("set_lcm.measurement.balance_residuals" if basis == "volume" else
           "set_lcm.lcm.residual + set_lcm.fdi.residual_covariance")
    return {"provider": "fsrt", "revision": PIN, "module": MODULE,
            "profile": "volume_instant_integrated_totals" if basis == "volume" else "mass_exact_linear_conservation_spd",
            "interval_api": api, "cumulative_api": api,
            "residual_sign": "storage_change_minus_net_inflow",
            "coefficient_uncertainty": "declared_exact",
            "covariance_policy": "full_declared_psd" if basis == "volume" else "full_declared_spd",
            "maximum_condition_number": None if basis == "volume" else MAX_CONDITION_NUMBER}


def check_runtime(runtime: dict) -> None:
    expected = {"schema", "adapter_version", "repository_root", "revision", "source_tree", "module",
                "source_root", "python_executable", "python_sha256", "python_version", "dependencies"}
    if type(runtime) is not dict or set(runtime) != expected:
        raise ValueError("Require complete pinned subprocess runtime identity")
    if (runtime["schema"] != "ciw.subprocess-runtime.v1" or runtime["adapter_version"] != "ciw-pinned-subprocess-v1"
            or runtime["revision"] != PIN or runtime["module"] != MODULE or runtime["source_root"] != SOURCE_ROOT):
        raise ValueError("Leakage requires the exact FSRT native source profile")
    if type(runtime["dependencies"]) is not dict or set(runtime["dependencies"]) != {"numpy", "scipy"}:
        raise ValueError("Require recorded numerical dependency versions")
    import re
    if type(runtime["python_version"]) is not str or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", runtime["python_version"]):
        raise ValueError("Require complete native Python version")
    for dependency, version in runtime["dependencies"].items():
        if version is None and dependency == "scipy":
            continue
        if type(version) is not str or not 1 <= len(version) <= 64 or not re.fullmatch(r"[0-9]+\.[0-9]+[A-Za-z0-9.+-]*", version):
            raise ValueError("Invalid retained native numerical dependency version")
    try:
        python_version = tuple(int(v) for v in runtime["python_version"].split(".")[:2])
        numpy_major = int(runtime["dependencies"]["numpy"].split(".")[0])
    except (TypeError, AttributeError, ValueError) as exc:
        raise ValueError("Cannot validate native Python/NumPy versions") from exc
    if python_version < (3, 12) or numpy_major < 2:
        raise ValueError("FSRT requires Python >=3.12 and NumPy >=2.0")
    if runtime["source_tree"] != SOURCE_TREE:
        raise ValueError("Retained native source tree differs from the fixed FSRT commit tree")
    if type(runtime["python_sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", runtime["python_sha256"]):
        raise ValueError("Invalid retained native executable digest")
    for field in ("repository_root", "python_executable"):
        if type(runtime[field]) is not str or not (
                PurePosixPath(runtime[field]).is_absolute() or PureWindowsPath(runtime[field]).is_absolute()):
            raise ValueError("Native operator binding paths must be absolute")


validate_runtime = check_runtime


_BOOTSTRAP = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import numpy as np
request = json.loads(sys.stdin.buffer.read())
try:
    edges = np.asarray(request['edges_s'], dtype=float)
    n = len(edges)-1
    channels = request['channels']
    m = len(channels)
    storage = np.asarray([row['value'] for row in request['inventory']['readings']], dtype=float)
    flows = np.asarray([[channel['readings'][i]['value'] for channel in channels] for i in range(n)], dtype=float)
    signs = np.asarray([1.0 if channel['direction']=='in' else -1.0 for channel in channels])
    P = np.asarray(request['covariance']['matrix'], dtype=float)
    if not np.array_equal(0.5*P+0.5*P.T, P):
        raise ValueError('native covariance arithmetic cannot preserve the declared raw covariance')
    raw_rows = request['inventory']['readings'] + [channel['readings'][i] for i in range(n) for channel in channels]
    groups = [list(dict.fromkeys(row['evidence_refs']+[row['calibration_ref'],row['clock_ref']])) for row in raw_rows]
    if request['basis']=='volume':
        from set_lcm.measurement import BalanceRecord, balance_residuals
        record = BalanceRecord(edges=edges, storage=storage, flows=flows, flow_signs=signs,
                               covariance=P, storage_support='instant', volume_unit='m3',
                               flow_unit='m3', flow_support='total', evidence_ids=tuple(tuple(g) for g in groups))
        if not np.array_equal(record.covariance, P):
            raise ValueError('native BalanceRecord changed the declared raw covariance')
        interval = balance_residuals(record, cumulative=False)
        cumulative = balance_residuals(record, cumulative=True)
        series = [(s.operator, s.residual, s.covariance, [list(g) for g in s.evidence_ids]) for s in (interval,cumulative)]
    else:
        from set_lcm.schema import ConstraintSet
        from set_lcm.lcm import residual, check_spd
        from set_lcm.fdi import residual_covariance
        check_spd(P)
        H = np.zeros((n,n+1+n*m))
        for i in range(n):
            H[i,i], H[i,i+1] = -1.0, 1.0
            H[i,n+1+i*m:n+1+(i+1)*m] = -signs
        y = np.concatenate([storage, flows.ravel(order='C')])
        series = []
        for label, operator in [('interval',H),('cumulative',np.cumsum(H,axis=0))]:
            cs = ConstraintSet(version='ciw.leakage.mass.'+label+'.v1', A=operator, b=np.zeros(n),
                               description='Exact declared-support kg inventory conservation; no projection',
                               row_units=('kg',)*n)
            r = residual(y,cs)
            A_r, C = residual_covariance(P,cs)
            if not np.array_equal(A_r,operator):
                raise ValueError('native mass kernel changed the declared support row basis')
            check_spd(C,'mass residual covariance')
            if np.linalg.cond(C)>1e12:
                raise ValueError('mass residual covariance exceeds the conditioning budget')
            evidence = [list(dict.fromkeys(ref for j,value in enumerate(row) if value != 0 for ref in groups[j]))
                        for row in operator]
            series.append((A_r,r,C,evidence))
    result = {}
    for index,label in enumerate(('interval','cumulative')):
        H,r,C,evidence = series[index]
        if not all(np.all(np.isfinite(array)) for array in (H,r,C)):
            raise ValueError('native conservation output is not finite')
        if np.any(np.diag(C)<0):
            raise ValueError('native arithmetic produced negative residual variance; no repair')
        result[label] = {'operator':H.tolist(), 'residual':r.tolist(), 'covariance':C.tolist(),
                         'support':[{'start_s':float(edges[i]) if label=='interval' else float(edges[0]),
                                     'end_s':float(edges[i+1])} for i in range(n)], 'evidence_refs':evidence}
    print(json.dumps({'status':'ok','data':result},allow_nan=False,separators=(',',':')))
except (ValueError,TypeError,np.linalg.LinAlgError) as exc:
    print(json.dumps({'status':'refused','reason':str(exc)},allow_nan=False,separators=(',',':')))
'''


class NativeLeakageBackend:
    """Explicit trusted checkout/interpreter binding, checked around every call."""
    def __init__(self, repository: Path, python: Path | None = None) -> None:
        manifest = json.loads(Path(__file__).with_name("adapter-runtimes.json").read_text())["fsrt"]
        if manifest["revision"] != PIN or manifest["module"] != MODULE:
            raise ValueError("FSRT manifest differs from the supported leakage native profile")
        self.adapter = PinnedSubprocessAdapter(repository, PIN, MODULE, source_root=SOURCE_ROOT,
                                                python_executable=python if python is not None else sys.executable,
                                                max_output_bytes=2 * 1024 * 1024)
        check_runtime(self.adapter.runtime_identity())

    def runtime_identity(self) -> dict:
        runtime = self.adapter.runtime_identity()
        check_runtime(runtime)
        return runtime

    def calculate(self, request: dict) -> dict:
        request = validate_request(request)
        before = self.runtime_identity()
        code, payload = self.adapter._run(_BOOTSTRAP, [str(self.adapter.source_root)],
                                          canonical_json(request).encode())
        after = self.runtime_identity()
        if before != after:
            raise AdapterRefusal("LEAKAGE_RUNTIME_CHANGED", "Bound native runtime changed during calculation")
        if code:
            raise AdapterRefusal("LEAKAGE_NATIVE_FAILED", "Pinned FSRT failed the bounded conservation calculation")
        reply = _json(payload)
        if type(reply) is not dict:
            raise AdapterRefusal("LEAKAGE_MALFORMED_RESPONSE", "Native conservation reply is not an object")
        if set(reply) == {"status", "reason"} and reply["status"] == "refused" and type(reply["reason"]) is str:
            raise AdapterRefusal("LEAKAGE_UNSUPPORTED_COVARIANCE", reply["reason"])
        if (set(reply) != {"status", "data"} or reply["status"] != "ok" or type(reply["data"]) is not dict
                or set(reply["data"]) != {"interval", "cumulative"}):
            raise AdapterRefusal("LEAKAGE_MALFORMED_RESPONSE", "Native conservation reply has an unknown shape")
        readings = raw_readings(request)
        result = {"schema": RESULT_SCHEMA, "request_ref": digest(request), "basis": request["basis"],
                  "unit": request["unit"], "covariance_unit": COVARIANCE_UNITS[request["basis"]],
                  "kernel": kernel_profile(request["basis"]), "runtime": before,
                  "raw": {"order": raw_order(request), "values": [r["value"] for r in readings],
                          "covariance": deepcopy(request["covariance"]["matrix"]),
                          "evidence_refs": [deepcopy(r["evidence_refs"]) for r in readings],
                          "calibration_refs": [r["calibration_ref"] for r in readings],
                          "clock_refs": [r["clock_ref"] for r in readings],
                          "covariance_evidence_refs": deepcopy(request["covariance"]["evidence_refs"])},
                  **reply["data"], "authority": deepcopy(AUTHORITY), "limitations": deepcopy(LIMITATIONS)}
        self._check_response(request, result)
        return result

    @staticmethod
    def _check_response(request: dict, result: dict) -> None:
        """Bound native shape/support/operator/provenance; verifier owns the oracle."""
        n, m = len(request["edges_s"]) - 1, len(request["channels"])
        size = len(result["raw"]["values"])
        H = [[0.0] * size for _ in range(n)]
        for i in range(n):
            H[i][i], H[i][i + 1] = -1.0, 1.0
            for j, channel in enumerate(request["channels"]):
                H[i][n + 1 + i * m + j] = -1.0 if channel["direction"] == "in" else 1.0
        cumulative = [[sum(H[k][j] for k in range(i + 1)) for j in range(size)] for i in range(n)]
        groups = [evidence_group(r) for r in raw_readings(request)]
        from .control_contracts import keys, number
        for label, expected in (("interval", H), ("cumulative", cumulative)):
            series = result[label]
            keys(series, {"operator", "residual", "covariance", "support", "evidence_refs"})
            if series["operator"] != expected:
                raise ValueError("Native conservation operator changed the declared support semantics")
            if (type(series["residual"]) is not list or len(series["residual"]) != n
                    or type(series["covariance"]) is not list or len(series["covariance"]) != n):
                raise ValueError("Native conservation result has inconsistent dimensions")
            for value in series["residual"]:
                number(value)
            for row in series["covariance"]:
                if type(row) is not list or len(row) != n:
                    raise ValueError("Native residual covariance has inconsistent dimensions")
                for value in row:
                    number(value)
            support = [{"start_s": request["edges_s"][i] if label == "interval" else request["edges_s"][0],
                        "end_s": request["edges_s"][i + 1]} for i in range(n)]
            evidence = [list(dict.fromkeys(ref for j, value in enumerate(row) if value != 0 for ref in groups[j]))
                        for row in expected]
            if series["support"] != support or series["evidence_refs"] != evidence:
                raise ValueError("Native conservation support or evidence lineage changed")
