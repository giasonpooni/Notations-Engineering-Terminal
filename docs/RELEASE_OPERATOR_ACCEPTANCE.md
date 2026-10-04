# Three scripted release operator journeys

Run the harness with a regular installed NET wheel and its core dependencies:

~~~sh
python -I scripts/check_release_operator.py --output-dir ../net-release-operator-001 --expected-wheel dist/your-built-wheel.whl
~~~

Use the actual wheel filename. The output must be new and outside the checkout;
a virtual environment containing the installed wheel should also be outside
the checkout. No optional private provider, native engine, or Legibility extra
is needed. The per-child timeout defaults to 180 seconds and can be set within
1–600 seconds with --timeout.

The launcher checks distribution provenance, isolated Python execution,
non-editable installation, core preflight, and, when supplied, every ciw package
file against the selected wheel. It copies the existing encoder fixtures into
the retained output and launches each journey in a separate isolated process.
The thermal source comes from the installed package; project fixtures use the
existing typed project authoring functions. Harness and fixture hashes travel
with the receipt.

| Journey | Original discrepancy | Explicit correction | Acceptance comparison |
| --- | --- | --- | --- |
| Encoder calibration | Existing synthetic original offset misses the 0.998 m reference | Existing corrected machine-manifest fixture | Absolute residual at most 0.0005 m |
| Thermal observation | A declared 5 K synthetic observation/noise amendment biases the first posterior | Restore the packaged, coherently declared synthetic thermal source | First core posterior absolute error at most 1 K and smaller than the biased case |
| Typed project graph | A result still pins the prior signal revision after a frame amendment | Append a result revision with the current input pin and frame | No declared result needs reevaluation |

These are three existing provider-free workflow kinds. The project journey
inspects declarations; it does not execute the computations declared by its
project objects. The thermal threshold is a fixture acceptance criterion, not
a physical instrument specification or calibrated measurement tolerance.

Every journey uses the actual Session request surface:

1. source.add and operation.execute retain the original source and result.
2. bundle.get, result.get and experiment.inspect reopen the original.
3. A retained comparison exposes the declared fixture discrepancy.
4. correction.propose is inert; correction.review accepts the explicit
   replacement source and marks the original result and dependent claim stale.
5. Explicit operation.execute creates the corrected occurrence. A historical
   bundle.replay remains stale.
6. bundle.replay reproduces the corrected numerical content with fresh bundle,
   execution and result identities.
7. A final offline reopen restores complete workbench records, correction
   journal and eligibility. Execution entry points are forbidden during the
   read-only checks. Prior stage directories must retain identical file hashes.

Read report.json at the root and within each journey. They retain request and
response JSON, source bytes, original/corrected/replayed bundles, comparison
reports, saved workspaces, checks and hashes. Failed children leave their logs
and any completed evidence; the root attempts the other journeys and remains
FAIL. Exit codes are 0 for PASS, 1 for failed acceptance, and 2 for refusal.

A PASS establishes scripted installed software acceptance for these fixtures.
Independent human usability, measured industrial pilot criteria, physical
validation, private-provider qualification, state admission and equipment
actuation remain unperformed. This harness does not close those release gates.
