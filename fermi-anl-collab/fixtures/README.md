# Synthetic acceptance fixture

All people, messages, organizations, projects, dates, claims, and monetary amounts in this folder are fictional. No employee data is included.

## Use this fixture

`records.json` is the acceptance oracle and metadata manifest. It is an illustrative fixture contract, **not a required production schema or a new import format**. The actual sample inputs are in `sources/`: EML messages, two text-extractable PDFs, and one speaker-labeled UTF-8 transcript.

The code-access agent should map these inputs and expectations to the demo's real import paths, document unsupported cases, and plan the smallest changes. Do not make the application read acceptance-oracle claims as if they were source evidence. Sidecar dates and exposure relationships represent curated fixture truth; a working importer needs a documented way to preserve or establish these facts.

## Import each revision deliberately

- **r1 / archive-alex-001**: import only its listed `source_ids`. This includes E1, E2_EMAIL, E2_DESIGN, E3, E4, E5, C1, C2, U1, X1, and J1. Exclude R1, F1, and O1, even though all files are present in the package. Publish at the fictional June 20 timestamp.
- **r2 / archive-alex-001**: retain r1 evidence and add R1 and F1. Publish July 10. R1 was written and known in February, recovered July 9. F1 was written and received in July while describing February. Only the first can improve the employee's documented February perspective.
- **b1 / archive-blair-002**: import only O1. It tests a separate employee's evidence and must not enter Alex's archive or imply Alex's awareness.

The r1 expected-source inventory records R1 as a known omission. It never exposes R1's contents. Future F1 is not a known omission from a June archive. Unenumerated omissions remain unknown; this fixture does not claim complete mailbox coverage.

## Stable IDs and source occurrences

The backlog's E2 is split into **E2_EMAIL** (adoption and launch deadline) and **E2_DESIGN** (nightly reconciliation). E2_EMAIL's body deliberately omits the upstream-delivery constraint and the reconciliation requirement. Its PDF attachment is byte-identical to the standalone E2_DESIGN PDF. An email-only story may preserve the attachment without using its content; the file story adds that content to answers.

E2_DESIGN is a single canonical source with two import occurrences. Keep its email-parent relation and standalone occurrence, but do not count them as independent corroboration. SHA-256 and byte counts in `records.json` apply to the supplied files; its attachment digest identifies the same PDF bytes.

## Boundary records

| ID | Purpose |
| --- | --- |
| C1 and C2 | Conflicting February 11 accounts of who approved the February 10 decision; the fixture supplies no resolution. |
| U1 | Undated, unvalidated forecast with unknown Alex exposure; cannot establish earlier awareness. |
| X1 | Restricted amount and canary marker; only explicitly authorized readers may use the content. Employee receipt does not grant a present-day reader access. |
| F1 | July retrospective about February; cannot be treated as contemporaneous February knowledge. |
| J1 | Untrusted source instruction tries to change policy and cutoff; it must never control either. |
| O1 | Blair's unrelated equipment statement; not Alex testimony and not in Alex's revision lists. |

Source classifications are `atlas` for ordinary fixture evidence and `restricted` for X1. They label expected constraints, not production group names or a complete policy engine. The handoff's policy decisions specify actual authorization behavior. O1 is also ordinary Atlas evidence but belongs to a different employee archive.

## Source-level and segment-level temporal eligibility

E4 starts at 14:00 UTC and ends at 14:07 UTC. Its statements occur at 14:02, 14:04, 14:05, and 14:06. The manifest records **whole-source exposure at 14:07**, when all its recorded statements have occurred. A historical cutoff during the meeting must not admit the entire transcript merely because Alex attended its start. An implementation with segment-specific provenance may admit an individually evidenced earlier segment while continuing to exclude later segments. This is a permissible refinement of the conservative whole-source boundary.

## What has and has not been checked

The files are syntactically generated sample inputs. The PDFs were rendered and visually checked, their text extracted, email attachments compared against the standalone PDF, and manifest paths, dates, hashes, and revision membership checked. No demo code was provided to this fixture author, so no application import, retrieval, authorization, meeting, or temporal test has been run.

This tiny corpus supports functional examples. It is **not a large-data or performance benchmark**; the handoff separately requires a representative scale profile and archive-worker memory check.
