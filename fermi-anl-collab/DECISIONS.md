# Resolved planning decisions

This file resolves planning-level ambiguity for package version 1.0. These are proposed prototype decisions adopted for the handoff, not claims about the existing demo or production service-level commitments. Requirements explicitly stated by the owner remain controlling.

**Fixed** means the required product outcome. **Resolved default** means the lean choice made here so the receiving agent can plan without another product-design round. Implementation details remain source-informed.

## D01 — Scope and structure

**Fixed:** preserve all twelve stories and the three outcome increments. Temporal decision evolution is core. “SOTA” means state of the art; do not assume an existing Soya product or a particular graph framework.

**Resolved default:** retain the demo's runtime, UI conventions, and working components where suitable. Funeral Director, Mortuary, and Reanimator are logical modules; separate services are unnecessary. Use Prepare, Access, and Meet.

## D02 — Input and coverage contract

**Resolved default:** support EML including MIME attachments, text-extractable PDF, and speaker-labeled UTF-8 text. Original files, parent attachments, source/version identity, and import results remain traceable.

Known missing, failed, unsupported, or unprocessed items are visible coverage gaps. An operator can accept a partial publication explicitly; the product never implies that an unknown corpus is complete. Capture attachments in S02; supported PDF attachment content participates in S03.

A retry must not duplicate an already accepted logical source. Identical bytes from distinct origins retain their separate provenance and access context; deduplication must not widen access or fabricate independent corroboration. Source-format samples are supplied; real export variants and supported size limits are verified against the code.

## D03 — Evidence, attribution, and decisions

**Fixed:** distinguish documented evidence, supported inference, and unresolved uncertainty. Cite the passage supporting a consequential historical claim and the evidence supporting a causal transition.

Proposal, adoption, approval, deferral, supersession, and deployment are distinct. Attendance, receipt, or access does not establish endorsement. A later document or a new opportunity does not automatically replace an adopted decision. Contradictory accounts remain visible.

**Resolved default:** present a chronological decision trail and a two-date comparison with: prior state, later state, changed conditions, recorded reason for the transition, retained constraints, sources, and unknowns. Do not require a universal decision-state ontology.

## D04 — Five different time meanings

Preserve these meanings even if the existing data model stores them differently:

1. **Event/effective time:** when an action, decision, or condition occurred.
2. **Source creation/version time:** when the available artifact was created or revised.
3. **Employee exposure:** evidence of when this employee authored, received, attended, or otherwise encountered the relevant information.
4. **Ingestion time:** when the system acquired the artifact.
5. **Publication/revision:** which accepted evidence set is being consulted.

A current permission check is separate from all five.

**Resolved default:** use explicit UTC timestamps in the fixture. In the product, display the selected timezone; a date-only cutoff means the end of that displayed calendar day. Preserve uncertain or date-only precision. If possible exposure spans the cutoff, do not assert definite earlier awareness. The agent must select one consistent representation and test boundary equality; no invented precision.

## D05 — Historical versus retrospective perspective

**Historical:** use evidence in the selected casket revision that supports the employee knowing the relevant information by the cutoff. Event date alone is insufficient. A later-written retrospective description of an earlier event is not proof of earlier employee awareness.

A plan or forecast about a future date can be part of an earlier perspective if it was known then. Preserve its status as a plan or forecast; do not turn it into knowledge that the future outcome actually occurred. The cutoff excludes later-acquired knowledge, not every reference to a future date.

**Retrospective:** allow later evidence within the selected revision to explain the history, with its later origin explicit. Later testimony may support a historical claim while remaining distinct from contemporaneous evidence.

A newly computed summary may explain old evidence if it is derived solely from eligible inputs. Its computation date is not a historical event. A previously computed summary or graph relationship that includes future or unauthorized inputs cannot be reused as-is.

**Resolved default:** changing perspective, cutoff, or evidence revision starts fresh generation context. The previous conversation remains a separate record. Rewinding after a discussion of June cannot retain June knowledge in the February context.

