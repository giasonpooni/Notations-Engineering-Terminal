# Legibility version comparison

Run `ciw legibility compare before/bundle.json after/bundle.json --output comparison.json`.

Both bundles must recompile exactly and describe the same object. The comparison
retains before/after values and paths as arrays, so identifiers containing slashes
remain unambiguous. Claims, properties, artifacts and annotations are matched by
their explicit IDs; their list ordering does not create semantic changes.
Scientific and evidence changes precede object labels in the output.

Versions are opaque strings: comparison does not infer chronology or improvement.
Changed source content under the same version produces a conflict flag and CLI
exit code 2. A source-order change can produce this flag with no semantic changes,
because source digests still cover list ordering. Invalid bundles and mismatched
objects also return exit code 2. Output files are created exclusively.

The comparison identity is a deterministic content hash, not a signature. This
operation checks bundle consistency only. Run `ciw legibility verify` separately
for artifact bytes, exports, signatures, explicit trust and freshness expectations.
Physical validation remains not assessed and canonical admission remains false.
