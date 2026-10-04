# NET qualified research release

The next milestone is a dependable public research baseline: one immutable
candidate, completed software checks, retained evidence, and three usable
operator journeys. This page makes the 2026-10-03 audit actionable. It does
not declare a release complete.

The reviewed starting point is
`ffad7dba2b258339b27170e071c0de431f6f2aef`, containing 21 imported modules.
A [read-only baseline observation](../release/baseline-observation.json) records
all eight selected runs as queued/pending; it is not an executed collector report.
The integrated candidate is published on `release/net-research-candidate`.
Resolve that ref once and use its full immutable SHA for qualification. The
machine-readable [release scope](../release/release-scope.json) records the
starting commit, retained integration inputs, PR dispositions, and blockers. The
[capability ledger](../release/capability-ledger.json) separates documented
implementation, installation evidence, local availability, and qualification.

## First milestone and acceptance

1. Qualify the combined #135/#137 infrastructure, #144 shared contracts,
   #124 typed composition, and #128 Legibility reconciliation. Exact source
   heads and reconciliation commits are recorded in the scope JSON. #137 retains an original SET commit as additional
   ancestry: use a merge commit, because squash/rebase can discard history
   required for exact provider provisioning.
2. Select one full commit SHA after integration. Review the
   [CI policy](../release/qualification-policy.json) against its actual workflow
   definitions. The policy declares 46 required jobs across 12 workflows, including the
   integrated matrices, real OCI execution, installed Legibility checks and
   three correction/replay journeys, and one identical candidate wheel
   exercised on Linux and Windows. Missing or renamed jobs block acceptance.
3. Complete the selected public source, independent-wheel, installed-package,
   replay, Linux/Windows, and deployment lanes on that commit. Run the evidence
   collector and inspect retained artifacts. An old branch's green run cannot
   qualify a new integrated commit.
4. Review each selected capability's actual numerical/reference and retained
   replay evidence. Metadata and artifact existence alone cannot close this gate.
5. Have a new operator complete three documented run → inspect → compare →
   correct → replay journeys. The automated precursor uses encoder residual, synthetic thermal-observer,
   and typed-project revision journeys through supported correction APIs;
   installed impact/Legibility checks remain a separate required lane. Record commands,
   candidate wheel digest, environment, outputs, failures and elapsed operator
   effort. Do not relabel a provider-free reopen as fresh replay.

The first milestone closes only when PR dispositions are reconciled, public
checks finish on the selected candidate, capability evidence is reviewed, and
external blockers are named. The operator exercise closes the subsequent alpha
milestone; it remains explicitly open in the scope record.

## Collect and replay CI evidence

The collector uses Python 3.11+ and the standard library. It performs read-only
GitHub API requests and does not dispatch scientific providers or merge PRs.
Set `GITHUB_TOKEN` through your normal environment if authentication/rate limits
require it; only repository/Actions read access is needed.

```sh
python scripts/check_release_evidence.py --sha FULL_40_CHARACTER_COMMIT_SHA --output results/release-candidate-001
python scripts/check_release_evidence.py --sha FULL_40_CHARACTER_COMMIT_SHA --evidence results/release-candidate-001/evidence.json --output results/release-candidate-001-recheck
python -m unittest discover -s tests -p test_release_evidence.py -v
```

The output directory must be new; the collector refuses to overwrite previous
observations. The **NET release evidence** workflow tests the gate on Linux and Windows
with Python 3.11/3.12. A push to the isolated `release/**` candidate branch
collects metadata for that exact SHA; manual `candidate_sha` is also supported.
It uses a read-only token and uploads the report even when blocked. If the
collector finishes before other qualification lanes, its blocked report is
retained. After those lanes complete, rerun the collector job on the same SHA.
The separately required [installed-journey workflow](RELEASE_OPERATOR_ACCEPTANCE.md)
creates a fresh virtual environment outside the checkout and runs the exact
built wheel under Python isolation. It retains candidate identity, wheel digest,
reports, workspaces, command logs, and wheel bytes on Linux and Windows.
The separate installed operator-readiness lane builds one portable wheel once,
passes its digest independently to both platform jobs, and checks installed
source/resource bytes before and after the journey. Its report does not claim
source-to-binary attestation. These scripted synthetic checks do not substitute
for a new human operator.

The collector emits `evidence.json` and `report.json`, with the candidate SHA,
canonical policy digest, run links, and specific blockers. Exit codes are 0 for
`ci_pass`, 1 for incomplete/failed selected CI, and 2 for invalid input or an
unrecoverable collection/output error. `ci_pass` means only that the declared
CI metadata and retained-artifact presence requirements were satisfied.

The gate requires the latest eligible push/manual run for each named workflow,
exact repository/path/SHA and attempt identities, completed successful required
jobs, and current-attempt, nonempty, unexpired named artifacts. Missing,
queued, cancelled, neutral, skipped-required or failed checks cannot become
passes. A newer failing run supersedes an older green one. Collection rejects
observed changes in pagination, run state, or latest-run identity; this remains
a point-in-time observation, not an atomic GitHub snapshot.

