# Fluid and material balance assessment

The agent interface uses the same two retained operations and requires an
explicit native binding at launch:

```bash
python -m ciw.agent_mcp leakage-config --basis volume --output-dir leakage-agent
python -m ciw.agent_mcp serve --instrument leakage --profile leakage-agent/profile.json \
  --provider-checkout /absolute/path/to/net-providers/leakage-001/fsrt
```

Use `--basis mass` for the material profile and `--python` when selecting a
qualified native interpreter. Saved profiles and evidence cannot select provider
code. The finite `qualify` command also checks both typed agent graphs,
idempotent retries and fresh replays.

`net polymer leakage` executes NET's retained balance instrument using the
existing, pinned FlowState Reconstruction Testbed (FSRT). It accepts coolant or
hydraulic-fluid **volume amounts in m³**, and resin or material **mass amounts
in kg**. Both use signed inventory and boundary-transfer accounting. A measured
shortfall can be consistent with leakage, an omitted outlet, inventory error,
or meter bias; this instrument does not confirm its cause.

This is a working numerical instrument with synthetic readiness fixtures. A
successful qualification establishes that the selected native source, runtime,
retention, replay and numerical audit work for those finite fixtures. It does
not establish physical calibration, plant accuracy or commissioning.

## Provision the existing native source

Leakage needs only the existing FSRT source. From the NET monorepo root,
materialize its retained commit in a new standalone checkout:

```bash
git clone --shared --no-checkout --config core.autocrlf=false --config core.hooksPath=/dev/null . ../net-providers/leakage-001/fsrt
git -C ../net-providers/leakage-001/fsrt checkout --no-recurse-submodules --detach 09a756dd9cdd3a9bb6cb14b5cd498f6259937ac2
net polymer leakage doctor --provider-checkout ../net-providers/leakage-001/fsrt
```

The backend validates this exact Git commit, source tree and working bytes before
execution. Keep the NET source checkout available because the new checkout
shares its retained Git objects. These Git commands do not install dependencies
or execute the provider. A fresh output path is required.

If the existing `net provision --workflow measurement-chain` already produced
an `fsrt` checkout at this revision, reuse it. That broader workflow also requires
RCI and JSPT pins; its plan can refuse when those unrelated historical pins are
unavailable. They are not needed by leakage. `calibrated-window` has no FSRT
role, and `calibrated-observable` selects a different FSRT revision.

FSRT at this revision declares Python 3.12 or newer and NumPy 2.0 or newer.
Use a Python 3.12 or newer interpreter with NET's pinned NumPy 2.4.3. If the
interpreter running NET already meets those requirements, omit `--python`.
Otherwise, select a separate runtime explicitly:

```bash
python3.12 -m venv ../net-leakage-runtime
../net-leakage-runtime/bin/python -m pip install 'numpy==2.4.3'
net polymer leakage doctor --provider-checkout ../net-providers/leakage-001/fsrt --python ../net-leakage-runtime/bin/python
```

The backend loads the approved source directly; installing the FSRT package
into its checkout is unnecessary. Never edit or substitute that source to make
a readiness check pass. A provider path is always an explicit operator choice
on an execution command. Retained requests and reports do not select executable
directories.

## Run the finite readiness gate

```bash
net polymer leakage qualify --provider-checkout ../net-providers/leakage-001/fsrt --output-dir leakage-qualification
```

Add `--python ../net-leakage-runtime/bin/python` if using the separate runtime.
Qualification executes both volume and mass fixtures, makes fresh numerical
audits, repeats native execution in new replay workspaces, and exports retained
results. It keeps workflow readiness separate from the balance decision. The
fixtures can correctly show a shortfall while their numerical audits pass.
Use a new output directory for every qualification or replay.

## Run, inspect, verify and export

```bash
net polymer leakage example --basis volume --output volume.request.json
net polymer leakage run volume.request.json --provider-checkout ../net-providers/leakage-001/fsrt --output-dir volume-run
net polymer leakage inspect volume-run
net polymer leakage verify volume-run --output volume-fresh-audit.json
net polymer leakage replay volume-run --provider-checkout ../net-providers/leakage-001/fsrt --output-dir volume-replay
net polymer leakage export volume-run --output-dir volume-export

net polymer leakage example --basis mass --output mass.request.json
net polymer leakage run mass.request.json --provider-checkout ../net-providers/leakage-001/fsrt --output-dir mass-run
```

The same fixtures are retained in
[`examples/leakage/volume.request.json`](../examples/leakage/volume.request.json)
and [`examples/leakage/mass.request.json`](../examples/leakage/mass.request.json).
They are synthetic evidence, not factory observations or process recipes.
Each interval has 1.0 incoming, 0.8 outgoing and a 0.15 inventory increase:
the residual is approximately −0.05 per interval and −0.20 over the window,
in the selected m³ or kg unit.

