# GeniusNew Migration Matrix — Phase 0

Status: evidence inventory only. No source runtime code, secrets, credentials, data, workflow, database change, deployment change, or production change is migrated by this document.

## Evidence binding

- Target repository: `Kaancodm/GeniusNew`
- Target starting SHA: `284f361ba6d0ac3ca2c838cfe556050f3b9a6db4`
- Target starting tree: `71aa1b54aaad720bccfe5a789db9e7df469213e0`
- Source repository: `Kaancodm/Agent-Genius`
- Source default branch: `main`
- Source SHA: `b0c7ce136160a4ba818eee028b7980c952848b5a`
- Source tree: `3ad2ce2aec78eb4d65d1a5783910d4f036a39bff`
- Source recursive tree response: complete (`truncated=false`).
- Target recursive tree response: complete (`truncated=false`).
- Formal `EXECUTION_ENVIRONMENT`: UNKNOWN
- Formal `ACCESS_LEVEL`: UNKNOWN
- Formal `KAAN_APPROVAL_CHANNEL`: UNKNOWN
- Observed write capability: dedicated GeniusNew branch creation and documentation write only in this phase.

The older `docs/IMPORT-MANIFEST.md` remains historical evidence. It is bound to source SHA `09496c94c064ae36ee98b1a21553c7b3b358e864`, not the current source SHA above, and therefore does not satisfy this Phase 0 inventory by itself.

## Mandatory safety interpretation

1. `Agent-Genius` is technical source material only.
2. No Agent-Common identity/data is transferable.
3. `ACCEPT` is intentionally unused in Phase 0: no source security-critical implementation has yet been demonstrated, in the GeniusNew target boundary, with current exact-head target tests and required independent review.
4. `REBUILD` means useful behavior/security semantics may be reconstructed GeniusNew-native; it does not authorize copying.
5. `REJECT` means the identified source artifact/candidate must not be migrated directly.
6. No secret-bearing source content was copied into this matrix.
7. Source-main tree inspection found no `.gitmodules`, no tree entry with mode `120000`, and no tree entry of type `commit`. Any later PR/branch extraction must re-check file modes independently before use.
8. A repository-wide exact-string search for `Agent-Common` returned `incomplete_results=true`; completeness of that content search is therefore UNKNOWN. Known Agent-Common lineage is nevertheless directly observed in Source PR #1 and is rejected as a mergeable source block.

## Current GeniusNew overlap

| Target item | Exact head | Observed state | Migration consequence |
|---|---|---|---|
| `main` contract core | `284f361ba6d0ac3ca2c838cfe556050f3b9a6db4` | `geniusnew/contracts.py`, `schemas/handoff-v1.schema.json`, contract tests already present | Source Handoff/Policy semantics must be compared, not copied over this implementation. |
| PR #3 database | `c8a011268726df0e04e7ae768eddf5761a9c14ea` | open draft database foundation | Reconcile with Source PR #45 before any Phase 4 database work. |
| PR #5 CI | `76b8b7103d52ff5dc39cbc38e92f78b0233ad3bc` | open; `Verify contracts` run observed failed | Do not treat target CI as trusted/green. Phase 1 must rebuild the full trust root. |
| PR #6 approval | `b35fa768aa129e5137d6d3e53c0ffca073c65f12` | open; process-local ApprovalStore by design | Reconcile before Phase 3; persistence/gateway boundary remains missing. |

## Migration matrix

`STATUS=CLASSIFIED` means the Phase 0 decision is complete; it does not mean implementation or acceptance is complete.

