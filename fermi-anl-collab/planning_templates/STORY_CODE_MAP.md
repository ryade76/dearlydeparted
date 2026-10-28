# Dearly Departed — Story-to-code map

> TEMPLATE: replace every not-assessed marker with source evidence and an explicit status. This file is not a report on the demo.

## Capability map

Observed states: implemented, partial, mocked, not found in inspected scope, unverified. Planned treatments: reuse, extend, replace, add, investigate.

Cite paths and symbols and identify the level of verification. Presence of a library, UI label, or date field is insufficient evidence of the user outcome.

| Story | Observed state | Source evidence / check performed | Gap | Planned treatment and slice | Acceptance IDs |
| --- | --- | --- | --- | --- | --- |
| S01 — Open the right door | Not assessed | Complete from repository. | Identify bounded gap. | Select treatment and link slice. | S01-AC01, S01-AC02, S01-AC03, P01, P02, P03, P04, P05, P06, P07, A02, R03 |
| S02 — Ask Alex why from the email record | Not assessed | Complete from repository. | Identify bounded gap. | Select treatment and link slice. | S02-AC01, S02-AC02, S02-AC03, S02-AC04, P01, P03, P06, P07, A01, A03, J01 |
| S03 — Bring the design document into the explanation | Not assessed | Complete from repository. | Identify bounded gap. | Select treatment and link slice. | S03-AC01, S03-AC02, A01 |
| S04 — Recover what was said in the meeting | Not assessed | Complete from repository. | Identify bounded gap. | Select treatment and link slice. | S04-AC01, S04-AC02, T02 |
| S05 — Seal it and bring it back | Not assessed | Complete from repository. | Identify bounded gap. | Select treatment and link slice. | S05-AC01, S05-AC02, S05-AC03, P07, A02, R01, R02 |
| S06 — Ask from the time it happened | Not assessed | Complete from repository. | Identify bounded gap. | Select treatment and link slice. | S06-AC01, S06-AC02, S06-AC03, T01, T02, T03, A03, J01 |
| S07 — Follow the decision as it changes | Not assessed | Complete from repository. | Identify bounded gap. | Select treatment and link slice. | S07-AC01, S07-AC02, S07-AC03, P03, T03 |
| S08 — Recover the missing chapter | Not assessed | Complete from repository. | Identify bounded gap. | Select treatment and link slice. | S08-AC01, S08-AC02, S08-AC03, P07, T01, R01, R02, R03 |
| S09 — Bring Alex into the room | Not assessed | Complete from repository. | Identify bounded gap. | Select treatment and link slice. | S09-AC01, S09-AC02, S09-AC03, P03, P04, A03, M01, J01 |
| S10 — Give the virtual colleague a seat | Not assessed | Complete from repository. | Identify bounded gap. | Select treatment and link slice. | S10-AC01, S10-AC02, M01 |
| S11 — Test yesterday’s reasoning against today’s problem | Not assessed | Complete from repository. | Identify bounded gap. | Select treatment and link slice. | S11-AC01, S11-AC02 |
| S12 — Leave with the decision trail | Not assessed | Complete from repository. | Identify bounded gap. | Select treatment and link slice. | S12-AC01, S12-AC02, S12-AC03, P04, M02, R02, R03 |

## Acceptance coverage

For every ID in acceptance/scenarios.json, provide a row below. A shared check may cover multiple stories, but no ID may disappear.

| Acceptance ID | Planned validation method | Code/test location or proposed location | Fixture / controlled mutation | Executed result or not yet run | Owning delivery slice |
| --- | --- | --- | --- | --- | --- |
| Complete all IDs. | State observable check. | Distinguish existing from proposed. | Name exact inputs. | Do not invent results. | Link the user outcome. |

## Evidence limits

List inspection scope, mocks, unavailable services, missing credentials, and unexercised paths. Describe a capability as not found in the inspected scope rather than asserting repository-wide absence without a suitable search.

## Traceability review

Confirm twelve stories, all scenario IDs, three complete outcomes, and the shared Definition of Done. Record any split such as S07a/S07b while preserving the parent outcome and scenario mapping.
