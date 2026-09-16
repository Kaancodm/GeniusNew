# GeniusNew Migration Matrix — Phase 0

Status: Phase 0 inventory and classification are complete; the gate is `PASS` for that scope only. This remains an evidence inventory only. No source runtime code, secrets, credentials, data, workflow, database change, deployment change, or production change is migrated by this document.

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
8. Repository-wide Agent-Common content-search completeness on the current Source `main` tree is now proven by direct Git blob enumeration at the exact SHA; see "Agent-Common completeness verification". The earlier GitHub Code Search response with `incomplete_results=true` is superseded, not reinterpreted, and was not used as evidence. Known Agent-Common lineage is directly observed in Source PR #1 and is rejected as a mergeable source block.

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
| Runtime entrypoint | `main.py` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE until Phase 10/11; create a GeniusNew-native entrypoint only if deployment requires it | REBUILD | `tests/test_vercel_entrypoint.py`; target import/smoke check must bind to the exact GeniusNew app | source file only re-exports the legacy portal app; old module path/namespace must not become target authority | CLASSIFIED |
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
| Unreachable approval-rule decision | `docs/policy-decision-0001-unreachable-approval-rules.md` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | target-native policy ADR/spec during Phase 2/3 | REBUILD | preserve invariant that concrete approval rules are a subset of `allowed_tools`; re-run target policy/schema/full-suite gates | least privilege: removing unreachable rules must not add executable tools; any privilege expansion requires a separate explicit security/product decision | CLASSIFIED |
| Approval | `bürgerbüro/portal/execution_approval.py`; approval docs/tests | `b0c7ce136160a4ba818eee028b7980c952848b5a` | reconcile with target PR #6 | REBUILD | `tests/test_execution_approval.py`; target PR #6 tests | durable one-time approval, scope/replay/expiry, gateway revalidation | CLASSIFIED; TARGET PR #6 OVERLAP |
| Execution Resolution | `bürgerbüro/portal/execution_resolution.py`; `docs/execution/trusted-resolution-20260914.md` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE until Phase 6 | REBUILD | `tests/test_trusted_execution_resolution.py` | resolved worker/tool/sandbox choices must be server-owned | CLASSIFIED |
| AuditChain | `polizei/forensik/audit_chain.py`; audit schema/model | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE until Phase 5 | REBUILD | `tests/test_audit_chain.py`; `tests/test_audit_log.py` | append-only hash chain, actor/Principal binding, payload minimization | CLASSIFIED |
| Forensik | `polizei/forensik/anchor.py`; `polizei/forensik/verify.py` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE until Phase 5 | REBUILD | `tests/test_forensik_anchor.py`; `tests/test_forensik_verify.py` | deterministic evidence, tamper detection, exact artifact binding | CLASSIFIED |
| Deterministic forensic bundle | Source PR #1 `polizei/forensik/deterministic_bundle.py` | `c406080656eb9bec0cfafb2aa331733638b72457` | NONE until Phase 5; re-specify GeniusNew-native | REBUILD | Source PR #1 `tests/test_deterministic_bundle.py`; preserve deterministic archive/hash invariants as requirements evidence only | PR #1 has Agent-Common lineage and is rejected as a mergeable block; no direct copy authorization; deterministic metadata normalization, unsafe-path/symlink handling and exact artifact binding must be revalidated in GeniusNew | CLASSIFIED |
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
| Environment template | `.env.example` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | target-native `.env.example` only after the GeniusNew configuration contract is defined | REBUILD | preserve placeholder-only/no-real-secret discipline and fail-closed behavior for missing required auth configuration; target secret/config scans required | legacy `AGENT_GENIUS_*` names, tier/key format, audit path, and Firecracker host paths are assumptions, not target authority; never copy real secrets | CLASSIFIED |
| Project-isolation WP01 | `docs/project-isolation-wp01.md` | `b0c7ce136160a4ba818eee028b7980c952848b5a` | NONE | REJECT | historical isolation evidence only | explicitly records Agent-Common lineage, legacy schema IDs/examples, uncommitted local evidence and incomplete runtime proof; must not become GeniusNew authority | CLASSIFIED |
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