| Component | SOURCE | SHA | DESTINATION | Decision | TESTS / evidence to preserve | SECURITY BOUNDARY | STATUS |
|---|---|---|---|---|---|---|---|
| Staat | `staat/**` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE; map semantics into GeniusNew-native modules | REBUILD | Existing policy/orchestrator/schema tests are requirements evidence; exact current-source full-suite result UNKNOWN | Legacy namespace/trust metaphor must not become target authority | CLASSIFIED |
| Polizei | `polizei/**` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE; later security/runtime modules | REBUILD | gateway, audit-chain, forensics, monitor tests exist | Independent enforcement/verification must remain server-owned | CLASSIFIED |
| Land | `land/**` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE; later worker/transport modules | REBUILD | worker, runtime, MVP E2E tests exist | Worker/transport cannot become an authority source | CLASSIFIED |
| Aktenzeichen / Handoff | `staat/verfassung/handoff.schema.json`; `staat/verfassung/models/handoff.py` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | existing `geniusnew/contracts.py`; `schemas/handoff-v1.schema.json` | REBUILD | `tests/test_handoff.py`, identity/TTL regressions | Exact subject/job/worker/policy/TTL binding; client cannot author privileged identity | CLASSIFIED; TARGET OVERLAP |
| Verfassung / Contracts | `staat/verfassung/*.schema.json`; `staat/verfassung/models/**` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | `schemas/**`; `geniusnew/**` | REBUILD | governed-execution, job-result, audit-log, schema tests | Runtime/schema equivalence, strict parsing, fail closed | CLASSIFIED |
| Portal | `bürgerbüro/portal/**`; `web/portal/**` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE until Phase 10 | REBUILD | portal/auth and Vercel-entrypoint tests | Browser is untrusted; security fields remain server-owned | CLASSIFIED |
| Auth | `bürgerbüro/portal/auth.py` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE until Phase 10 | REBUILD | `tests/test_portal_auth.py` | Authentication subject must be mapped server-side to Principal | CLASSIFIED |
| Identity / Principal | `staat/verfassung/models/principal.py`; `docs/identity-team-beta.md`; `tests/test_identity_quota.py` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | target contract/identity layer, exact new path deferred | REBUILD | identity/quota tests; current target contract tests | Principal derived by trusted server context, not caller payload | CLASSIFIED |
| Teams | `docs/identity-team-beta.md`; identity/quota implementation/tests | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE until Phase 2 contract consolidation | REBUILD | team read/create and scope tests as requirements | team_id optional only when explicitly authorized; tenant/team binding | CLASSIFIED |
| Quotas / Concurrency | `bürgerbüro/portal/quota.py`; `bürgerbüro/portal/service.py`; `tests/test_identity_quota.py` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE until Phase 2/runtime integration | REBUILD | quota/concurrency regressions | atomic server-side admission; restart/process assumptions cannot be inherited | CLASSIFIED |
| TTL | `docs/execution/ttl-contract-repair-20260910.md`; runtime paths; TTL/deadline tests | `b0c7ce136160a4ba818eee028b7980c952848b5a` | target contract/runtime checkpoints | REBUILD | `tests/test_ttl_contract_regressions.py`; `tests/test_execution_deadline.py` | admission, pre-dispatch, worker revalidation, result acceptance | CLASSIFIED |
| Orchestrator | `staat/regierung/orchestrator.py` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE until Phase 6 | REBUILD | `tests/test_orchestrator.py`; MVP E2E | no shared mutable authority; deterministic routing; fail closed | CLASSIFIED |
| Grenzschutz | `polizei/grenzschutz/gateway.py` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE until Phase 6 | REBUILD | `tests/test_gateway.py` | independent default-deny enforcement before dispatch | CLASSIFIED |
| WorkerTransport | `land/transport.py` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE until Phase 6/7 | REBUILD | worker/MVP/identity concurrency tests | serialized dispatch, bound worker, no PENDING/DENIED/expired dispatch | CLASSIFIED |
| WorkerMonitor | `polizei/interne_ermittlung/monitor.py` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE until Phase 6/7 | REBUILD | `tests/test_monitor.py` | worker cannot self-validate; independent result/TTL/integrity enforcement | CLASSIFIED |
| Policies | `staat/gesetze/approval-policy.json`; `staat/gesetze/policy.py`; `bürgerbüro/portal/execution_policy.py` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | target policy layer, exact path deferred | REBUILD | `tests/test_policy.py`; `tests/test_execution_policy.py` | default deny; policy-version binding; allowlists | CLASSIFIED |
| Approval | `bürgerbüro/portal/execution_approval.py`; approval docs/tests | `b0c7ce136160a4ba818eee028b7980c952848b5a` | reconcile with target PR #6 | REBUILD | `tests/test_execution_approval.py`; target PR #6 tests | durable one-time approval, scope/replay/expiry, gateway revalidation | CLASSIFIED; TARGET PR #6 OVERLAP |
| Execution Resolution | `bürgerbüro/portal/execution_resolution.py`; `docs/execution/trusted-resolution-20260914.md` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE until Phase 6 | REBUILD | `tests/test_trusted_execution_resolution.py` | resolved worker/tool/sandbox choices must be server-owned | CLASSIFIED |
| AuditChain | `polizei/forensik/audit_chain.py`; audit schema/model | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE until Phase 5 | REBUILD | `tests/test_audit_chain.py`; `tests/test_audit_log.py` | append-only hash chain, actor/Principal binding, payload minimization | CLASSIFIED |
| Forensik | `polizei/forensik/anchor.py`; `polizei/forensik/verify.py` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE until Phase 5 | REBUILD | `tests/test_forensik_anchor.py`; `tests/test_forensik_verify.py` | deterministic evidence, tamper detection, exact artifact binding | CLASSIFIED |
| Registry | Source PR #1 `einwohnermeldeamt/database/registry.py` | `c406080656eb9bec0cfafb2aa331733638b72457` | NONE; re-specify if needed by identity/runtime | REBUILD | `tests/test_registry.py` on research branch | authoritative ownership/identity source; PR block itself is Agent-Common-lineage and rejected | CLASSIFIED |
| Semantic Memory | Source PR #46 `nationalbibliothek/memory/{engine,models,promotion,scoring,store}.py` | `6e0c7ffada1d9e774717b4b9f21b1fc676dd7bc5` | NONE until Phase 9 | REBUILD | `tests/test_semantic_memory.py`; exact-head full repository CI is not established | Principal/tenant/user/team binding; M0 quarantine; trusted R4 promotion; reviewer != promoter | CLASSIFIED |
| GitHub Adapter | `bürgerbüro/portal/github_adapter.py` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | future generic ToolAdapter + GitHub implementation | REBUILD | `tests/test_github_adapter.py`; `tests/test_github_adapter_pr_modes.py` | read-before-write, expected HEAD, path/type validation, least privilege | CLASSIFIED |
| Sandbox | `industriegebiet/runtime.py`; `industriegebiet/sandboxes/{profiles.py,profiles.json}` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE until Phase 7 | REBUILD | runtime/profile tests | arbitrary execution disabled; explicit provider flags; resource/network limits | CLASSIFIED |
| gVisor | Source PR #1 `industriegebiet/sandboxes/runsc.py` | `c406080656eb9bec0cfafb2aa331733638b72457` | NONE; experimental provider only | REBUILD | `tests/test_runsc_sandbox.py` exists on research branch; real-host evidence UNKNOWN | hardened Linux host validation required before trust | CLASSIFIED |
| Firecracker | `industriegebiet/firecracker_runtime.py` plus Source PR #1 `industriegebiet/sandboxes/firecracker.py` | main `b0c7ce136160a4ba818eee028b7980c952848b5a`; PR `c406080656eb9bec0cfafb2aa331733638b72457` | NONE; experimental provider only | REBUILD | runtime/provisioner tests; source status records KVM skip historically; current real-host evidence UNKNOWN | KVM/Jailer host proof required; no production trust | CLASSIFIED |
| CI | `.github/workflows/{verify,container,dependency-audit,gitguardian,semgrep}.yml`; `.github/dependabot.yml` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | `.github/workflows/**` in Phase 1 | REBUILD | source workflow structure exists; current exact-source CI result not assumed | exact SHA binding, required/optional semantics, least permissions | CLASSIFIED |
| Semgrep | `.github/workflows/semgrep.yml`; `.semgrep/agent-genius.yml`; Semgrep validation scripts | `b0c7ce136160a4ba818eee028b7980c952848b5a` | target `.semgrep/**` + workflow in Phase 1 | REBUILD | Semgrep fixture/verifier tests | rules must be GeniusNew-native and scanner failure semantics explicit | CLASSIFIED |
| GitGuardian / Secret Scanning | `.github/workflows/gitguardian.yml` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | target CI in Phase 1 | REBUILD | current source workflow present; external scanner availability/config must be observed | never treat unavailable external scanner as a successful core gate | CLASSIFIED |
| dependency audit | `.github/workflows/dependency-audit.yml`; `pyproject.toml` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | target CI/dependency config in Phase 1 | REBUILD | pip/dependency audit semantics | pinned/controlled dependencies; external failure semantics explicit | CLASSIFIED |
| container verification | `.github/workflows/container.yml`; `Dockerfile`; `.dockerignore` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | target container/CI in Phase 1/11 | REBUILD | container smoke/security workflow exists | non-root/runtime surface, reproducible build, no secret injection | CLASSIFIED |
| Vercel configuration | `web/portal/vercel.json`; Source PR #43 portal instrumentation | main `b0c7ce136160a4ba818eee028b7980c952848b5a`; PR `d6d145d33bfcdac08dc6389ddcd1b2cf51a3b71b` | NONE until Phase 10/11 | REBUILD | `tests/test_vercel_entrypoint.py`; PR #43 explicitly leaves external activation unverified | CSP/security headers and deployment settings must be revalidated on target | CLASSIFIED |
| HANDOVER | `HANDOVER.md` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE; future GeniusNew HANDOVER must be regenerated | REJECT | Historical evidence only | contains stale historical state/lineage; must not become target authority | CLASSIFIED |
| PROJECT_STATUS | `PROJECT_STATUS.md` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE; future GeniusNew status must be regenerated | REJECT | Historical evidence only | document itself says parts are historical; includes Agent-Common branch lineage | CLASSIFIED |
| tests | `tests/**` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | `tests/**`, rewritten against GeniusNew interfaces | REBUILD | complete source-main test tree inventoried; exact current-source run result UNKNOWN | preserve negative security invariants without legacy fixtures/identity assumptions | CLASSIFIED |
| schemas | `staat/verfassung/*.schema.json`; `scripts/validate_schemas.py` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | `schemas/**`, rewritten/compared | REBUILD | schema validation/tests present; target Handoff schema already exists | schema/runtime identity must match exactly | CLASSIFIED |
| Database placeholder on main | `einwohnermeldeamt/database/.gitkeep` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE | REJECT | no implementation to migrate | placeholder is not persistence evidence | CLASSIFIED |
| Database hardening candidate | Source PR #45 `database/**`; `.github/workflows/database.yml` | `6ebd5fbdce782a43dc0c7f45daf3bbcdf6abf5c9` | reconcile with target PR #3 in Phase 4 | REBUILD | PR describes PostgreSQL/RLS tests, but GitHub Actions is explicitly blocked on exact candidate | tenant isolation, RLS/server-only choice, non-owner NOBYPASSRLS runtime, migrations-as-code | CLASSIFIED; TARGET PR #3 OVERLAP |
| Execution/security evidence docs | `docs/execution/**`; `docs/security-toolchain-v1.md`; `docs/identity-team-beta.md` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | future GeniusNew ADR/evidence docs, regenerated | REBUILD | evidence/requirements source only | historical claims never substitute current exact-SHA evidence | CLASSIFIED |
| Root legacy guidance | `README.md`; `AGENTS.md` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE | REJECT | historical/project-source guidance only | Agent-Genius identity and old operating assumptions must not become GeniusNew authority | CLASSIFIED |

