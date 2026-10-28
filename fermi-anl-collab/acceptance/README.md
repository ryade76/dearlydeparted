# Acceptance examples and evaluation

This folder specifies intended behavior. **None of these scenarios has been executed against the demo.** The JSON is the canonical scenario catalog; the feature file is a readable generated representation without step definitions.

## How to use the package

Use the raw files selected by `fixtures/records.json` as input. Use the oracle metadata to check expected identities, dates, exposure, and claims. It may supply curated metadata through a documented import mechanism; it must never be indexed wholesale as employee evidence.

Do not ingest `records.json` claims, this folder, story text, the answer expectations, or the complete package into the employee archive. Doing that would reveal missing evidence, restricted facts, future events, and expected answers and invalidate the examples.

The fixture is deliberately tiny. It can establish useful functional examples; it cannot establish throughput, corpus completeness, or production readiness.

## Traceability

Every story card has stable acceptance IDs such as `S07-AC02`. Shared boundary IDs begin with `P` (policy), `T` (time), `A` (archive/identity), `M` (meeting), `R` (restore/operational behavior), or `J` (untrusted source instructions).

`stories.json` maps both kinds back to S01–S12. A shared case may apply to multiple stories, but need not be duplicated as multiple independent tests. The receiving agent maps each ID to an appropriate local check, integration test, end-to-end demonstration, or domain review.

Some scenarios require controlled mutations: a failed import, unavailable directory, corrupted object, changed grant, or interrupted publication. These are recipes for isolated checks, not claims that the provided raw source file is already corrupted. Keep the baseline fixture intact.

## Evaluate meaning and evidence

Check four things:

1. **Supported claims:** does the answer correctly describe the recorded decision, constraints, and causal links?
2. **Evidence:** can the user open the source passage supporting each consequential claim, with the right version and attribution?
3. **Uncertainty:** does the product retain missing reasons, uncertain exposure, contradictory approval accounts, and an unrecorded deployment date?
4. **Boundaries:** was future, restricted, wrong-person, withdrawn, or generated material prevented from becoming an eligible historical input?

Do not grade only wording similarity. Generated phrasing may vary. Inspect retrieval and generation inputs for consequential authorization and temporal tests; a correct-looking final sentence alone cannot prove that ineligible evidence was excluded. A model-based grader may assist, but critical claims and exclusions need a deterministic check or a domain review against the sources.

## Main temporal demonstrations

| Context | Required interpretation | Must not be claimed |
| --- | --- | --- |
| r1, January 17 | Alex proposes streaming; proposal is supported. | Streaming was already adopted or deployed. |
| r1, February 10 | Team adopts nightly batching for the launch deadline; the design document establishes nightly reconciliation when PDF support is present. | April/June developments, F1's later recollection, or R1's specific nightly-only upstream constraint. |
| r1, April 13 | The feed is available, but migration was deferred pending validation; nightly remains adopted. | Feed availability itself changed the adopted decision. |
| r1, retrospective February-to-June comparison | Feed availability opens the option; validation is required and later passes; streaming is adopted with nightly reconciliation retained. | June adoption proves the system was deployed. |
| r2, February 10 | R1 strengthens the explanation with February-known partner constraints. | July recovery was a July change of mind; F1 was already known in February. |
| r2, retrospective explanation | Maya's July recollection may be attributed as later testimony. | Later testimony is contemporaneous February evidence. |

The fixture's C1 and C2 disagree about who formally approved the February decision. Inspect that conflict in retrospective mode or after both February 11 accounts became known; it is not evidence available in a February 10 historical perspective. The product must preserve the conflict. E4 demonstrates a deferral; Taylor's proposal and Alex's attendance are not substitutes for a recorded decision.

## Permission demonstrations

Use `access_examples.json` as fictional directory/policy inputs. It describes expected outcomes, not a mandatory policy engine or production LDAP schema.

Verify subtree and project grants, an explicit denial, source restrictions, distinct Collect/Consult/Administer capabilities, current authorization on historical revisions, and shared-room intersection. X1's marker and amount are restricted. Do not put either into the test user's query when checking for leakage.

A seeded local directory adapter can support development checks. It does not prove a real LDAP integration works. The completed plan must identify the real adapter/mapping and the integration check that remains.

## What completion means

The full prototype requires all three increments and the shared Definition of Done. The receiving agent should propose a meaningful test strategy, not automatically turn every sentence into a separate low-value test.

Cold restore, encryption, interruption, revocation, evidence withdrawal, and archive size checks need the relevant real or isolated integration path. UI screenshots, hard-coded answers, mocks, and the presence of an S3 library do not establish those outcomes.

During the pilot, two successors and a domain custodian verify the main temporal task. Record task completion and verification time, unresolved misconceptions, and manual searches outside the product. A restricted or future fact leaking is a failed boundary even if the overall answer sounds useful.

