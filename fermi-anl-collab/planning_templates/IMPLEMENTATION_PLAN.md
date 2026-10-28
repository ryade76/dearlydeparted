# Dearly Departed — Implementation plan

> TEMPLATE: complete this using the actual repository. Replace guidance and placeholders with findings. Do not imply that this template is a completed code assessment.

## 1. Recommendation

State the smallest implementation approach that completes A, B, and C. Name the first executable slice, the highest-risk gap, and whether the plan has any genuine blockers.

## 2. Verified baseline

- Repository root, branch/commit if available, relevant working-tree state, and repository instructions.
- Actual entry point, runtime, framework, persistence, retrieval/generation path, and demo limitations.
- Evidence inspected: exact paths and symbols.
- Checks actually executed: command, environment, result, and limitation.
- Things not verified, including external services or credentials.

## 3. Required outcomes and guardrails

Reference D01–D15 and S01–S12. Record any proposed change explicitly. Keep the complete three-increment outcome, current policy, temporal provenance, and restore semantics.

## 4. Minimal implementation design

Explain only decisions needed to execute the plan:

- Existing components to reuse or extend and why.
- Minimum data/provenance/identity changes, including event, creation, exposure, ingestion, and revision meaning.
- Current authorization enforcement before evidence affects graph traversal, retrieval, summaries, context, and generation.
- Casket contents, publication, protected key authority, independent restore, and update recovery.
- Meeting context, virtual identity, saved records, and current-access reevaluation.
- Required interface changes and any migration/backward-compatibility steps.
- Why a new component or replacement is necessary, if proposed.

Do not present multiple incompatible architectures without a main recommendation.

## 5. Sequenced delivery slices

A — Explain an inherited decision from a restorable archive: S01–S05.

B — Explain how the decision evolved: S06–S08.

C — Use recovered reasoning in a meeting: S09–S12.

For each actual planned slice, complete:

### Slice [identifier] — [user outcome]

- **Stories / acceptance IDs:**
- **Observed starting point and source evidence:**
- **Proposed change and components/interfaces affected:**
- **Tasks beneath this outcome:**
- **Prerequisites / dependencies:**
- **Failure and recovery behavior:**
- **Verification method and expected result:**
- **Demonstration available when complete:**
- **Remaining uncertainty / blocking point:**

Name parser, storage, graph, policy, and UI tasks under the outcome they serve. Split S07 if needed while keeping both state inspection and transition explanation.

## 6. Acceptance and regression plan

Map all scenario IDs through STORY_CODE_MAP.md. Identify what can be verified now, what checks must be added, what requires a real integration, and what requires domain review. Prioritize meaningful authorization, temporal, identity, revision, and recovery regressions.

Explain how the oracle is kept out of the retrieval corpus, how raw source variants are imported, and how generated answers are evaluated by claims and evidence.

## 7. Environment and operational profile

Specify:

- Authoritative LDAP view, group/org mapping, propagation assumptions, 60-second observation-freshness enforcement, and an isolated development substitute.
- S3 endpoint, encryption/key-management integration, minimal required service permissions, and missing integration dependencies.
- Actual model/runtime configuration inspected; any proposed change and its cost/dependency implications.
- Supported input limits and how oversized/unsupported items become visible gaps.
- Reference environment, archive-worker memory limit, scale corpus bytes and item count, largest item, measurement method, and provisional latency targets derived from the baseline.
- Restore/rebuild dependencies, retry behavior, suspension, and publication failure handling.

Do not embed credentials, invent measured throughput, or treat a mocked integration as complete.

## 8. Pilot and completion

Describe the demonstrated path from original decision through deferral and revision to a present-day next check. Include current permissions and recovered-old-evidence behavior.

State the remaining integration gates and the evidence needed to declare the full prototype complete. A/B/C are outcomes, not sprint-duration promises.

## 9. Deferred work

Confirm the package's exclusions. Explain any proposed exception by the exact acceptance outcome it enables.

## 10. Risks, decisions, and next step

Link DECISIONS_AND_UNCERTAINTIES.md. Summarize only material risks and real blockers. End with the first concrete implementation action and its completion evidence.