## Open source PR reconciliation

Every open Source PR observed during this inventory is explicitly classified below. An open PR is not trusted merely because it exists.

| PR | Exact head | Classification | Reason / overlap |
|---|---|---|---|
| #46 `feat(memory): principal-bound semantic memory v1` | `6e0c7ffada1d9e774717b4b9f21b1fc676dd7bc5` | REBUILD | Useful Principal/M0/R4 semantics; draft; full repository CI remains blocked and persistence/integration are absent. |
| #45 `feat(database): version complete GeniusNew schema and default-deny access` | `6ebd5fbdce782a43dc0c7f45daf3bbcdf6abf5c9` | REBUILD | Valuable default-deny/RLS/database-test semantics; overlaps target PR #3; exact-head GitHub Actions blocked. |
| #44 `feat(portal): add GeniusNew release dashboard` | `892a317d2b4b5d5008a423f424cc01c76837fd5b` | REBUILD | Static UX/status concepts useful, but snapshot is bound to Agent-Genius/source state and cannot be target truth. |
| #43 `feat(portal): prepare Vercel Speed Insights` | `d6d145d33bfcdac08dc6389ddcd1b2cf51a3b71b` | REBUILD | CSP/same-origin concepts useful; external Vercel activation explicitly unverified and target portal is not yet built. |
| #32 `feat: add canonical identity and memory scope contracts` | `f37907dfcc43f5c697c30a001e5449077801b1e0` | REJECT | Old base and overlapping identity work now present on source main; do not import a second identity contract. Relevant invariants are captured under Identity/Principal and Semantic Memory. |
| #27 `bump GitGuardian/ggshield 1.52.2 -> 1.54.0` | `58dad5d39cc749900977fac6671d42caea864b0f` | REJECT | Dependency bump is not target architecture; current target scanner version/pinning must be selected and verified in Phase 1. |
| #26 `bump actions/setup-python 5 -> 7` | `432df7ed62fe7da390bb4da364d1d4c33588101e` | REJECT | Dependency bump from stale source base; Phase 1 must select/pin target action versions independently. |
| #25 `bump actions/checkout 4 -> 7` | `051c7052c0dbce79cdc21ce25dfe6b85eca96f68` | REJECT | Dependency bump from stale source base; Phase 1 must select/pin target action versions independently. |
| #10 `feat: add experience-ranked memory core` | `38ef2fa10330b21d1c10927779fd71fcc285e3fb` | REJECT | Superseded for migration purposes by the newer principal-bound PR #46; independent security-review gate remained open. |
| #1 `research: extract Phase 2 sandbox/forensics components onto canonical main` | `c406080656eb9bec0cfafb2aa331733638b72457` | REJECT | Direct block reuse prohibited: base branch is `claude/agent-common-phase-1-1gpnqj`, branch intentionally marked research-only, and it overlaps newer main implementations. Registry/forensics/gVisor/Firecracker semantics are individually classified REBUILD above. |