- repository/security configuration: `.github/**`, `.semgrep/**`, Docker files, `pyproject.toml`, `.env.example`;
- root/runtime entrypoints: `main.py`;
- portal/API: `bürgerbüro/portal/**`, `web/portal/**`;
- execution/security docs: `docs/**`, including `docs/project-isolation-wp01.md` and `docs/policy-decision-0001-unreachable-approval-rules.md`, plus root HANDOVER/PROJECT_STATUS/README/AGENTS;
- execution/sandbox: `industriegebiet/**`, `land/**`;
- security enforcement/forensics: `polizei/**`;
- policy/orchestration/contracts: `staat/**`;
- current database/memory placeholders: `einwohnermeldeamt/**`, `nationalbibliothek/**`;
- scripts and all `tests/**`;
- all observed open Source PRs listed above.

All components identified by the complete Source `main` tree and the explicitly reviewed open-PR candidates are now assigned one of `ACCEPT`, `REBUILD`, or `REJECT`, including the previously omitted Source PR #1 deterministic forensic bundle and the four artifacts found by the independent read-only review: `main.py`, `.env.example`, `docs/project-isolation-wp01.md`, and `docs/policy-decision-0001-unreachable-approval-rules.md`. There are no `ACCEPT` decisions in Phase 0. Repository-wide `Agent-Common` content-string-search completeness is no longer `UNKNOWN`: all 129 files of the exact Source `main` tree were scanned from Git blobs and all 21 matches are classified in "Agent-Common completeness verification".

## Agent-Common completeness verification

This section closes the single remaining Phase 0 blocker: repository-wide completeness of the Agent-Common reference search on the current Source `main` tree.

### Evidence binding and method

- Source exact SHA: `b0c7ce136160a4ba818eee028b7980c952848b5a`, verified as both `refs/heads/main` and the repository `HEAD` symref of `Kaancodm/Agent-Genius`.
- Scan method: read-only clone; enumeration and content read directly from the Git objects of that exact commit (`git ls-tree -r <sha>`, `git grep -I -i -E <pattern> <sha>`). The working directory and `.git` internals were not searched.
- GitHub Code Search was **not** used as evidence. Its earlier `incomplete_results=true` response is superseded by direct blob enumeration; it is not reinterpreted as "no matches".
- Complete tree: **YES**. `git ls-tree -r` returned 129 entries in a single non-paginated, non-truncated listing, and every referenced blob object was verified present and readable (`git cat-file -e`: 0 missing objects).

### Scan coverage

| Measure | Value |
|---|---|
| Files total (tree entries) | 129 |
| Files textually scannable | 129 |
| Files binary / not scannable | 0 (no blob contains a NUL byte) |
| Symlinks (mode `120000`) | 0 |
| Submodules (type `commit` / mode `160000`) | 0 |
| `.gitmodules` | absent |
| `.gitattributes` | absent, therefore no clean/smudge filter could mask blob content |
| Unusual git modes | none; all 129 entries are mode `100644`, no `100755` |

### Search patterns

Applied case-insensitively to the blob content of every one of the 129 files:
`Agent-Common`, `agent-common`, `agent_common`, `agent common`, `agentcommon`, `common-phase`, `agent-common.dev`, plus the union regex `agent[-_. ]?common`.

`agentcommon` returned zero matches. The union regex returned **21 matching lines in 10 files**; every other pattern's result set is a subset of it.

### Match list and classification

