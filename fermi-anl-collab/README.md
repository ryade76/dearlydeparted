# Dearly Departed — Agent planning handoff

**Version 1.0 · 28 October 2026 · Planning inputs, not an implemented system**

> Bring Alex into the room. Show me what made sense then, what changed, and why we ended up here.

This package gives an agent with the demo source code enough product direction and concrete examples to produce an implementation plan. It is self-contained. The original conversation and backlog page are not required.

**No demo repository was supplied or inspected while preparing this package.** There are no claims here that a capability is implemented, that application tests passed, or that a deployment is ready.

## Start here

1. Extract this ZIP somewhere the planning agent can read.
2. Give the agent access to the existing demo repository.
3. Paste the contents of [AGENT_PROMPT.md](AGENT_PROMPT.md), or instruct the agent to read and follow that file.
4. Receive three completed documents: `IMPLEMENTATION_PLAN.md`, `STORY_CODE_MAP.md`, and `DECISIONS_AND_UNCERTAINTIES.md`.

The receiving task ends with a reviewable, source-grounded plan. Feature implementation, deployment, and creating external tickets are separate work.

## Read in this order

| File | Purpose |
| --- | --- |
| [AGENT_PROMPT.md](AGENT_PROMPT.md) | Assignment, authority, inspection workflow, and required outputs. |
| [PRODUCT_BRIEF.md](PRODUCT_BRIEF.md) | Product outcome, scope, users, three modules, and lean experience. |
| [DECISIONS.md](DECISIONS.md) | Product decisions resolved for this handoff; temporal, policy, archive, and meeting rules. |
| [USER_STORIES.md](USER_STORIES.md) | The agreed twelve user stories, scope notes, and shared Definition of Done. |
| [stories.json](stories.json) | The same story cards with stable IDs, dependencies, and acceptance references. |
| [acceptance/README.md](acceptance/README.md) | How to use the acceptance examples and evaluate generated answers. |
| [acceptance/scenarios.json](acceptance/scenarios.json) | Canonical acceptance examples, including consequential failure and boundary cases. |
| [acceptance/scenarios.feature](acceptance/scenarios.feature) | A generated Given–When–Then view of those examples; step definitions are not included. |
| [acceptance/access_examples.json](acceptance/access_examples.json) | Fictional identities, policy inputs, and expected access decisions. |
| [fixtures/README.md](fixtures/README.md) | Raw synthetic source files, revision selection, and provenance conventions. |
| [planning_templates](planning_templates) | Three templates for the receiving agent's completed plan. |
| [SOURCES.md](SOURCES.md) | Backlog provenance and primary agile references. |
| [PACKAGE_MANIFEST.json](PACKAGE_MANIFEST.json) | File hashes and packaging metadata. |

## What is fixed and what remains for the agent

The core outcome, twelve story IDs, three modules, temporal behavior, and access rules are fixed for this prototype. The handoff resolves a number of previously open defaults, including initial source formats, immutable published revisions, fixed meeting membership, and saved-record access.

The receiving agent must inspect the code to choose implementation details: which components to reuse, where to enforce policy and time boundaries, how to persist provenance, and what needs to change. Unknown credentials, directory schema, or hardware must become named integration prerequisites with a development substitute and a blocking point. They must not become guesses about the repository.

The three increments are outcomes, not promised sprint lengths:

- **A — Recover an inherited decision:** S01–S05, with evidence and a cold-restorable archive.
- **B — Follow its evolution:** S06–S08, including historical perspective and recovered old evidence.
- **C — Use the reasoning together:** S09–S12, including a separately authorized virtual participant.

All three are required for the full prototype. A working first increment is a milestone, not completion.

## Authority and consistency

Use this order for product interpretation: explicit user instructions, then `DECISIONS.md`, then the story cards, then the acceptance examples. The latter clarify the former; they do not authorize scope changes. Report any actual contradiction and propose a specific resolution. Repository instructions continue to govern how to work in the repository.

`USER_STORIES.md` preserves the agreed backlog with the explicitly noted D05 wording clarification. `stories.json` adds machine-readable references; `scenarios.feature` is generated from `scenarios.json`. Fixture metadata is a testing vocabulary, not a required production database schema.

Everything in `fixtures/` and `acceptance/access_examples.json` is fictional. The fixture contains later and restricted records intentionally: directory presence is never permission to ingest or retrieve every file for every revision.

## Lean guardrails

Retain working demo components where they can meet the stories. Separate services, a new graph platform, a general policy language, voice, avatars, live connectors, and autonomous external actions are not prerequisites. Keep storage and parser tasks underneath the user outcomes they enable. Do not drop decision evolution to make the first demonstration easier.