## Completeness reconciliation

The complete current Source `main` tree was inspected by recursive Git tree enumeration and reconciled into these groups:

- repository/security configuration: `.github/**`, `.semgrep/**`, Docker files, `pyproject.toml`;
- portal/API: `bürgerbüro/portal/**`, `web/portal/**`;
- execution/security docs: `docs/**`, root HANDOVER/PROJECT_STATUS/README/AGENTS;
- execution/sandbox: `industriegebiet/**`, `land/**`;
- security enforcement/forensics: `polizei/**`;
- policy/orchestration/contracts: `staat/**`;
- current database/memory placeholders: `einwohnermeldeamt/**`, `nationalbibliothek/**`;
- scripts and all `tests/**`;
- all observed open Source PRs listed above.

No relevant component discovered in those source categories remains without one of `ACCEPT`, `REBUILD`, or `REJECT`. There are no `ACCEPT` decisions in Phase 0.

## Phase 0 boundary and gate basis

This matrix is the only Phase 0 change. It deliberately performs no source-code transfer. The classification gate is therefore evaluated on inventory/classification completeness, not on later implementation readiness.

Security-relevant unknowns that would block later acceptance remain explicit rather than being interpreted as success, including exact current-source test status for most main components, real-host sandbox evidence, external Vercel settings, formal Kaan release-approval channel, and complete content-string search coverage. Because no source implementation is accepted or migrated in Phase 0, these unknowns do not create an unclassified component; they become mandatory re-verification inputs for their later phases.
