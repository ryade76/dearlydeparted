# Dearly Departed — Decisions and uncertainties

> TEMPLATE: use repository evidence to complete this register. Settled package decisions are not open questions. No entry below is a claim about the actual code.

## Adopted product decisions

Apply D01–D15 in DECISIONS.md. If a discovered limitation would force a user-visible change, write a specific proposal below and preserve the requirement in the plan until that change is authorized.

## Source-informed decisions

| ID | Question | Evidence inspected | Chosen approach | Alternatives rejected and why | Stories / acceptance IDs | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| TECH-01 | Where will eligibility be enforced before evidence affects generation? | Complete from source. | Select one approach. | Explain the minimal choice. | S01, S06, S07, S09 | State the consequential check. |

Add entries only where a decision affects execution or review. Do not produce an architecture-decision document for every routine code edit.

## Remaining environment dependencies

Resolve these from the environment where possible; otherwise state a bounded prerequisite.

| ID | Dependency | Affected work | Safe development assumption | Owner / responsible role | Evidence or action needed | Latest blocking point |
| --- | --- | --- | --- | --- | --- | --- |
| ENV-01 | Real LDAP schema, group/org mapping, and authoritative visibility | S01 and all protected operations | Seeded identities can validate behavior, not real integration. | Directory/service owner | Inspect configuration and run an authorized integration check. | Before claiming real LDAP-based prototype access works. |
| ENV-02 | S3 endpoint and decryption authority | S05, S08, saved records | Isolated S3-compatible test service if available and permitted. | Storage/application owner | Verify encryption, permissions, cold restore, and key failure. | Before claiming Mortuary acceptance. |
| ENV-03 | Reference hardware and representative archive profile | S05, S08 | Constrain the isolated worker and choose a larger corpus. | Implementing team | Record concrete profile and measurement method. | Before scale acceptance or throughput claims. |

Replace these rows with actual findings. Do not leave generic TBDs or ask the owner for values the repository already establishes.

## Genuine product conflicts, if any

For each conflict include:

- Requirement and exact observed limitation.
- Evidence, affected story/scenario IDs, and impact.
- Recommended resolution and one credible alternative.
- Effect on scope, access, cost, and reversibility.
- Work that can continue independently.
- The precise decision needed and the latest blocking point.

If there is no genuine conflict, say so. Do not manufacture an approval checkpoint.

## Bounded investigations, only if necessary

For each proposed or completed spike: question; evidence already inspected; why that evidence was insufficient; maximum effort; isolated experiment; result if executed; decision enabled; stopping condition.

Distinguish a planned experiment from a completed one. Reaching a timebox with no answer yields a specific blocker, not an invented conclusion.

