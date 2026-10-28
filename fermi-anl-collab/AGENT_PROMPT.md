# Assignment to the code-access planning agent

You are planning the next implementation of **Dearly Departed** using this handoff and the existing demo source code available in your workspace.

**Produce a concrete implementation plan. Do not implement product features during this task.** Inspect the actual demo first, preserve useful work, resolve technical uncertainty from evidence, and choose the smallest credible changes that deliver the agreed user outcomes.

## Read before planning

Read `README.md`, `PRODUCT_BRIEF.md`, `DECISIONS.md`, `USER_STORIES.md`, `stories.json`, and the acceptance and fixture READMEs. Inspect the supplied scenario and fixture data where relevant. Use the three files in `planning_templates/` as your output structure.

Treat the package as product requirements and synthetic examples. It is not a report on the code. “SOTA” means state of the art; it is not a product called Soya and is not proof that any particular GraphRAG capability already exists.

## Your authority

Resolve routine, reversible technical choices using the code, documented requirements, and the smallest suitable approach. Record the chosen option and why. You may split a story vertically while preserving its ID linkage and every acceptance outcome. S07 may become S07a (inspect decision states) and S07b (explain transitions); both remain core.

You may read the repository, inspect local configuration examples without disclosing secrets, run relevant existing local checks after checking their effects, and use temporary synthetic experiments when necessary. Keep experiments isolated from application source and live services. Record every executed check and its actual result.

Do not implement features, deploy, provision paid infrastructure, alter production data, change access, publish this package, or create external issues as part of planning. Do not silently weaken current permissions, temporal boundaries, provenance, encryption, or recovery to fit the demo.

If a genuine product decision changes scope, access, substantial cost, or an irreversible commitment, finish the unaffected planning and present a concrete recommendation with the exact tradeoff. Do not ask the owner to repeat decisions already settled in this package.

## Inspection sequence

1. **Establish the baseline.** Read applicable repository instructions. Identify the repository root, branch, commit if available, relevant uncommitted changes, toolchain, startup path, configuration requirements, and actual demo entry point. If source access is unavailable, say so; do not fabricate a code assessment.
2. **Trace the real flow.** Follow one input from intake through storage, extraction, graph/retrieval, generation, citation, and UI. Trace meeting and policy paths where present. Distinguish running behavior, static UI, fixtures, mocks, unused code, and documentation.
3. **Map S01–S12 to evidence.** Cite paths and symbols, and line ranges where useful. For an absence claim, state the scope searched. A dependency name or date column does not prove a capability works.
4. **Inspect the consequential boundaries first.** Determine where evidence becomes available to retrieval, graph traversal, shared summaries, model context, caches, citations, and saved records. Locate the point where current authorization and historical eligibility must apply before evidence can influence output.
5. **Check archive portability.** Identify exactly what is persisted, what must be included or reproducibly rebuilt, key access requirements, publication semantics, integrity checks, and how restore survives loss of temporary intake state and the departed account.
6. **Run useful existing checks.** Use representative synthetic data and isolated services where available. Record command, environment, result, and limits. A mocked check remains a mocked check. Do not claim an external LDAP or S3 integration was tested when it was not.
7. **Resolve choices and sequence delivery.** Prefer reuse or a bounded extension. Credit existing verified capabilities against their stories and sequence the remaining gaps. Select one main approach; do not leave competing architectures as an unresolved menu. Put component work underneath a story's observable outcome.
8. **Verify coverage.** Every story and acceptance ID must map to a planned implementation and verification path, or a named blocker with a concrete next step.

## Capability assessment

Record two separate dimensions:

- **Observed state:** implemented, partial, mocked, not found in inspected scope, or unverified.
- **Planned treatment:** reuse, extend, replace, add, or investigate.

An “implemented” label requires observed evidence at the claimed level. “Reuse” does not remove the need to verify the relevant acceptance conditions. A source-backed static assessment and an executed end-to-end check must be distinguishable.

## Resolve uncertainty without inventing facts

Classify each material question:

| Class | Required treatment |
| --- | --- |
| Settled product decision | Apply the package rule; do not reopen by default. |
| Source-verifiable fact | Inspect source and useful checks, then record evidence. |
| Reversible implementation choice | Select a compatible option and explain why alternatives are unnecessary. |
| Environment dependency | Specify the required contract, local substitute, owner, validation, and latest blocking point. |
| Genuine product conflict | Recommend one concrete resolution and state its consequences. |

Use a timeboxed spike only if a specific question prevents a credible plan. State the question, evidence already checked, maximum effort, isolated experiment, expected decision, and stopping condition. Likely candidates are temporal/policy filtering in the existing retrieval path and an independently restorable casket at the chosen scale. A spike is not a new platform workstream.

Do not guess story points, sprint commitments, runtime performance, credentials, library versions, API behavior, or existing file paths. Give a sizing range only if you have an explicit basis and confidence level; the implementing team owns final sizing.

## Required outputs

Write these three completed files in the repository's established planning location, or in a separate `planning/dearly_departed/` directory if no convention exists:

1. **IMPLEMENTATION_PLAN.md** — verified baseline; recommended minimal approach; delivery order A/B/C; story-linked tasks; data and interface changes; migration/compatibility; acceptance and failure checks; environment dependencies; pilot and deferred work.
2. **STORY_CODE_MAP.md** — S01–S12 and all acceptance IDs mapped to source evidence, observed state, gaps, treatment, implementation slice, and validation. Include cross-cutting cases without losing their story associations.
3. **DECISIONS_AND_UNCERTAINTIES.md** — resolved source-informed decisions, alternatives rejected, and bounded remaining dependencies. Each remaining issue needs affected stories, owner or responsible role, next action, safe development assumption, and latest blocking point.

Return a short executive summary naming the first executable slice, the highest-risk gap, the decisions you resolved, and any real blockers. Link the completed files.

## Definition of a complete plan

The plan is complete when another development agent can identify the first change, the relevant code areas, the acceptance evidence, and the next slice without redesigning the product. It must:

- Preserve the “why,” the decision's stages, the reason for each supported transition, and reasoning retained across changes.
- Explain eligibility before generation, including graph-derived content, caches, and conversation context.
- Separate historical time, exposure time, source creation, ingestion, and casket publication.
- Keep a session's evidence revision fixed while reevaluating current access.
- Include encrypted S3 publication, cold restore, controlled updates, LDAP-based hierarchical/project policy, and virtual-participant authority.
- Cover all acceptance IDs with a test or review method; identify tests still to be written and checks actually run.
- Name actual prerequisites without presenting unknowns as completed work.
- Keep all three increments and the twelve-story scope; justify any additional infrastructure by an otherwise unmet acceptance condition.

