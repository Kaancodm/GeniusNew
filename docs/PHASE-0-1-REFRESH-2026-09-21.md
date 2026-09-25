# Phase 0/1 refresh — 2026-09-21

Status: evidence refresh only. No merge, deployment, production change, secret migration, or source-code import is authorized by this document.

## Exact evidence binding

- Target repository: `Kaancodm/GeniusNew`
- Target default branch: `main`
- Target HEAD: `d47f8a621ef57472acdc97e54488ef75547c2c16`
- Source repository: `Kaancodm/Agent-Genius` (read-only source)
- Source default branch: `main`
- Source HEAD: `2909bf0098ed295f6ea2ba9d040262f63d38f998`
- Historical source baseline: `09496c94c064ae36ee98b1a21553c7b3b358e864`
- Previous Phase-0 matrix source SHA: `b0c7ce136160a4ba818eee028b7980c952848b5a`

The source is 69 commits ahead of the historical baseline. Relative to the source SHA used by the existing migration matrix, current source `main` is only one commit ahead.

## Target Phase 0 status

Current `main` already contains GeniusNew-native contract, approval, audit, gateway, key-separation, worker, result, and process-isolation modules.

The exact `main` HEAD has a completed successful GitHub Actions run for `.github/workflows/verify.yml`:
- run `35376015934`
- conclusion: `success`
- event: `push`
- head: `d47f8a621ef57472acdc97e54488ef75547c2c16`

Six pull requests are currently open against `main`:

| PR | Head | Scope |
|---|---|---|
| #20 | `c88cb97d5ba780316c990897805a9bfe8bf79d6d` | demo script |
| #21 | `719869b36d4b02190daa88af1856de24dc25493b` | orchestrator |
| #22 | `dbc66cf0fcbf52790a4e5f98099238547382ef8e` | result verifier |
| #23 | `52f6e6f41224086b40709459c0319daaebb8032d` | TTL control points |
| #24 | `005afbeca9d78805127b7d88150cf509b8587b3d` | HTTP entrance |
| #25 | `431fb26213b0a139cd7b5420b90dc2a43a86e5d5` | integration/wiring |

Comparison evidence shows PR #25 is ahead of each PR #20–#24 head with none of those heads ahead of #25. Treat #25 as the integration candidate rather than an independent sixth implementation.

PR #25 exact head has a successful GitHub Actions run:
- run `35600107406`
- workflow: `Verify contracts`
- event: `pull_request`
- conclusion: `success`
- head: `431fb26213b0a139cd7b5420b90dc2a43a86e5d5`

No submitted PR reviews or inline review threads were observed on #25 during this refresh. Therefore independent review remains an open gate even though CI is green.

## Source Phase 1 delta

The current source differs from the previous Phase-0 inventory SHA `b0c7ce136160a4ba818eee028b7980c952848b5a` by exactly one commit and two added files:

- `docs/superpowers/specs/2026-09-20-drill-sergeants-design.md`
- `docs/superpowers/plans/2026-09-20-drill-sergeants-milestone-1.md`

No runtime source file, workflow, schema, policy, database file, portal file, or test file changed in that delta.

### Classification

| Source candidate | Decision | Reason |
|---|---|---|
| Drill Sergeants architecture design | REBUILD | Useful Zero-Trust requirements: external control, fail-closed action brokerage, independent verification, quarantine, capability reduction, hash-chained evidence. It is authored in the legacy Agent-Genius namespace and defines a separate subsystem/trust model, so it must not become GeniusNew authority by direct copy. |
| Drill Sergeants Milestone-1 implementation plan | REBUILD | Useful contract/state/policy test ideas. File paths and implementation assumptions target the legacy `staat/` / `polizei/` structure and therefore must be translated to GeniusNew-native interfaces. |

Direct `ACCEPT` remains inappropriate for these new source artifacts. They are requirements evidence only.

## Phase 0/1 gate result

- Target exact HEAD established: PASS
- Open PR inventory: PASS
- Target main CI at exact HEAD: PASS
- Integration PR #25 CI at exact head: PASS
- Source exact HEAD established: PASS
- Source delta since existing migration inventory identified: PASS
- New source candidates classified: PASS
- Independent security review for integration PR #25: OPEN
- Merge authorization: NOT GRANTED BY THIS DOCUMENT
- Deployment authorization: NOT GRANTED BY THIS DOCUMENT

## Next gate

Before any merge of #25:
1. independent security review of #25 at exact head `431fb26213b0a139cd7b5420b90dc2a43a86e5d5`;
2. verify that review remains valid if the head changes;
3. re-run exact-head CI after any review-driven modification;
4. merge only after explicit Kaan authorization.

After the v0.1 integration gate, continue the migration matrix in the established order and translate approved legacy semantics into GeniusNew-native contracts rather than importing legacy namespaces or trust assumptions.