| Path | Line | Match | Classification | Security relevance | Action |
|---|---|---|---|---|---|
| `PROJECT_STATUS.md` | 73 | default-branch claim naming `claude/agent-common-phase-1-1gpnqj` | HISTORICAL_ONLY | Stale repository-metadata claim; the Source default branch is in fact `main` at this SHA, so the statement is outdated | Do not carry over; regenerate target status natively |
| `PROJECT_STATUS.md` | 88 | heading `### claude/agent-common-phase-1-1gpnqj` | HISTORICAL_ONLY | Records legacy Phase-1 branch lineage only | Do not carry over |
| `PROJECT_STATUS.md` | 113 | `agent_common.egg-info/` hygiene rule | HISTORICAL_ONLY | Cleanup rule for generated metadata; no active dependency | Do not carry over |
| `README.md` | 32 | warning about the former distribution `agent-common` | HISTORICAL_ONLY | Environment-hygiene note; no runtime authority | Do not carry over |
| `docs/execution/AG-TOOLCHAIN-P0-STAGING-progress.md` | 6 | branch `Kaancodm/agent-common/p0-scanner-candidate-20260905` | HISTORICAL_ONLY | Worktree/branch provenance only | Do not carry over |
| `docs/execution/AG-TOOLCHAIN-P0-STAGING-progress.md` | 36 | claim that `pyproject.toml` still declares `agent-common` | HISTORICAL_ONLY | Factually outdated at this SHA: `pyproject.toml` declares `agent-genius`. Concrete proof that source documentation must not be read as current truth | Do not carry over; treat as historical claim only |
| `docs/project-isolation-wp01.md` | 11 | former distribution name `agent-common` | REJECT | Part of the document already classified REJECT; legacy identity record | Do not migrate |
| `docs/project-isolation-wp01.md` | 19 | legacy egress target `*.internal.agent-common.dev` | REJECT | Documents a removed legacy network target; restoring it would re-grant egress | Do not migrate; never reintroduce the domain |
| `docs/project-isolation-wp01.md` | 36 | note on the four schema `$id` values | REJECT | Records deliberately retained legacy contract identifiers | Do not migrate as authority |
| `docs/project-isolation-wp01.md` | 37 | note on the `agent-common/summarizer:1.0` fixture | REJECT | Records a retained test fixture | Do not migrate as authority |
| `docs/project-isolation-wp01.md` | 38 | note on branch name and `agent_common.egg-info` | REJECT | Historical provenance record | Do not migrate as authority |
| `docs/project-isolation-wp01.md` | 39 | note on `land/industriegebiet/` | REJECT | Records a compatibility bridge decision | Do not migrate as authority |
| `docs/project-isolation-wp01.md` | 41 | statement that no `agent_common` package import exists | REJECT | Historical assertion; independently re-verified below rather than trusted | Do not migrate as authority |
| `docs/project-isolation-wp01.md` | 43 | statement about an external Agent-Common checkout | REJECT | Historical assertion about runtime paths | Do not migrate as authority |
| `docs/security-toolchain-v1.md` | 3 | scope line "Agent Common is not part of this work" | HISTORICAL_ONLY | Scope statement of a past work package; no authority | Do not carry over |
| `staat/verfassung/audit_log.schema.json` | 3 | `"$id": "https://agent-common.dev/schemas/audit_log.schema.json"` | REJECT | Legacy identity namespace embedded in an active contract identifier | GeniusNew schemas must use a GeniusNew-native `$id`; never copy this value |
| `staat/verfassung/bug_report.schema.json` | 3 | `"$id": "https://agent-common.dev/schemas/bug_report.schema.json"` | REJECT | Legacy identity namespace embedded in an active contract identifier | GeniusNew-native `$id` required |
| `staat/verfassung/handoff.schema.json` | 3 | `"$id": "https://agent-common.dev/schemas/handoff.schema.json"` | REJECT | Legacy identity namespace embedded in an active contract identifier | GeniusNew-native `$id` required |
| `staat/verfassung/job_result.schema.json` | 3 | `"$id": "https://agent-common.dev/schemas/job_result.schema.json"` | REJECT | Legacy identity namespace embedded in an active contract identifier | GeniusNew-native `$id` required |
| `tests/test_sandbox_profiles.py` | 117 | `spec.docker_run_args("agent-common/summarizer:1.0", ...)` | TEST_FIXTURE_ONLY | Static image-name string used to assert Docker argument order; verified that the test neither pulls nor starts the image | Rebuild the test with a GeniusNew-native fixture name; do not copy the string |
| `tests/test_sandbox_profiles.py` | 120 | `assert args[-1] == "agent-common/summarizer:1.0"` | TEST_FIXTURE_ONLY | Same static fixture assertion | Rebuild with a GeniusNew-native fixture name |

No match occurred in a secret-bearing file. No secret value was read or reproduced. All 21 matches are classified; `UNCLASSIFIED_MATCHES: 0`.

### WP01 evidence re-verified against the exact current Source HEAD

`docs/project-isolation-wp01.md` is bound to the older base `6b8c9ee17e3c551e4541e6b7c86b48e087d39051`. Its claims were re-checked against `b0c7ce136160a4ba818eee028b7980c952848b5a` rather than accepted:

