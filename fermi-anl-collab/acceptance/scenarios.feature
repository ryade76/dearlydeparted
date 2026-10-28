# Generated from scenarios.json. Acceptance specification only; no step definitions or executed application results.
# Do not ingest this file or the oracle JSON as employee source evidence.
Feature: Dearly Departed prototype outcomes and boundaries

  # Verification: Use Sam (descendant) and Priya (sibling) from access_examples.json. Nolan is the separate cross-project example.
  @S01-AC01 @S01
  Scenario: S01-AC01 - Subtree inheritance without sibling access
    Given a Network-subtree grant
    When I check a descendant and sibling department
    Then the descendant is allowed and the sibling is denied unless another applicable grant exists.

  # Verification: Nolan may consult ordinary Alex/Atlas evidence but cannot use X1; Maya has the additional required restricted grant.
  @S01-AC02 @S01
  Scenario: S01-AC02 - Project grant respects evidence restriction
    Given an Atlas project grant and a restricted source
    When I check access
    Then the project grant reaches eligible Atlas evidence and cannot override the restriction.

  # Verification: Chris can perform authorized collection; the product denies question and source-passage access without Consult.
  @S01-AC03 @S01
  Scenario: S01-AC03 - Collect does not grant Consult
    Given Collect without Consult
    When the operator requests consultation
    Then access is denied with an appropriate rule explanation and no protected content.

  # Verification: Verify the launch deadline from E2_EMAIL's body. Reconciliation is usable only if actual PDF interpretation and its S03 evidence are available; it is not required to demonstrate the email slice. Credit working PDF support if the demo already has it.
  @S02-AC01 @S02
  Scenario: S02-AC01 - Email supports the recorded rationale
    Given an authorized operator imports the E1–E2 emails
    When an authorized successor asks why nightly processing was adopted
    Then the answer explains the recorded deadline and opens its supporting passage.

  # Verification: Use r1. R1 and the oracle claims about R1 must be absent from every retrieval/model path. Do not assert the specific nightly-only partner constraint.
  @S02-AC02 @S02
  Scenario: S02-AC02 - Missing upstream reason stays unknown
    Given R1 is missing
    When the user asks why real-time delivery was unavailable
    Then that specific reason remains unknown rather than being invented.

  # Verification: Inject a failure for a known selected item using the future implementation's supported test seam; preserve accepted item identity on retry.
  @S02-AC03 @S02
  Scenario: S02-AC03 - Retry preserves accepted evidence
    Given a known failed item
    When the operator retries it
    Then its status is visible and previously accepted evidence is not duplicated.

  # Verification: One preserved PDF attachment with a parent link; unprocessed content is visible as a gap until S03.
  @S02-AC04 @S02
  Scenario: S02-AC04 - Capture and account for attachments
    Given email attachments
    When they are imported
    Then originals and parent-email relationships are retained and unprocessed or failed items remain visible as coverage gaps. S03 adds supported attachment content to answers.

  # Verification: Require nightly reconciliation with a locator into E2_DESIGN; an email-only citation is insufficient for that constraint.
  @S03-AC01 @S03
  Scenario: S03-AC01 - Document adds the missing constraint
    Given an approved standalone or attached design document adds the reconciliation requirement absent from email
    When the user asks which constraints mattered
    Then the answer incorporates it and opens the document passage.

  # Verification: Use E5 as the only available version in this scenario; no fabricated February version or citation is permitted.
  @S03-AC02 @S03
  Scenario: S03-AC02 - Do not invent an earlier file revision
    Given only a later file revision is available
    When the system displays or cites it
    Then it retains its actual revision and date and does not invent an earlier version.

  # Verification: Require the validation-related deferral from E4 and the speaker/segment supporting it.
  @S04-AC01 @S04
  Scenario: S04-AC01 - Meeting evidence has speaker attribution
    Given the E4 transcript
    When the user asks what the team agreed
    Then the answer identifies the deferral, its recorded reason, and the relevant speaker-attributed segment.

  # Verification: Taylor's immediate-migration proposal stays a proposal; Alex's explicit statement can be attributed, but mere attendance cannot prove agreement.
  @S04-AC02 @S04
  Scenario: S04-AC02 - Proposal and attendance are not adoption or endorsement
    Given one attendee proposes migration and others defer it
    When the decision is summarized
    Then the proposal remains distinct from adoption and attendance does not become endorsement.

  # Verification: Restore original evidence plus usable derived state or a documented rebuild path; service key authority remains available.
  @S05-AC01 @S05
  Scenario: S05-AC01 - Cold restore survives departed-account disablement
    Given a published encrypted S3 casket
    When temporary intake state is removed and the departed account is disabled
    Then an authorized user can restore it, repeat the known question, and open its preserved evidence.

  # Verification: Test corruption and unavailable decryption authority separately. Neither path may expose a ready revision.
  @S05-AC02 @S05
  Scenario: S05-AC02 - Failed restore never becomes ready
    Given corruption or unavailable decryption authority
    When restoration is attempted
    Then the revision never becomes ready and a recoverable failure is shown.

  # Verification: Known omission R1 stays a visible gap without leaking its unavailable contents; unspecified completeness remains unknown.
  @S05-AC03 @S05
  Scenario: S05-AC03 - Partial publication is explicit
    Given unresolved intake gaps
    When publication is requested
    Then partial coverage requires explicit acceptance and remains visible.

  # Verification: At end of 2025-02-10 UTC in r1: nightly adopted for deadline, reconciliation if S03 is present; no April feed or June outcome.
  @S06-AC01 @S06
  Scenario: S06-AC01 - Historical answer excludes later knowledge
    Given E1–E5
    When the user asks from February 10
    Then the answer identifies the adopted nightly approach without using April or June knowledge; the date and casket revision are visible.

  # Verification: Inspect generation inputs as well as final text. Later-derived content with future dependencies must be excluded or recomputed.
  @S06-AC02 @S06
  Scenario: S06-AC02 - Rewind resets future-bearing context
    Given the conversation already discussed June
    When the user returns to February
    Then a fresh historical context still excludes that later knowledge, including summaries and relationships derived from later or otherwise ineligible evidence. A newly computed summary using only eligible inputs remains permitted.

  # Verification: Use U1 and F1. A historical answer cannot establish earlier awareness from unknown exposure or a July recollection.
  @S06-AC03 @S06
  Scenario: S06-AC03 - Uncertain time does not establish earlier awareness
    Given uncertain dates or a later-written retrospective
    When an earlier perspective is requested
    Then the system does not assert unsupported earlier awareness.

  # Verification: Use retrospective mode in r1 for the full trail. Require proposal, batching adoption, feed opportunity, deferral, streaming adoption, and retained reconciliation. Deployment remains unrecorded.
  @S07-AC01 @S07
  Scenario: S07-AC01 - Inspect distinct decision stages
    Given E1–E5, with casket r1 in retrospective mode
    When I open the trail
    Then I see dated proposals, adopted positions, the April opportunity, the deferral, and the June revision, with documented participants and sources. Adoption is distinct from deployment.

  # Verification: Use retrospective comparison of February 10 and June 15 in r1. Require E3 feed availability, E4 validation gate, and E5 completed validation with streaming adoption, plus the retained constraint.
  @S07-AC02 @S07
  Scenario: S07-AC02 - Explain the transition and retained reasoning
    Given the February and June positions, with casket r1 in retrospective mode
    When I compare them
    Then the explanation identifies the new feed, validation requirement, validation outcome, changed processing choice, and retained nightly reconciliation, citing each supported transition.

  # Verification: Use retrospective mode, or a historical cutoff at or after 2025-02-11T10:30:00Z. C1 and C2 are February 11 accounts of the February 10 decision; they cannot establish February 10 awareness. They disagree on approver. Do not choose the later account just because it is later. Missing causes stay unknown.
  @S07-AC03 @S07
  Scenario: S07-AC03 - Unresolved evidence stays unresolved
    Given conflicting approval accounts or missing causal evidence, with casket r1 in retrospective mode
    When I inspect the affected stage
    Then the disagreement or missing explanation remains explicit. A later document does not automatically supersede an earlier decision.

  # Verification: Use r2, with R1's February authorship. A current reconstruction may improve without importing F1's later recollection into February.
  @S08-AC01 @S08
  Scenario: S08-AC01 - Recovered evidence improves the February reconstruction
    Given revision 1 lacks R1
    When R1 is recovered in July and revision 2 is published
    Then a new February answer can explain the original upstream-delivery constraint.

  # Verification: Show source event/creation/exposure in February and recovery/publication in July as different facts.
  @S08-AC02 @S08
  Scenario: S08-AC02 - Recovery is not a July reversal
    Given that updated answer
    When the user inspects the change
    Then it is identified as newly recovered evidence about February, not a July reversal or a changed decision date.

  # Verification: Inject interruption before publication. Existing r1 sessions remain r1, but their authorization remains current.
  @S08-AC03 @S08
  Scenario: S08-AC03 - Update preserves pinned sessions and last good revision
    Given an active revision-1 session or an interrupted update
    When revision 2 is prepared
    Then the existing session stays pinned and the last published revision remains usable.

  # Verification: A fixed Maya/Sam roster consults the same topic and pinned revision/perspective. The representation is not the old LDAP account.
  @S09-AC01 @S09
  Scenario: S09-AC01 - Start a topic meeting with visible context
    Given authorized participants
    When the host starts an Atlas meeting
    Then everyone sees its topic, roster, revision, and cutoff and can ask follow-ups.

  # Verification: X1 is not an eligible input in a Maya/Sam room; neither its amount, canary, citations, nor dependent conclusions may appear.
  @S09-AC02 @S09
  Scenario: S09-AC02 - Shared output respects the least permitted evidence scope
    Given a source available only to the manager
    When the shared room asks a related question
    Then that source cannot contribute protected facts, citations, or indirect conclusions.

  # Verification: Reevaluate before output release. Stopping and restarting is acceptable; continuing with stale privileged context is not.
  @S09-AC03 @S09
  Scenario: S09-AC03 - Recognized access change stops affected output
    Given a participant loses access
    When the next affected output would be released after the change is recognized
    Then it is blocked and the meeting requires a newly authorized start.

  # Verification: A configured virtual principal initiates at least one relevant follow-up; a decorative agent label or host-authenticated proxy is insufficient.
  @S10-AC01 @S10
  Scenario: S10-AC01 - Virtual employee participates under its own identity
    Given the virtual identity has explicit access
    When the host includes it in a new meeting
    Then it initiates a relevant follow-up, receives a cited answer, and appears as the acting virtual participant.

  # Verification: Maya plus bot-atlas excludes X1 even though Maya alone can consult it.
  @S10-AC02 @S10
  Scenario: S10-AC02 - Virtual scope cannot inherit host authority
    Given the virtual identity has less access than the host
    When the room responds
    Then its restricted scope constrains shared output and the host’s authority does not elevate it.

  # Verification: Supply an explicitly fictional current latency requirement. Require an attributed assumption to verify, not a claim of known deployment.
  @S11-AC01 @S11
  Scenario: S11-AC01 - Separate historical reasoning from current scenario and advice
    Given a participant supplies a new requirement
    When the room evaluates the historical choice
    Then it separates recorded reasoning, the participant’s assertion, generated advice, and an assumption to verify.

  # Verification: Open a fresh historical context; do not retain the present-day requirement as Alex's historical belief.
  @S11-AC02 @S11
  Scenario: S11-AC02 - New requirement cannot become past knowledge
    Given that contemporary discussion
    When a fresh historical perspective is opened
    Then the new requirement does not become something the departed employee knew earlier.

  # Verification: Required brief headings and context appear; citations open preserved source versions under current access.
  @S12-AC01 @S12
  Scenario: S12-AC01 - Save a useful evidence-backed meeting record
    Given a completed meeting
    When I save its record
    Then it retains findings, decision transitions, uncertainty, next check, citations, roster, cutoff, and casket revision.

  # Verification: Whole-record denial is allowed and should be planned before selective redaction.
  @S12-AC02 @S12
  Scenario: S12-AC02 - Reopening uses current authority
    Given changed access
    When someone reopens the record
    Then current permissions apply and the whole record may be denied.

  # Verification: Contemporary generated answers and participant assertions remain distinguishable when later imported.
  @S12-AC03 @S12
  Scenario: S12-AC03 - Reingested meeting remains contemporary generated material
    Given the meeting is later ingested
    When its statements are used
    Then their contemporary generated origin remains explicit rather than becoming original employee testimony.

  # Verification: Exercise an isolated fixture variant with a missing mapping; do not remove the normal mapping from other tests.
  @P01 @S01 @S02
  Scenario: P01 - Unknown policy mapping denies use
    Given an imported item has no resolved source-to-policy mapping
    When an operator tries to make it available for consultation
    Then the item is withheld from retrieval and answers until the mapping is resolved
    And the operator can see the permitted failure metadata without exposing the item's protected content

  # Verification: Use the role and policy input examples. This does not forbid separately granting an administrator Consult.
  @P02 @S01
  Scenario: P02 - Administration is separate from archive consultation
    Given Ada has Administer but no Collect or Consult grant
    When Ada edits an allowed policy setting and then requests an archived passage
    Then policy administration succeeds within Ada's authority
    And the content request is denied without granting Consult implicitly

  # Verification: Inspect scoped retrieval/generation inputs as well as the answer. The query itself must not supply the secret amount or marker.
  @P03 @S01 @S02 @S07 @S09
  Scenario: P03 - Restrictions apply before evidence can influence generation
    Given Sam can consult the Alex archive but cannot consult X1 and the system has previously processed X1
    When Sam asks for the contingency reserve or a room containing Sam asks the same question
    Then the answer cannot reveal X1's amount or canary through a direct claim, hidden inference, citation, cache, graph relationship, or summary
    And the product reports that the available permitted record does not establish the requested fact

  # Verification: Use an injected clock and unavailable directory adapter. No wall-clock waiting is required.
  @P04 @S01 @S09 @S12
  Scenario: P04 - Expired authorization freshness fails closed
    Given the last successful directory/policy observation is more than 60 seconds old and refresh is unavailable
    When an affected answer, citation, or saved record would be released
    Then the affected operation is denied until freshness is reestablished
    And already recognized revocations block immediately even if the cached observation is younger than 60 seconds

  # Verification: Distinguish an explicit deny from a missing grant. Do not let an added project grant override it.
  @P05 @S01
  Scenario: P05 - Explicit deny defeats an otherwise applicable grant
    Given Taylor is in the Network subtree and also matches the suspended-consult denial
    When Taylor requests Consult on the Alex archive
    Then Consult is denied despite the subtree grant

  # Verification: For manual exports, a documented approved origin and mapping satisfy this prototype contract; live connectors are deferred.
  @P06 @S01 @S02
  Scenario: P06 - Collect does not bypass the source authorization boundary
    Given Chris has Collect but the export origin is unapproved or its custody/policy mapping is unresolved
    When Chris attempts to admit that export as consultable evidence
    Then the export is not made available to consultation until its approved origin and policy mapping are established

  # Verification: The plan must identify checkpoints that enforce observation freshness without relying only on the grant at job start. Directory visibility assumptions remain explicit.
  @P07 @S01 @S02 @S05 @S08
  Scenario: P07 - Collect revocation stops intake and publication
    Given Chris started an authorized intake or update job and then loses Collect authority
    When the job reaches its next bounded authorization checkpoint or attempts publication
    Then recognized revocation pauses or stops the affected work and prevents publication
    And a stale observation beyond the configured 60-second limit cannot authorize continued work
    And the previous published revision remains intact

  # Verification: The record's described event date must not be substituted for employee exposure.
  @T01 @S06 @S08
  Scenario: T01 - Late recollection differs from recovered contemporaneous knowledge
    Given r2 contains both February-authored R1 and July-authored F1
    When the user asks from February 10 and then opens a fresh retrospective perspective
    Then the historical answer may use R1 but must not present F1's documentation-turnover recollection as Alex's February knowledge
    And the retrospective answer may attribute the July recollection to Maya with its later origin explicit

  # Verification: Both approaches are acceptable. The plan must state which it uses; meeting attendance at the start cannot expose the whole transcript.
  @T02 @S04 @S06
  Scenario: T02 - Do not expose later statements within a meeting
    Given E4 has meeting-start event time 14:00 UTC and conservative whole-source exposure at 14:07 UTC
    When the user asks from 2025-04-12T14:03:00Z
    Then the answer does not use the later deferral or statements made after the cutoff
    And a conservative implementation excludes E4 entirely; a segment-aware implementation may use only individually evidenced statements by 14:03

  # Verification: Preserve uncertainty in the graph and citations as well as prose.
  @T03 @S06 @S07
  Scenario: T03 - Uncertain exposure cannot establish a historical belief
    Given U1's creation and Alex exposure are unknown
    When the user asks whether Alex knew its forecast on February 10
    Then the answer says that this awareness is not established
    And the system does not invent a precise date or treat the forecast as a validated outcome

  # Verification: Logical identity and origin both matter. Byte equality alone is not an authorization rule.
  @A01 @S02 @S03
  Scenario: A01 - Attachment and standalone copy retain one evidence lineage
    Given E2_EMAIL contains the same E2_DESIGN bytes also supplied as a standalone PDF
    When both import occurrences are accepted and a user verifies the design requirement
    Then the parent-email and standalone provenance remain available
    And the duplicate is not counted as two independent confirmations and its permissions are not broadened

  # Verification: Use distinct immutable principal identifiers. Do not use the departed account to authenticate the representation.
  @A02 @S01 @S05
  Scenario: A02 - Archive identity survives disablement deletion and email reuse
    Given the archive belongs to archive-alex-001 and the original LDAP account is disabled or removed
    When a different enabled principal later uses the old email address and requests the archive
    Then the archive retains the original employee attribution and remains usable by properly authorized successors
    And the new principal receives no access or historical authorship merely from the reused address

  # Verification: This variant isolates archive scoping from policy denial. Keep Sam-to-Blair denied in the baseline policy fixture. Neither a shared project label nor access to both archives merges employee identities or revision inventories.
  @A03 @S02 @S06 @S09
  Scenario: A03 - A second archive cannot become the first employee's memory
    Given O1 belongs only to archive-blair-002 and an isolated test variant grants Sam Consult on both archives while Sam explicitly selects archive-alex-001/r1
    When Sam asks what Alex selected for the unrelated equipment
    Then O1 is not retrieved into Alex's archive scope
    And the answer does not attribute Blair's equipment selection or knowledge to Alex

  # Verification: Use separate contexts; host permissions and previous agent memory cannot expand room scope.
  @M01 @S09 @S10
  Scenario: M01 - Virtual private context is not imported into a shared room
    Given the configured virtual employee previously saw X1 in an unrelated privileged context but has only ordinary Atlas access in this room
    When the virtual employee joins a fresh room and participates
    Then the shared context does not contain the prior restricted knowledge
    And all generated contributions and answers use only the room's permitted evidence and current attributed scenario inputs

  # Verification: Wider record sharing is deferred. Original roster members still need current Consult and evidence eligibility.
  @M02 @S12
  Scenario: M02 - Saved records stay within the original roster
    Given Nolan has current Consult access to ordinary Atlas evidence but was not on a saved meeting's roster
    When Nolan opens that saved record
    Then the record is denied even though Nolan may separately ask questions of evidence he is permitted to consult

  # Verification: The receiving agent plans or runs only an authorized isolated check. The supplied tiny corpus is not this benchmark; repeated bytes are not independent semantic evidence.
  @R01 @S05 @S08
  Scenario: R01 - Archive mechanics work beyond worker memory
    Given a declared scale corpus exceeds the archive-processing worker's configured memory limit
    When the corpus is ingested into a casket then cold-restored using the proposed implementation
    Then the complete plaintext casket is never required to fit in memory
    And integrity and item accounting are preserved and measured resource/elapsed-time results are reported against the declared profile

  # Verification: Use the actual deployment's encryption/key mechanism; do not invent cryptography. A mocked storage adapter cannot prove S3 encryption.
  @R02 @S05 @S08 @S12
  Scenario: R02 - Encryption and key failure are meaningful behavior
    Given sensitive sources derived artifacts and saved records are persisted through the intended storage path
    When a reviewer examines configured encryption behavior and attempts a restore without decryption authority
    Then the plan provides evidence of encryption at the relevant persisted boundaries rather than relying on an S3 library name
    And a restore lacking decryption authority fails without making the revision ready or exposing plaintext

  # Verification: Whole-casket suspension and whole-record denial are accepted lean responses; full purge automation is deferred.
  @R03 @S01 @S08 @S12
  Scenario: R03 - Withdrawal prevents reuse through old revisions and outputs
    Given evidence used by an earlier answer or record is withdrawn and the affected casket is suspended
    When a user requests that evidence through an old revision cached answer or saved record
    Then the affected output remains blocked until a clean permitted view is available
    And the product does not treat pinned evidence revisions as pinned permission

  # Verification: The application must not promote source content to system authority. J1 intentionally contains no restricted canary or amount.
  @J01 @S02 @S06 @S09
  Scenario: J01 - Source instructions cannot change the task or boundaries
    Given J1 contains a source instruction attempting to override policy and historical cutoff
    When the record is encountered during an authorized historical question
    Then the instruction is treated as source content and cannot alter permissions cutoff or the user's task
    And no future or restricted evidence becomes eligible as a result

