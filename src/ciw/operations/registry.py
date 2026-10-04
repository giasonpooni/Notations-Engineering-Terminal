"""Explicit process-local operation registration; saved files never load code."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable


def valid_operation_id(value: object) -> bool:
    """A version is explicit; a syntactically valid name never loads a provider."""
    return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*\.v[1-9][0-9]*", value) is not None


@dataclass(frozen=True)
class Operation:
    operation_id: str
    role: str
    execute: Callable[[dict, dict], dict]
    runtime_identity: Callable[[], dict]

    def descriptor(self) -> dict:
        return {"operation_id": self.operation_id, "role": self.role}


class OperationRegistry:
    def __init__(self):
        self._operations: dict[str, Operation] = {}

    def register(self, operation: Operation) -> None:
        if operation.operation_id in self._operations:
            raise ValueError(f"Operation already registered: {operation.operation_id}")
        if not valid_operation_id(operation.operation_id) or operation.role not in {
            "analysis", "state_estimator", "calibration", "verification", "decision", "backend"
        }:
            raise ValueError("An operation needs a versioned identity and an explicit role")
        self._operations[operation.operation_id] = operation

    def get(self, operation_id: str) -> Operation:
        from ..adapters.protocol import AdapterRefusal
        if operation_id not in self._operations:
            raise AdapterRefusal("operation_unavailable", f"No trusted provider bound for {operation_id}")
        return self._operations[operation_id]

    def describe(self) -> list[dict]:
        return [item.descriptor() for item in self._operations.values()]


def default_registry() -> OperationRegistry:
    from ..adapters.registry import default_registry as adapters
    registry = OperationRegistry()
    for operation_id in ("statistics.v1", "spectrum.periodogram.v1"):
        registry.register(Operation(
            operation_id, "analysis",
            lambda run, parameters, name=operation_id: adapters().execute(name, run, parameters),
            lambda: {"provider": "ciw.oscillator", "version": "1"},
        ))
    from ..legibility_workflow import operation as legibility_operation
    registry.register(legibility_operation())
    from ..system_workflow import operations as system_operations
    for operation in system_operations():
        registry.register(operation)
    return registry
