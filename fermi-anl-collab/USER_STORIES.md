# Dearly Departed — User stories

> Agreed agile backlog, exported 2026-10-28. One temporal acceptance sentence is clarified below to distinguish computation time from the time of its evidence (D05). Source: Dearly Departed Agile Prototype Backlog (private planning page). The package is self-contained; access to that page is not required. `DECISIONS.md` resolves the proposed defaults and boundaries in this snapshot. Acceptance IDs and machine-readable cards are in `stories.json` and `acceptance/scenarios.json`.

Dearly Departed lets an authorized successor ask why an inherited decision was made, follow how it changed over time, and use that reasoning with human and virtual colleagues.

The product experience is: **“Bring Alex into the room. Show me what made sense then, what changed, and why we ended up here.”** Historical perspective and decision evolution are core prototype outcomes.

The seven journeys remain the product map. The twelve cards below are proposed development slices within that scope. Developers must assess their size against the actual GraphRAG baseline before selecting them for an iteration; no story points or sprint commitments are assumed.

# How the documentation works

- **Card, Conversation, Confirmation:** a short outcome statement, a scope discussion, and observable completion examples. See [Agile Alliance on the Three Cs](https://agilealliance.org/glossary/three-cs/).

- **INVEST:** check independence, negotiability, value, estimability, size, and testability. Minimize dependencies and name the remaining ones. Deliver usable capabilities through the necessary layers; parser, graph, storage, and UI work become tasks beneath a story. See [Bill Wake on INVEST](https://xp123.com/invest-in-good-stories-and-smart-tasks/).

- **Given, When, Then:** describe starting conditions, a user action, and an observable result. The examples specify claims, evidence, and boundaries rather than exact generated wording. See [Agile Alliance on acceptance examples](https://agilealliance.org/glossary/given-when-then/).

- **Refinement and Definition of Done:** refine work until the team can size it and complete it within an iteration; use a shared quality standard across completed work. See the [Scrum Guide](https://scrumguides.org/scrum-guide.html#product-backlog).

Each card has one principal user outcome, scope, dependencies, and acceptance examples. Scope notes are the starting point for a conversation with the product owner, domain custodian, developers, and tester. Interface details remain negotiable within the agreed behavior.

# Story map and scope

| Existing journey | Development cards |
| --- | --- |
| Give the right people a seat | S01 |
| Pack the casket | S02, S03, S04 |
| Seal it and bring it back | S05 |
| Recover the missing chapter | S08 |
| Ask why we did it | S02, S03, S04 |
| Follow the decision as it changes | S06, S07 |
| Bring Alex into the room | S09, S10, S11, S12 |

Keep the Funeral Director, encrypted S3 Mortuary, and Reanimator. Use the shared Prepare, Access, and Meet views. The pilot has one primary employee archive, one project, one organizational hierarchy plus project groups, a central administrator, and one virtual employee. A small second archive tests identity and access separation.

Proposed import defaults are EML email exports, text-extractable PDF files, and speaker-labeled text transcripts. Confirm representative samples during refinement. Live connectors, OCR, transcription, delegated administration, policy simulation, dynamic rosters, multiple departed participants, voice, avatars, and external action execution remain deferred.

# Synthetic acceptance fixture

Use a fictional Atlas history with stable source IDs and dates. Source creation, employee exposure, decision date, and ingestion date remain distinct.

| Record | Date | Evidence and decision state |
| --- | --- | --- |
| E1 | January 17 | Alex proposes real-time processing. This is a proposal. |
| E2 | February 10 | The team adopts nightly batching to meet the launch deadline. An email establishes that reason; a design document adds a nightly reconciliation requirement. |
| E3 | April 3 | A partner event feed becomes available. This opens an option without changing the adopted decision. |
| E4 | April 12 | A meeting defers migration pending completeness and reconciliation validation. Nightly processing remains adopted. |
| E5 | June 15 | Successful validation supports adopting streaming while retaining nightly reconciliation. Adoption does not prove deployment. |
| R1 | Written February 8; recovered July | An email authored by Alex explains that the partner could supply only nightly files until April. It is missing from casket revision 1 and added to revision 2. |

Each E1–E5 source records its creation date and Alex’s documented relationship: authored, received, or attended. For this fixture, evidence of exposure exists by the listed date. Also include conflicting approval accounts, a source with uncertain date or exposure, an unrelated employee’s statement, and a restricted source. Unknown exposure cannot establish earlier awareness.

Revision 1 is published after June 15 with E1–E5, the attribution conflicts, boundary-test sources, and declared gaps, but without R1. Revision 2 is published in July with the same evidence plus R1. An expected-source inventory identifies R1 as a known omission; unenumerated coverage remains unknown.

**Critical distinction:** E2 to E5 is historical decision evolution. Adding R1 in July improves the reconstruction of February; it is not a July change of mind.

# Increment A Explain an inherited decision from a restorable archive

Outcome: a successor can ask why Atlas runs overnight, verify the answer across the supported sources, and repeat the task after cold restoration.

S02–S04 use a completed, encrypted intake revision with accepted coverage. S05 adds the durable Mortuary round trip. Consultation is permission-controlled from its first working slice.

## S01 Open the right door

**As an administrator, I want organizational and project policies to grant Collect or Consult access, so authorized people can use the appropriate employee knowledge.**

**Scope:** LDAP supplies current identities, groups, and available organizational attributes. The shared UI applies subtree, project, and evidence policies and explains an individual access decision. **Depends on:** no other card; use seeded test identities and archive metadata.

- **Given** a Network-subtree grant, **when** I check a descendant and sibling department, **then** the descendant is allowed and the sibling is denied unless another applicable grant exists.

- **Given** an Atlas project grant and a restricted source, **when** I check access, **then** the project grant reaches eligible Atlas evidence and cannot override the restriction.

- **Given** Collect without Consult, **when** the operator requests consultation, **then** access is denied with an appropriate rule explanation and no protected content.

## S02 Ask Alex why from the email record

**As a successor, I want to ask about a decision preserved in approved email, so I can recover its documented rationale.**

**Scope:** one declared email import format including attachment capture, one employee, question and answer, and source passage access. **Depends on:** S01.

- **Given** an authorized operator imports the E1–E2 emails, **when** an authorized successor asks why nightly processing was adopted, **then** the answer explains the recorded deadline and opens its supporting passage.

- **Given** R1 is missing, **when** the user asks why real-time delivery was unavailable, **then** that specific reason remains unknown rather than being invented.

- **Given** a known failed item, **when** the operator retries it, **then** its status is visible and previously accepted evidence is not duplicated.

- **Given** email attachments, **when** they are imported, **then** originals and parent-email relationships are retained and unprocessed or failed items remain visible as coverage gaps. S03 adds supported attachment content to answers.

## S03 Bring the design document into the explanation

**As a successor, I want design files considered alongside email, so I can recover constraints documented elsewhere.**

**Scope:** one declared file format; no OCR or universal document browser. **Depends on:** S02.

- **Given** an approved standalone or attached design document adds the reconciliation requirement absent from email, **when** the user asks which constraints mattered, **then** the answer incorporates it and opens the document passage.

- **Given** only a later file revision is available, **when** the system displays or cites it, **then** it retains its actual revision and date and does not invent an earlier version.

## S04 Recover what was said in the meeting

**As a successor, I want meeting evidence attributed to its speakers, so I can distinguish recommendations, disagreement, and adopted decisions.**

**Scope:** one speaker-labeled transcript format; no transcription service. **Depends on:** S02; S03 can be delivered independently.

- **Given** the E4 transcript, **when** the user asks what the team agreed, **then** the answer identifies the deferral, its recorded reason, and the relevant speaker-attributed segment.

- **Given** one attendee proposes migration and others defer it, **when** the decision is summarized, **then** the proposal remains distinct from adoption and attendance does not become endorsement.

## S05 Seal it and bring it back

**As a custodian, I want the employee archive restored from the encrypted Mortuary, so consultation survives loss of the intake session and departed account.**

**Scope:** publish and restore one identifiable casket revision; no storage console. **Depends on:** S02; this increment’s final demonstration also includes S03 and S04.

- **Given** a published encrypted S3 casket, **when** temporary intake state is removed and the departed account is disabled, **then** an authorized user can restore it, repeat the known question, and open its preserved evidence.

- **Given** corruption or unavailable decryption authority, **when** restoration is attempted, **then** the revision never becomes ready and a recoverable failure is shown.

- **Given** unresolved intake gaps, **when** publication is requested, **then** partial coverage requires explicit acceptance and remains visible.

# Increment B Explain how the decision evolved

Outcome: a successor can explain the progression from proposed streaming to adopted batching, through a deferred migration, to the later revised decision, with evidence for the transitions.

## S06 Ask from the time it happened

**As a successor, I want a historical cutoff, so I can understand the employee’s documented perspective without hindsight.**

**Scope:** an as-of date, a retrospective mode, and fresh context when switching. **Depends on:** S02.

- **Given** E1–E5, **when** the user asks from February 10, **then** the answer identifies the adopted nightly approach without using April or June knowledge; the date and casket revision are visible.

- **Given** the conversation already discussed June, **when** the user returns to February, **then** a fresh historical context still excludes that later knowledge, including summaries and relationships derived from later or otherwise ineligible evidence. A newly computed summary using only eligible inputs remains permitted.

- **Given** uncertain dates or a later-written retrospective, **when** an earlier perspective is requested, **then** the system does not assert unsupported earlier awareness.

## S07 Follow the decision as it changes

**As a successor, I want to follow and compare a decision’s stages, so I can understand what changed, why it changed, and which reasoning persisted.**

**Scope:** one decision trail with a two-date comparison. A chronological list or table is enough. **Depends on:** S03, S04, and S06.

- **Given** E1–E5, **when** I open the trail, **then** I see dated proposals, adopted positions, the April opportunity, the deferral, and the June revision, with documented participants and sources. Adoption is distinct from deployment.

- **Given** the February and June positions, **when** I compare them, **then** the explanation identifies the new feed, validation requirement, validation outcome, changed processing choice, and retained nightly reconciliation, citing each supported transition.

- **Given** conflicting approval accounts or missing causal evidence, **when** I inspect the affected stage, **then** the disagreement or missing explanation remains explicit. A later document does not automatically supersede an earlier decision.

**Sizing rule:** if the team cannot finish this card within an iteration, split it into **S07a inspect the dated decision states** and **S07b explain the transition between two states**. Each must work end to end. Keep both in the core prototype.

## S08 Recover the missing chapter

**As a custodian, I want to add missed evidence to a new casket revision, so explanations improve while earlier sessions retain an identifiable evidence basis.**

**Scope:** retrieve, add or reprocess selected material, and republish; one updater. **Depends on:** S05 and S06.

- **Given** revision 1 lacks R1, **when** R1 is recovered in July and revision 2 is published, **then** a new February answer can explain the original upstream-delivery constraint.

- **Given** that updated answer, **when** the user inspects the change, **then** it is identified as newly recovered evidence about February, not a July reversal or a changed decision date.

- **Given** an active revision-1 session or an interrupted update, **when** revision 2 is prepared, **then** the existing session stays pinned and the last published revision remains usable.

# Increment C Use recovered reasoning in a meeting

Outcome: human and virtual colleagues investigate the history, introduce a current requirement, and leave with an evidence-backed next check.

## S09 Bring Alex into the room

**As a host, I want a topic meeting with a fixed human roster and an employee representation, so colleagues can investigate the record together.**

**Scope:** text, one representation, fixed participants, pinned revision and perspective. **Depends on:** S01, S02, and S06.

- **Given** authorized participants, **when** the host starts an Atlas meeting, **then** everyone sees its topic, roster, revision, and cutoff and can ask follow-ups.

- **Given** a source available only to the manager, **when** the shared room asks a related question, **then** that source cannot contribute protected facts, citations, or indirect conclusions.

- **Given** a participant loses access, **when** the next affected output would be released after the change is recognized, **then** it is blocked and the meeting requires a newly authorized start.

## S10 Give the virtual colleague a seat

**As a host, I want a separately authorized virtual employee to participate, so it can contribute questions within its permitted scope.**

**Scope:** one configured virtual identity; no delegation chains or external actions. **Depends on:** S09.

- **Given** the virtual identity has explicit access, **when** the host includes it in a new meeting, **then** it initiates a relevant follow-up, receives a cited answer, and appears as the acting virtual participant.

- **Given** the virtual identity has less access than the host, **when** the room responds, **then** its restricted scope constrains shared output and the host’s authority does not elevate it.

## S11 Test yesterday’s reasoning against today’s problem

**As a participant, I want to supply a current requirement, so I can identify which historical assumptions still matter.**

**Scope:** attributed scenario facts and generated advice; no autonomous execution. **Depends on:** S09; demonstrate with S10.

- **Given** a participant supplies a new requirement, **when** the room evaluates the historical choice, **then** it separates recorded reasoning, the participant’s assertion, generated advice, and an assumption to verify.

- **Given** that contemporary discussion, **when** a fresh historical perspective is opened, **then** the new requirement does not become something the departed employee knew earlier.

## S12 Leave with the decision trail

**As a host, I want a saved brief and transcript, so the recovered understanding remains useful after the meeting.**

**Scope:** What we learned, How the decision evolved, What remains uncertain, and Next check. **Depends on:** S09; the full demonstration exercises S07, S10, and S11.

- **Given** a completed meeting, **when** I save its record, **then** it retains findings, decision transitions, uncertainty, next check, citations, roster, cutoff, and casket revision.

- **Given** changed access, **when** someone reopens the record, **then** current permissions apply and the whole record may be denied.

- **Given** the meeting is later ingested, **when** its statements are used, **then** their contemporary generated origin remains explicit rather than becoming original employee testimony.

# Shared Definition of Done

Acceptance examples describe a particular story’s outcome. This proposed Definition of Done describes the quality required across completed increments; it is separate from a project-specific readiness checklist. The [Scrum Guide describes Definition of Done](https://scrumguides.org/scrum-guide.html#commitment-definition-of-done) as the shared quality standard for an increment.

- The story works through its intended user flow with the necessary components integrated. Its agreed positive, boundary, and failure examples pass; consequential permission, temporal, and revision regressions are automated.

- Current authorization governs discovery, original evidence, derived facts, answers, citations, and saved records. Only permitted evidence may influence generated output. Unknown policy mappings deny use. Revocations stop further affected output after recognition; old dates, caskets, and cached context cannot restore authority.

- Historical claims retain source references and attribution. Known-at-time, event time, source creation, and ingestion remain distinct. Inference and uncertainty are labeled; receipt or attendance does not establish agreement, and copied content is not independent corroboration. Generated meetings cannot become original employee memories. Source instructions cannot change policy or historical boundaries.

- Sensitive persisted material remains encrypted, and archival identity survives directory removal or address reuse. Failed updates preserve the previous published revision. Operators can retry or suspend; withdrawing evidence keeps affected older outputs blocked until a clean permitted view is available.

- Relevant operations retain actor, result, revision, perspective, and policy references. Logs do not provide an alternative route to restricted content.

- The product owner or domain custodian can verify the demonstrated claims against the fixture. A casket or update story also passes the agreed cold-restore, integrity, interruption, and representative-size checks applicable to it.

Use this standard from the first increment. Dedicated monitoring and audit screens remain deferred.

# Refinement checklist and implementation focus

Before a card is selected, confirm:

1. One principal user outcome and a bounded scope.

2. Representative inputs and the expected claims, citations, unknowns, and prohibited facts.

3. Relevant policy mappings, temporal meaning, and failure behavior.

4. Dependencies and integration contracts known and sequenced.

5. The implementing team can size the work and expects to complete it within its iteration.

This is a lightweight team convention, not an additional mandatory Scrum artifact or an approval workflow. The [Scrum Guide assigns sizing to the developers](https://scrumguides.org/scrum-guide.html#product-backlog). Do not label these cards Sprint-ready or assign points before that assessment.

If a card is too large, split by source family, user scenario, or observable capability. Keep each result usable. For S07, separate state inspection from transition explanation if necessary; keep both as core scope. Technical tasks can cover extraction, policy enforcement, storage, integration, and UI within the selected story.

Use a focused, timeboxed investigation only when a specific uncertainty prevents sizing. The two likely candidates are whether the GraphRAG baseline preserves usable temporal/provenance boundaries and whether a casket can be restored independently at the intended scale. Each investigation must return a demonstrated result, the smallest identified gap, and a refined story. It is not a new platform workstream.

# Delivery order and pilot acceptance

A, B, and C are outcome increments, not promised sprint durations. Begin with S01 and S02; add S03 and S04 in either order, then complete the archive round trip. Deliver S06–S08 as the temporal core, followed by the meeting stories. The team selects its actual Sprint Goal and capacity.

Before implementation, fix the sample export formats, directory-policy freshness limit, and scale profile. Record corpus bytes, item count, largest supported item, reference environment, and acceptable task latency. Use an archive larger than the archive-processing worker’s memory to verify that whole-casket plaintext loading is unnecessary.

Pilot with two successors and a domain custodian. Each successor should explain the original decision, reconstruct its stages, identify why the position changed or was retained, verify the evidence, and name the next check for a current requirement.

Require correct proposal/adoption/deferral/revision distinctions, explicit unresolved gaps, no forbidden future or restricted facts, and correct behavior when old evidence is recovered. Measure task completion, verification time, and where users still leave the product to search manually.

The prototype is complete only after all three outcome increments and their shared conditions pass. Its signature result is: **“I can see why the decision made sense then, how the reasoning changed, what survived those changes, and what I should verify now.”**

