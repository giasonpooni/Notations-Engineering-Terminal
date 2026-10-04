"""Read-only identity for NET's additive scientific system instrument."""
import hashlib
import platform
from pathlib import Path


def runtime_identity():
    root = Path(__file__).parent
    names = ("system_spec.py", "system_models.py", "system_workflow.py", "system_source.py", "system_study.py",
             "system_execution.py", "system_runtime.py", "core/identities.py")
    return {"provider": "ciw.scientific-system", "version": "2",
            "python": platform.python_version(),
            "source_files": {name: "sha256:" + hashlib.sha256((root / name).read_bytes()).hexdigest()
                             for name in names}}
