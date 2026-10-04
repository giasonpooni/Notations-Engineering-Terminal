# Developing imported modules

The registry connects each co-located module tree to a retained native source
commit. Editing its files directly makes the source audit refuse the draft until
that connection is updated. The commands below prepare the updated source
identity and preserve the existing module history, package boundary and runtime
pins. They require Python 3.11 or newer, Git 2.46 or newer, complete retained
history and configured Git author/committer identities.

Start from an audited baseline and capture its full commit identity before
editing. Work on an attached draft branch. For example, for Calibration:

```sh
python scripts/superrepo.py audit
base_revision="$(git rev-parse HEAD)"
git switch -c draft/calibration-change
# Edit only instruments/measurement/calibration/ and commit the changes.
git add instruments/measurement/calibration
git commit -m "Describe the calibration change"
mkdir -p results
python scripts/superrepo.py module-prepare --role mcur --base "$base_revision" \
  --branch review/calibration-change --output-plan results/calibration-update.json
```

The draft must contain changes to exactly one registered module. A matching
change to that module's declared `version` in `instruments/manifest.json` is
allowed when its package metadata changes. Other registry fields, Terminal
files, licences, notices and other modules must remain unchanged. Commit all
intended changes before preparing; tracked byte/index drift and unexpected
ignored or untracked executable source are refused. Each plan is created
exclusively and cannot overwrite an existing plan.

Prepare constructs a native commit whose root tree is exactly the draft module
tree and whose sole parent is the previous native source commit. Its integration
candidate retains both the draft and that native source commit as parents. The
candidate changes only the selected module's source commit/tree fields beyond
the draft. All 21 source boundaries, metadata and execution bindings are audited
in a detached candidate checkout.

Inspect the plan's `candidate_revision` with `git show` or `git diff` and then
apply it from the same clean draft checkout:

```sh
python scripts/superrepo.py module-apply --plan results/calibration-update.json
git switch review/calibration-change
python scripts/superrepo.py audit
python scripts/superrepo.py doctor --group measurement
python scripts/superrepo.py check --group measurement --output-dir results/calibration-qualification
```

Apply recomputes the scope, native history, candidate tree and source audit from
Git objects. It refuses altered plans, changed draft bytes/HEAD, occupied branch
names and missing objects. It creates only the absent review branch; the caller's
draft branch, index and files stay in place. Publication holds a prepared Git
HEAD guard while a transaction from the detached candidate verifies the draft
reference and creates the review reference. This guards symbolic HEAD and branch
identity without relying on the files backend's unsupported combination of
HEAD and its referent in one transaction. Managed worktree mutations use the
existing shared lock. Ordinary filesystem edits are outside Git's ref locks;
candidate bytes come exclusively from committed objects.

A publication timeout reports whether the review reference is absent, names the
exact candidate or contains concurrent state. The command never deletes it.
If cleanup or a publication lifecycle check fails after branch creation, the
report retains the observed branch state and error, and the CLI exits nonzero.
Unpublished preparation objects can eventually be pruned by Git; prepare a new
plan if they are no longer available. Failed preparations can leave unreachable
objects and never require shared-object cleanup.

Source auditing establishes consistency and retained ancestry. It does not
qualify the changed package or its compositions, admit physical state or publish
a release. Run all affected gates and review their explicit exclusions before
merging the review branch with a native merge commit. Changes to Terminal
contracts or runtime pins belong in subsequent reviewed commits on that branch,
with compatibility evidence for affected compositions. Existing gate
expectations also require review when relevant: Surface's selected source SHA,
Polygon's original test count and the declared Metrology/Yield-Weighted package
versions are intentionally fixed in their checks. Preserve each original licence
and notice; licence changes require their own separately authorized review.