## D06 — Recovered evidence does not rewrite history

R1 is authored in February and recovered in July. Revision 2 can improve a February historical answer because authorship supports February exposure. The product identifies a newly recovered piece of February evidence; it does not create a July decision reversal.

F1, a later retrospective source, is a contrasting example: it can appear in retrospective explanation but cannot independently establish February awareness.

## D07 — Identity and policy inputs

**Fixed:** use current LDAP identity/group information and available organizational attributes as inputs to policy. Stable archive subject identity is independent of account lifecycle and email reuse. The departed account is not reactivated to consult the archive.

**Resolved default:** one central administrator applies explicit organizational-subtree, project, and evidence policies through the shared UI. Organizational placement or manager title alone grants nothing. Nested membership is evaluated only as supported by a declared directory-to-policy mapping; an unresolved mapping denies use.

Collect controls intake/update. Consult controls questions, original-evidence viewing, and saved records. Administer controls policy administration. These capabilities do not imply one another. An administrator has no automatic content-reading grant.

Collect also does not bypass authorization to obtain the original source. For imported exports, record the approved origin and policy mapping; the pilot need not implement live source-system connectors.

## D08 — Policy combination and derived material

A request needs an applicable positive grant for its action and resource scope. Applicable explicit denials override grants. Evidence restrictions are additional conditions; a general subtree/project grant cannot bypass them. Unknown mappings deny use.

Current policy governs discovery, source passages, graph expansion, summaries, generation, citations, caches, old casket revisions, and saved records. **Unauthorized evidence cannot influence an answer merely because its citation is hidden.**

The plan must preserve dependencies on underlying evidence. A derived object with ineligible dependencies is excluded or rebuilt from eligible inputs. Policy precedence is observable behavior; this package does not mandate a policy engine or expression language.

## D09 — Access freshness and revocation

**Resolved default for planning:** current directory/policy observations may be cached for at most **60 seconds**. Reevaluate effective authorization before releasing a generated output, opening a citation, or reading a saved record. Once a relevant change is recognized, block the next affected output immediately.

When freshness cannot be established after that limit, deny the affected operation. A local policy change is effective as soon as the application recognizes it. The 60-second limit bounds the age of the application's directory/policy observation. The plan must document the authoritative directory view and its visibility or propagation assumptions; observation freshness is not a guarantee about upstream replication. Recognized changes take effect immediately for the affected operation.

Apply the same freshness limit before intake/update starts, at bounded checkpoints while it continues, and before publication. The plan must choose checkpoints that prevent ongoing work from relying on an observation older than the limit; pause affected work when a fresh observation cannot be obtained. Recognized loss of Collect authority pauses or stops affected work; it cannot publish a candidate revision or replace the last good revision.

Stop affected active meetings and require a freshly authorized start. Context repair and seamless roster changes are deferred. The receiving agent may propose a different freshness target with evidence and a clear consequence; it must not silently weaken this default.

## D10 — Casket content and publication

**Fixed:** encrypted S3 storage, usable restoration, retained evidence/provenance, and updates that preserve the previous good revision.

**Resolved default:** a casket is a logical archive across one or more objects. A published revision is immutable. One updater creates a candidate, checks integrity and coverage, and publishes it as one coherent revision. Failed or interrupted publication leaves the last good revision intact.

Include original sources, identities/versions, relevant time and attribution metadata, policy references, coverage, and either usable derived artifacts or enough information to rebuild them. Cite the original preserved source version. The implementation plan decides which artifacts to persist or regenerate.

Sensitive persisted content, including derived material and saved records, must be encrypted. Keys or equivalent decryption authority must remain protected separately from the ciphertext. This is a behavioral requirement; no encryption library, cipher construction, or key-management vendor is prescribed.

## D11 — Restore, scale, and suspension