`inspect` and `export` validate and read retained evidence without running FSRT.
Retained runtime paths may be absolute POSIX or Windows paths from the original
host; reading them does not resolve or execute those paths on the current host.
`verify` creates a new, independent finite numerical audit without rerunning the
native provider or changing the saved result. `--output` saves that receipt to
a new file. `replay` retains the same request and evidence while creating a new
execution and result occurrence. Select the same native source, interpreter
and dependency identities for replay; pass the same `--python` when using a
separate runtime. All output files and directories are
create-only. Native execution, retained evidence integrity, numerical audit,
and the declared boundary-balance decision are separate outcomes.
CLI exit code 0 means the command completed; a valid numerical workflow can
still report `UNACCOUNTED_LOSS`. Input or provider-binding errors return 1;
a reported workflow or audit refusal/failure returns 2.

To associate the balance with an existing polymer assessment, run:

```bash
net polymer leakage run volume.request.json --provider-checkout ../net-providers/leakage-001/fsrt --polymer-workspace injection-demo --output-dir coupled-volume-run
```

`injection-demo` is a polymer run directory containing `workspace.json`. The
request must match the assessment's declared process, frame, source kind, clock
identity, and facility, part, tool, machine, cycle and material-lot identities.
Its intervals must lie inside the declared cycle. The workflow retains a
reference to the
actual assessment occurrence. This association does not establish that a fluid
shortfall caused a dimensional defect or that either measurement is physically
qualified.

## What the balance computes

For each interval, let \(S\) be the inventory at its boundary times, \(F\) the
integrated incoming amounts, and \(O\) the integrated outgoing amounts. The raw
balance residual is

\[
r_i = S_{i+1} - S_i - \sum F_i + \sum O_i.
\]

A negative residual is an accounted boundary deficit. The report evaluates
that deficit against the caller's declared loss threshold and coverage factor,
including retained uncertainty. It cannot distinguish an undeclared physical
outlet from a biased inlet or inventory reading on balance evidence alone.

| Boundary | Inventory and transfer examples | Scope requirement |
| --- | --- | --- |
| Coolant loop | Fluid inventory, supply, return, drains and makeup | Choose a control volume that includes every relevant storage and outlet. |
| Hydraulic circuit | Reservoir and actuator/accumulator inventory, supply, return and drains | Account for stored fluid changes; supply minus return alone is insufficient. |
| Resin or material | Stock, feed, product, runners, rejects, purge and regrind | Declare one consistent mass basis and the boundary for internal transfers. |

Transfers wholly inside the selected boundary are not external loss. Drying,
moisture removal, volatile release and additions require explicit treatment
when relevant to the chosen mass basis. These declarations remain operator
assumptions; the software does not discover missing streams.

Volume accounting requires a suitable effectively incompressible inventory
basis. Pressure, temperature or composition changes can change fluid volume
without a material escape. This version does not infer density or compensate
for compressibility and thermal expansion.

The volume lane calls FSRT's existing support-aware `BalanceRecord` and
`balance_residuals`. The mass lane uses FSRT's public generic `ConstraintSet`,
`residual` and `residual_covariance` APIs with genuine kg units. It does not
relabel kg as m³ or modify the historical volume-only API. Both retain the raw
readings and signed observation map before any balance reconciliation.
Reconciled readings are inappropriate as the input to this detector because a
balance constraint can force their residual to zero.

## Evidence and uncertainty contract

The bounded `ciw.leakage-request.v1` request declares:

- A volume or mass basis, one boundary identity, explicit process identities,
  frame and clock references, and contiguous interval edges.
- Instant inventory readings exactly at those edges, and nonnegative
  `integrated_total` readings for every channel over every interval. Directions
  are `in` or `out`; purpose labels describe feed, product, runners, rejects,
  purge, regrind, return or another declared stream.
- Source evidence, calibration and clock references on every reading, plus a
  full raw covariance matrix with its own evidence references and exact order.
- A loss threshold, coverage factor and threshold-reference identity.

Raw covariance order is inventory readings first, then transfers in interval
order and declared channel order. Its unit is m⁶ for volume and kg² for mass.
The native calculation preserves covariance between readings, intervals and
channels. Interval and cumulative residual uncertainty come from the full
observation map, not from a sum of marginal standard uncertainties. Shared
readings can therefore cancel correctly in a cumulative balance.

The initial instrument accepts only integrated amounts and instant inventory
samples under `declared_exact_interval_labels`. It does not integrate sampled
flow rates, estimate missing inventory, or convert statistical clock uncertainty
into a deterministic interval-membership certificate. Evidence references
identify retained declarations; they do not independently attest calibration
or provenance. Synthetic and retained-observation sources remain visibly
distinct.

The independent verification scope is finite numerical consistency. A passing
audit checks the signed map, residuals, covariance propagation, cumulative
accounting and evidence bindings against the retained inputs. The workflow's
offline validation also binds the reported decision bands to the retained
calculation and declared policy. These checks do not grant physical-validation
authority. Threshold decisions include a conservative source-derived allowance
for native binary64 rounding and outward-rounded interval bounds; the nominal
declared band and the decision band are retained separately. Invalid covariance, incompatible
units, incomplete supports, broken references or unsupported numerical states
are rejected rather than silently repaired.

This instrument adds boundary accounting to the polymer evidence stack. It
does not implement polymer-melt rheology, Newtonian melt assumptions, a CFD
leak model, inferred leak location, automatic PLC writes, or confirmed causal
diagnosis.