| WP01 claim | Result at current Source HEAD | Evidence |
|---|---|---|
| Four schema `$id` values under `agent-common.dev` | STILL_PRESENT | `staat/verfassung/{audit_log,bug_report,handoff,job_result}.schema.json` line 3 |
| Test fixture `agent-common/summarizer:1.0` | STILL_PRESENT | `tests/test_sandbox_profiles.py:117,120`; surrounding assertions only inspect the generated argument list |
| Historical branch/package provenance in `PROJECT_STATUS.md` | STILL_PRESENT | `PROJECT_STATUS.md:73,88,113` |
| `land/industriegebiet/` as a local compatibility bridge | STILL_PRESENT, claim CONFIRMED | `land/industriegebiet/{__init__.py,runtime.py}` import only the in-repository `industriegebiet` package; no external Agent-Common checkout is referenced |
| Distribution renamed `agent-common` -> `agent-genius` | CHANGED, claim CONFIRMED | `pyproject.toml:6` is `name = "agent-genius"` at this SHA (`agent-common` at the old base) |
| Legacy egress target `*.internal.agent-common.dev` removed from policy | REMOVED, claim CONFIRMED | `staat/gesetze/approval-policy.json` contains no `common` string; Team egress is `["api.anthropic.com"]`, Enterprise egress is `[]` |
| No `agent_common` package import anywhere | CONFIRMED independently | The union-regex scan over all 129 files produced no import statement; the only `agent_common` occurrences are the three documentation lines listed above |

This re-verification is independent evidence from the current tree. It does not grant `docs/project-isolation-wp01.md` any authority; that document remains classified REJECT.

### Change control since the earlier baseline

Base `6b8c9ee17e3c551e4541e6b7c86b48e087d39051` -> head `b0c7ce136160a4ba818eee028b7980c952848b5a`, 62 commits, 81 changed paths (54 added, 27 modified, 0 deleted).

- Matching lines at base: 12 in 8 files. Matching lines at head: 21 in 10 files.
- Files that gained matches since the baseline: `README.md`, `docs/project-isolation-wp01.md`, `docs/execution/AG-TOOLCHAIN-P0-STAGING-progress.md`, `docs/security-toolchain-v1.md`. All four are documentation. No added or modified executable, schema, workflow, container or configuration file introduced a new Agent-Common reference.
- Files that lost their matches since the baseline are exactly the two carriers with active effect: `pyproject.toml` (distribution identity) and `staat/gesetze/approval-policy.json` (egress allowlist).
- Consequence: the earlier WP01 isolation review may be used as supporting evidence, because every change made after its base has been checked for newly introduced Agent-Common references.

### Open-PR separation

The current Source `main` tree and the open Source PRs remain separate evidence spaces and are not merged into one judgement. The open-PR list was re-enumerated at this SHA and contains exactly the ten PRs already classified in "Open source PR reconciliation" (#46, #45, #44, #43, #32, #27, #26, #25, #10, #1), with unchanged head SHAs. Source PR #1 is confirmed to target base branch `claude/agent-common-phase-1-1gpnqj`; its `REJECT` classification as a directly mergeable block is unchanged. No open-PR tree was scanned as part of this section.

### Remaining UNKNOWNs

The completeness question of this section is resolved. The following remain `UNKNOWN` and are explicitly **not** converted into success by this section or by the gate below:

- Formal `EXECUTION_ENVIRONMENT`, `ACCESS_LEVEL` and `KAAN_APPROVAL_CHANNEL`.
- Current exact-source full test-suite and CI results; no source CI run is treated as green.
- Real-host evidence for the gVisor and Firecracker/KVM sandbox providers.
- Whether the retained legacy schema `$id` values are referenced by any consumer outside this repository.

These are runtime, environment and approval facts. They are outside the inventory-and-classification scope of the Phase 0 gate and continue to block later phases on their own terms.

## Phase 0 boundary and gate basis

This matrix is the only Phase 0 change. It deliberately performs no source-code transfer.

`GATE: PASS`

`GATE_SCOPE`: Phase-0 inventory and classification completeness only.

`GATE_REASON`: every component of the exact Source `main` tree `b0c7ce136160a4ba818eee028b7980c952848b5a` and every open Source PR is classified as `REBUILD`, `REJECT`, `HISTORICAL_ONLY` or `TEST_FIXTURE_ONLY`; there are no `ACCEPT` decisions. The last remaining blocker, repository-wide Agent-Common content-search completeness, is resolved by direct Git blob enumeration of all 129 files at that SHA with 0 unscannable files, 0 symlinks, 0 submodules and 0 unclassified matches. No Agent-Common dependency remains unclassified.

`PASS` means only that. It does **not** mean the source code is safe, that Phase 1 is released, that a merge is authorized, that a deployment is authorized, or that any production change is authorized. The `UNKNOWN` facts recorded above (`EXECUTION_ENVIRONMENT`, `ACCESS_LEVEL`, `KAAN_APPROVAL_CHANNEL`, current exact-source test/CI results, real-host sandbox evidence) are unchanged and are not converted into success by this gate.

`NEXT_SINGLE_STEP`: independent read-only review of this exact commit before merge or Phase 1.