A cold restore must work after disposable intake/working state is removed and the departed account is disabled. Active service decryption authority and current authorization remain required; “independent” does not mean self-decrypting.

A local filesystem mock is not evidence of meeting the S3 requirement. An existing S3-compatible endpoint may be used for an isolated prototype if its required behavior is verified and identified.

Process archives without requiring the entire plaintext casket in memory. Before implementation, the code-access agent records a concrete scale profile: worker memory limit, corpus bytes, item count, largest supported item, environment, and measurement method. Use a scale corpus larger than that memory limit. Large-archive mechanics and the tiny fixture's semantic correctness are separate checks.

The plan sets provisional latency and size targets from the observed baseline; it cannot claim measured performance without measurement. If hardware or services are missing, identify the contract and the milestone blocked.

A custodian can suspend an affected casket. Evidence withdrawal may suspend the whole casket and affected old records until a clean permitted view is available. Old revisions and cached outputs cannot bypass withdrawal. Full retention/purge automation is deferred.

## D12 — Session and revision behavior

A session pins a published revision and a temporal perspective. Publishing a new revision does not change an existing session. Starting against another revision is explicit and uses fresh context.

During development of S02–S04, consultation may use a completed, encrypted intake revision with explicitly accepted coverage and a fixed evidence identifier. The same current authorization and evidence-boundary rules apply. Completion of Increment A and the full prototype requires publication to and verified restoration from the Mortuary. This exception keeps early delivery slices usable without creating a circular dependency on S05.

Permission evaluation remains current even while evidence revision is pinned. A snapshot of old authority must never accompany a revision into the present.

## D13 — Meeting and virtual participant scope

**Resolved default:** text meeting, one employee representation, fixed human roster, and at most one configured virtual employee. Every current participant must have the relevant Consult grant; shared system-retrieved evidence is the intersection of their effective permissions.

The departed representation is a scoped system capability, not a revived LDAP principal. It receives no authority from the departed employee's former permissions.

The virtual employee has its own explicit identity and grants. It constrains room scope like a human and cannot inherit host authority. Start it with fresh room-scoped context; private agent memory or tools cannot import knowledge outside the room's permitted evidence. No external actions or delegation chains.

Human-entered statements are attributed to their speaker and are scenario inputs, not verified historical sources. Preventing a human from manually typing confidential information is outside this prototype's system-controlled retrieval boundary.

## D14 — Current problems and saved outputs

Keep recorded history, participant-supplied current requirements, and generated advice distinguishable. New meeting content cannot become earlier employee knowledge.

**Resolved default:** saved brief/transcript access is limited to the original roster and current Consult/evidence permissions. Wider sharing is deferred. It is acceptable to deny an entire record when a reader no longer qualifies; selective redaction is unnecessary.

Retain roster, topic, revision, perspective/cutoff, citations, and evidence dependencies. The brief contains What we learned, How the decision evolved, What remains uncertain, and Next check. A reingested record retains its contemporary generated origin.

## D15 — Failure behavior and evidence of completion

Prompt-like instructions inside sources are untrusted content. They cannot change policy, time boundaries, system instructions, or the user's task.

Record consequential actions with actor, outcome, revision, perspective, and policy references without creating a second access path through logs. Status and denial explanations expose only appropriate metadata.

Use the shared Definition of Done from the backlog, including meaningful policy, temporal, revision, integrity, and recovery checks. A graceful “unknown,” denied request, or incomplete-coverage state can be the correct acceptance outcome.

## Decisions intentionally owned by the code-access agent

Choose the runtime/components to reuse, storage layout, metadata representation, graph/retrieval changes, auth integration points, key-management integration, job mechanism, and UI implementation after inspecting source. Choose concrete sample adapters, item limits, reference hardware, and performance targets based on actual capabilities.

Do not send these back as broad product questions. Record a main recommendation, evidence, alternatives rejected, and any external prerequisite. If a choice would change one of D01–D15, identify that as a proposed product change rather than disguising it as implementation.