PR-triggered evidence is excluded from release acceptance because branch/merge
test identity is separate from the integrated candidate. The selected public
jobs in `test.yml` are assessed independently of its two explicitly excluded
private PLSR jobs. Exclusions remain visible in the policy/report and do not
qualify those providers. A failed overall workflow is acceptable only when its
failure is explained by an explicitly excluded failing job and every included
job succeeds. The optional FlowState slow reproduction may be skipped; if it
runs and fails, the gate blocks. Full slow reproduction remains a separate
claim and is not established by this baseline.

Saved snapshots are unauthenticated inputs. Offline evaluation verifies their
structure and consistency; it does not prove they came from GitHub. Artifact
metadata does not authenticate artifact bytes, prove successful science, or
establish source-to-binary identity. Preserve and inspect the actual artifacts
for the separate capability-evidence review.

## Recorded PR dispositions

The selected foundation PR histories are retained in the candidate ancestry.
PRs remain open until their integration is qualified; other dispositions record
release scope rather than claiming completion of those increments.
Exact observed heads and rationales are retained in the scope JSON. Heads may
advance; refresh them before any action. “Superseded candidate” requires a
semantic comparison before closure; none of these recorded heads was an
ancestor of the reviewed main commit.

| PR | Release disposition | Reason |
| --- | --- | --- |
| [#148](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/148) | defer | Additional signal-processing and pump profiles need a separate integrated qualification. |
| [#146](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/146) | defer | New graphics, browser and export surfaces expand the first release. |
| [#145](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/145) | defer | New numerical profiles and installed journeys belong in a later qualified batch. |
| [#144](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/144) | prerequisite | Reconcile and verify relevant evidence-contract fixes before qualification; audit claims alone are insufficient. |
| [#143](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/143) | defer | New coupled models and held-out comparisons add scientific obligations. |
| [#142](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/142) | defer | Useful new scope, not a baseline prerequisite. |
| [#141](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/141) | defer | Additional profiles require their own integrated qualification. |
| [#139](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/139) | exclude transport | Transport PR explicitly says not to merge; native publication and retirement remain separate. Baseline contains 21 imports. |
| [#137](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/137) | prerequisite | Public provisioning and Windows fixes. Preserve required native ancestry with a merge commit; active repairs are separate. |
| [#135](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/135) | prerequisite | History-preserving module maintenance, preflight and Windows guards require current integrated evidence. |
| [#128](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/128) | prerequisite | Operator review/comparison increment included in the completion pass; verify installed impact import and identity boundaries. |
| [#124](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/124) | prerequisite | Typed composition foundation included in the completion pass; retain public OCI and installed evidence on the integrated candidate. |
| [#114](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/114) | defer | Measurement ingress is an additional increment, not delivered by existing atmosphere/impact code. |
| [#100](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/100) | defer | Restack and reconcile against current realization code before qualification. |
| [#99](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/99) | superseded candidate | Main has a different implementation; compare semantics and preserve missing requirements before any closure. |
| [#98](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/98) | superseded candidate | Main has different expansion code and a workflow; semantic reconciliation is still required. |
| [#78](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/78) | defer | Unattended invocation adds separate operational qualification. |
| [#66](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/66) | superseded candidate | Current README uses later framing; reconcile editorial intent separately. |
| [#64](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/64) | defer | Additional pinned native raster pipeline on an old stack. |
| [#63](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/63) | defer | Stateful agent execution and cleanup need their own current-head qualification. |
| [#53](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/53) | superseded candidate | Current README has been rewritten; preserve useful remaining editorial details selectively. |

#139 is history transport, not an integration PR. Its publication/verification
and any source-retirement decision are separate. The credential used by the
existing native-history publication attempt lacked permission to publish
workflow-containing history. That is an external blocker, not a numerical
failure, and retrying unchanged credentials does not resolve it.

## Sequence after the baseline

| Increment | Deliverable | Evidence to close |
| --- | --- | --- |
| Baseline | Reconciled backlog and immutable candidate | Selected public CI finished; reviewed artifacts; named external blockers |
| Contracts and provisioning | Selected composition foundations and clean installation | Supported-platform installation and genuine provider workflows; explicit refusals for unavailable capabilities |
| Usable alpha | Three operator journeys | New operator completes run, inspection, comparison, correction and fresh replay |
| One application pilot | Polymer/thermal investigation, with two reference workflows | Agreed held-out measurement criteria and a reproducible retained investigation |
| Hardened pilot | Packaging, recovery, limits and compatibility policy | Demonstrated recovery and upgrade/compatibility behavior with operator documentation |

The pilot selection is provisional. Measured validation must be specified
before making application claims. Broad multiphysics, autonomous research,
private-provider qualification, and commissioned equipment control remain
separate workstreams.

Continue from [Run NET](RUN_NET.md), [operator readiness](OPERATOR_READINESS.md),
[monorepo qualification](MONOREPO.md), and
[integration coverage](INTEGRATION_COVERAGE.md). The existing runtime manifests,
provider pins and profile-specific qualification requirements remain the
sources of truth for execution.
