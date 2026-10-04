# System reference worker image

This image packages the same calculation worker used by the local subprocess
adapter. A candidate still requires the session's independent structural and
scientific verification. A container does not establish numerical validity,
physical control timing, or experimental validation.

Build a NET wheel from the repository root:

```sh
python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
```

Select the official `python:3.12.14-slim` base by its verified immutable digest,
then build the worker image. Replace the placeholders with actual digests;
they are deliberately not qualified runtime references.

```sh
docker build -f containers/system-reference/Dockerfile \
  --build-arg PYTHON_BASE=python:3.12.14-slim@sha256:<verified-base-digest> \
  -t net-system-reference:development .
```

The build installs the repository's pinned NumPy 2.4.3 and websockets 16.0
dependencies. Build-time package downloads are distinct from execution, which
has networking disabled. The operator must retain the build inputs and image
digest to qualify a deployment. The version pins alone do not attest the
complete image or establish reproducible image bytes.

Load or publish the image to obtain its immutable `name@sha256:...` reference
and explicitly provide that reference to the NET container engine. The adapter
uses `--pull never`; supplying a digest does not authorize a registry download.
It refuses missing Docker, mutable image tags, timeouts, oversized output,
invalid JSON, and incompatible candidate schemas. It never switches engines
or retries a failed calculation.

Execution runs as the caller's uid/gid with a read-only root filesystem,
disabled networking, all capabilities dropped, no new privileges, the validated
specification's CPU and memory quotas (defaults: one CPU and 256 MiB), 64
processes, a bounded temporary filesystem, and one writable
task directory mounted at `/work`. That mount contains only the serialized
specification and output candidate. Workers are limited to a 1 MiB input,
16 MiB candidate, 64 KiB combined diagnostics, and a configurable timeout of
at most 300 seconds. Large otherwise admissible configurations can exceed the
candidate budget and are refused explicitly.

The runtime receipt records the declared resources and applied timeout. CPU
and memory quotas are enforced by OCI execution. Local and subprocess execution
record the resources as requested configuration without claiming those quotas
are enforced; subprocess execution still enforces its timeout and byte budgets.

No Docker daemon or Podman engine was available in the implementation workspace.
The subprocess calculation and adapter boundary checks can be verified there;
the image build and real OCI execution require an operator-provisioned runtime
and remain unverified until those checks run.

## Actual OCI qualification

The dedicated Linux CI job runs a real Docker daemon rather than treating an
adapter mock as container qualification. Run the same gate on a POSIX machine
with Docker and Python 3.12.14:

```sh
python scripts/qualify_system_oci.py --output-dir results/system-oci
```

The gate resolves the official Python and registry fixture tags to observed
repository digests, records their image inspections, and builds the NET wheel
against the immutable Python base. It starts a unique registry bound only to
IPv4 loopback on an ephemeral port. The image is pushed to that fixture and
pulled back by its recorded `name@sha256:...` reference. No public registry is
used, and the fixture is removed at the end. Image acquisition and build-time
dependency downloads precede the worker's `--pull never` execution.

The same retained Session runs a 128-cell configuration locally, in a subprocess,
and in OCI, plus its declared 64-cell reduction. The gate requires exact
candidate equality across all three deployments, separately identified passing
numerical reports, zero deployment comparison error, and a passing declared
reduction comparison. It checks the OCI image/resources receipt and creates a
separate actual container policy probe whose Docker inspection verifies memory,
CPU, process, user, privilege, filesystem and network settings. The probe also
compares packaged Python/dependency versions and source hashes with the local
worker. That probe is separate from the adapter's already-removed solver
container; the solver retains its own execution receipt.

CI uploads `checks.json`, `build-provenance.json`, command logs, Docker/image
inspections, the source wheel, `summary.json`, the retained `session/workspace.json`
and its recording/results/executions. Provenance records the exact git revision,
working tree changes, source file hashes, wheel hash, resolved base and worker
digests. Source and image identity do not establish reproducible image bytes or
experimental validation. A missing daemon or failed check exits with status 2
and leaves a failure report; the gate neither skips OCI nor substitutes another
engine. The source workspace remains unchanged during its restore check.

Docker documents the [image push/digest workflow](https://docs.docker.com/reference/cli/docker/image/push/),
[loopback port publishing](https://docs.docker.com/engine/network/port-publishing/)
and [`--pull never` execution](https://docs.docker.com/reference/cli/docker/container/run/).
