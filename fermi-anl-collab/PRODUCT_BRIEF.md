# Product brief

## The promise

**Dearly Departed preserves the reasoning behind inherited work.** An authorized successor can ask why a decision was made, see how it changed, inspect the evidence for those changes, and discuss its relevance to today's problem with colleagues.

The system reconstructs documented work knowledge and supported reasoning. It does not claim access to a former employee's private thoughts or turn generated dialogue into original testimony.

The signature experience is: **“Bring Alex into the room. Show me what made sense then, what changed, and why we ended up here.”**

## Users and responsibilities

| Actor | Main job |
| --- | --- |
| Successor or current staff member | Recover an explanation, verify it, follow a decision through time, and apply lessons to current work. |
| Meeting host | Start a topic discussion with a fixed roster and save a useful brief. |
| Virtual employee | Participate as a separately authorized identity within the room's permitted scope. |
| Custodian or intake operator | Import approved material, resolve visible failures, publish a casket, recover omissions, and restore the archive. |
| Central administrator | Apply organizational/project/evidence policies and explain an individual's effective access. |
| Domain custodian/product owner | Verify that the fixture's claims and the demonstrated user outcome are correct. |

A person may hold more than one role through explicit grants. Administrative authority and archive content access remain separate.

## Three logical modules

| Module | Responsibility |
| --- | --- |
| **Funeral Director** | Ingest approved email, files, and meeting transcripts; preserve originals, dates, attribution, and policy mappings; manage coverage and casket revisions. |
| **Mortuary** | Store encrypted, identifiable casket revisions in S3 and support verified restoration. A casket may span multiple objects. |
| **Reanimator** | Answer grounded questions and facilitate topic meetings using permitted evidence, an explicit revision, and a defined temporal perspective. |

These are logical responsibilities. They need not be separate services, repositories, databases, or deployments.

## Three views

**Prepare** shows the archive subject, imported sources, coverage, failures, candidate/published revision, and restore/update status.

**Access** applies Collect/Consult grants and restrictions using current LDAP identities, groups, and available organizational attributes. It explains an access outcome without revealing protected content.

**Meet** shows topic, participant roster, casket revision, perspective, citations, and the decision trail. One-to-one questions use the same underlying evidence and policy behavior.

Keep the experience direct: ask a question, inspect a passage, move to a date, compare two decision states, and save what the group learned. A list or table can communicate the trail; a graph visualization is unnecessary.

## Prototype boundaries

The pilot uses one primary employee archive, one project, one organizational hierarchy with cross-project membership, a central administrator, and one virtual employee. A small second archive validates identity and access separation.

Supported initial inputs are EML exports with attachment accounting, text-extractable PDF files, and speaker-labeled UTF-8 text transcripts. The package supplies synthetic examples. Original evidence and lineage remain available after the employee's LDAP account is disabled or deleted.

The prototype includes:

- A restorable encrypted archive and controlled reingestion.
- Current hierarchical/project/evidence policy across intake and consultation.
- Cited explanations of why a decision was made.
- Historical perspective without future knowledge.
- A decision trail and two-date explanation of changes and retained reasoning.
- Human and virtual participation within a common authorized evidence scope.
- A saved brief and transcript with current access checks.

Deferred work includes live connectors, OCR and speech recognition, bulk offboarding, delegated administration, policy simulation or a general policy language, dynamic rosters, multiple departed participants, full extraction-editing and revision-diff workbenches, voice/avatars, agent delegation, external actions, and comprehensive retention automation.

## Success at the pilot

Two successors independently explain the original Atlas decision, distinguish proposal from adoption, describe the deferral and later revision, identify what changed and what persisted, open supporting evidence, and name a concrete next check for a current requirement.

They must also recognize an unresolved approval conflict and a missing explanation. When old evidence is recovered, the product improves the reconstruction without inventing a later change of mind.

Measure task completion, time to find and verify an answer, and where users still leave the product to search manually. Do not substitute a persuasive demonstration for correct claims, permissions, temporal boundaries, and cold restoration.

