# PRD — CardioTrack CT-200 QA Traceability System
Tri9T AI — AI Engineering Internship Assignment

## 1. Problem statement
Regulated device documentation (like a medical device manual) changes over time. QA test
cases are generated from specific sections of that documentation. When the documentation
changes, nobody automatically knows which previously-generated test cases are now based on
stale text. This is a real failure mode in regulated software.

We are building a backend system that:
- Parses a markdown technical manual into a structured, versioned tree.
- Lets a user browse/search that tree.
- Lets a user select sections and generate QA test-case ideas via an LLM.
- Detects and surfaces when a generation is now "stale" because the source text changed.

This is an internal tool for a QA engineer, not a consumer product. No auth, no UI.

## 2. Users & primary use case
**User:** a QA engineer working with a regulated device manual.

**Core flow:**
1. Ingest `ct200_manual.md` → get a browsable tree (version 1).
2. Browse/search the tree, pick a few sections (e.g. "cuff over-pressure" + "auto-deflate").
3. Save that as a named selection, pinned to version 1.
4. Generate QA test case ideas from that selection via LLM.
5. Later, ingest `ct200_manual_v2.md` as version 2 of the same document.
6. Query: "did anything I generated test cases from change?" → get flagged, with a diff.
7. Retrieve old test cases and see, at read time, whether they're stale.

## 3. Scope — what must exist

### 3.1 Ingestion & structuring
- Parse a markdown manual into a heading-based tree (level, title, body, parent/child).
- Persist tree nodes with a content hash per node.
- Parser must not silently drop/merge/mis-parent content on irregularities in the real file
  (duplicate headings, skipped heading levels, tables, code blocks, lists that look like
  headings, etc. — exact list TBD after inspecting the actual manual).
- At least 3 unit tests, each targeting one specific irregularity found in the real document.

### 3.2 Versioning
- Re-ingest a modified manual as a new version of the *same* logical document.
- Same version 1 tree must remain queryable after v2 exists.
- Nodes that are unchanged (semantically) across versions map to the same logical node.
- Nodes whose body changed are flagged as changed.
- Matching strategy must be chosen and justified, including where it breaks.

### 3.3 Browse API
- List top-level sections, filterable by version (default: latest).
- Get one node by ID: full text, children, content hash.
- Search/filter nodes by heading text or body text.
- Given a node ID: has it changed across versions, and a lightweight diff if so.

### 3.4 Selection API
- Create a named selection = a set of specific (node_id, version) pairs.
- Selections are immutable snapshots — re-ingesting the doc must never change what an
  existing selection resolves to.

### 3.5 Generation API
- Given a selection, reconstruct the underlying text, send to an LLM with a designed prompt,
  parse structured output into 3–5 test case ideas.
- Must handle malformed/incomplete/off-spec LLM output without crashing or silently
  fabricating data — explicit retry/validation/failure policy required.
- Generated output stored linked to (a) the selection and (b) the exact node content hashes
  it was generated from, so staleness can be checked later even after re-versioning.
- Explicit, defensible policy for "same selection submitted twice."

### 3.6 Staleness / impact detection
- At retrieval time, tell the user whether a stored generation still reflects current document
  text, by comparing the hashes stored at generation time to the current hashes of those
  (logical) nodes in the latest version.
- Must state limitations honestly (e.g. a one-word wording change vs. a changed numeric
  threshold currently look the same to a hash-based check — call this out, don't hide it).

### 3.7 Retrieval API
- Fetch generations by selection ID or by node ID.
- Response must include the staleness status from 3.6 — not just raw data.

## 4. Explicitly out of scope
- Authentication / user accounts / multi-tenancy.
- A generic markdown parser for arbitrary documents (only this manual's real irregularities
  need to be handled correctly).
- Auto-regeneration of stale test cases (flagging only).
- Any frontend/UI. API + curl/Postman/script is the deliverable.

## 5. Success criteria (what "done" looks like)
- `POST` ingest v1, then ingest v2 → both versions independently browsable.
- A selection made against v1 still resolves to v1 text after v2 exists.
- A generation made from a section that changed in v2 is flagged `stale: true` on retrieval;
  one that didn't change is `stale: false`.
- Parser unit tests pass and each maps to a real irregularity found by inspection (documented).
- README lets a fresh reviewer run ingest v1 → generate → ingest v2 → see staleness, end to end.
- Approach doc answers the 3 decision-log questions with real reasoning, not boilerplate.

## 6. Constraints
- Tech stack: FastAPI + Pydantic + SQLAlchemy/SQLite (tree/versions/selections) and a
  NoSQL/JSON store for LLM outputs. Deviations must be justified in the approach doc.
- Any LLM provider; graded on structured-output validation discipline, not provider choice.
- Real, incremental git commit history is graded — not just the final diff.
