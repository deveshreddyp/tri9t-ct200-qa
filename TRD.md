# TRD — CardioTrack CT-200 QA Traceability System

Companion to PRD.md. This is the technical design: stack, data model, algorithms, API
contracts, and the project structure to use inside Antigravity IDE. Treat every "strategy"
section as a starting proposal — confirm it against the real `ct200_manual.md` content before
locking it in, per the assignment's own instruction to read the file before writing the parser.

## 1. Tech stack

- **Language/framework:** Python 3.11+, FastAPI, Pydantic v2, Uvicorn.
- **Relational store:** SQLAlchemy ORM + SQLite file DB — stores documents, versions, tree
  nodes, selections.
- **Document store:** local JSON files under `data/generations/` OR a local MongoDB —
  stores LLM generation outputs. Justify choice in approach doc (a well-structured JSON
  store per generation is acceptable and simpler to grade/run than standing up Mongo).
- **LLM:** any provider with a free tier (e.g. Groq, Gemini, OpenRouter). Use their SDK or
  plain `httpx` calls. All calls wrapped with schema validation (Pydantic) on the response.
- **Testing:** `pytest`.
- **Diffing:** Python's built-in `difflib` for lightweight text diffs — no need for a heavier lib.
- **Version control:** git, real incremental commits (see §7).

## 2. Project structure (for Antigravity IDE)

```
tri9t-ct200/
├── README.md
├── APPROACH.md
├── pyproject.toml / requirements.txt
├── .env.example
├── data/
│   ├── ct200_manual.md
│   └── ct200_manual_v2.md
├── app/
│   ├── main.py                 # FastAPI app, router includes
│   ├── config.py                # env vars, settings
│   ├── db.py                    # SQLAlchemy engine/session
│   ├── models/
│   │   ├── orm.py               # SQLAlchemy models: Document, Version, Node, Selection, SelectionItem
│   │   └── schemas.py           # Pydantic request/response models
│   ├── parser/
│   │   ├── markdown_tree.py     # heading-based parser → tree
│   │   └── hashing.py           # content hash function
│   ├── versioning/
│   │   └── matcher.py           # v1<->v2 node matching strategy
│   ├── selections/
│   │   └── service.py
│   ├── generation/
│   │   ├── prompt.py            # prompt template + builder
│   │   ├── llm_client.py        # provider call + retry
│   │   └── store.py             # JSON/Mongo persistence for generations
│   ├── staleness/
│   │   └── service.py           # hash comparison + diff summary
│   └── routers/
│       ├── documents.py
│       ├── nodes.py
│       ├── selections.py
│       ├── generations.py
├── tests/
│   ├── test_parser_irregularities.py   # the required 3+ tests
│   ├── test_versioning.py
│   ├── test_staleness.py
│   └── test_generation_contract.py     # malformed LLM output handling
└── scripts/
    └── demo_flow.sh / demo_flow.py     # end-to-end curl/script demo
```

## 3. Data model

### Relational (SQLite via SQLAlchemy)

**Document**
`id, name, created_at`

**DocumentVersion**
`id, document_id (FK), version_number, ingested_at, source_filename`

**Node**
`id, document_version_id (FK), logical_node_id, parent_id (FK, nullable), level (int),
title (str), body (text), order_index (int), content_hash (str)`
- `logical_node_id` is the stable ID that persists across versions for "the same section"
  (see §4). Two Node rows (v1 and v2) that represent the same logical section share this
  value but have different primary keys and different `content_hash` if the body changed.

**Selection**
`id, name, created_at`

**SelectionItem**
`id, selection_id (FK), node_id (FK — points at a specific Node row, i.e. specific
version), logical_node_id, content_hash_at_selection`
- Storing `content_hash_at_selection` redundantly (not just via the FK) is deliberate: it lets
  staleness checks work even if a Node row were ever deleted/migrated. FK to the concrete
  version is the primary pin; the hash is a belt-and-suspenders audit trail.

### Document/JSON store (generations)

Each generation record:
```json
{
  "generation_id": "uuid",
  "selection_id": "uuid",
  "created_at": "...",
  "source_snapshot": [
    {"logical_node_id": "...", "node_id": "...", "content_hash": "...", "title": "..."}
  ],
  "prompt_version": "v1",
  "llm_provider": "groq",
  "llm_raw_response": "...",
  "test_cases": [
    {"id": "...", "title": "...", "steps": ["..."], "expected_result": "..."}
  ],
  "validation_status": "ok | repaired | failed",
  "validation_notes": "..."
}
```
`source_snapshot` is what staleness checks against later — it is the generation's own copy
of the hashes it was built from, independent of the relational DB.

## 4. Version matching strategy (nodes across versions)

**Proposed approach — layered, not single-signal:**
1. **Primary: path + title match.** Build a path key from the sequence of ancestor titles
   plus this node's title (e.g. `Safety > Alarms > Over-pressure Alarm`). If a v2 node has an
   identical path key to a v1 node, treat them as the same logical node.
2. **Fallback: fuzzy title match within same parent.** If no exact path match (heading was
   reworded), compare title similarity (e.g. `difflib.SequenceMatcher` ratio) against sibling
   nodes under the matched parent. Above a threshold (e.g. 0.8) → same logical node,
   flagged as "title changed" in addition to any body change.
3. **Unmatched nodes:** a v1 node with no v2 counterpart → marked `removed` in v2's diff
   view. A v2 node with no v1 counterpart → new logical node, `added`.
4. **Duplicate headings:** if two siblings share an identical title (a known irregularity to
   verify against the real file), path key alone is ambiguous — disambiguate by order_index
   among same-title siblings (1st duplicate ↔ 1st duplicate, 2nd ↔ 2nd). Document this as a
   known failure mode: if duplicates are reordered or one is deleted, matching can misfire.

**Known failure modes to state in approach doc:**
- Section moved to a different parent with same title: path key changes → looks like
  remove+add instead of "moved." Acceptable for this assignment's scope; call it out.
- Heavily reworded title + reworded body together: fuzzy match may fail entirely →
  silently treated as remove+add. This is the sharpest edge; mention it explicitly in the
  decision log (§ "most likely to silently give wrong results").

## 5. Content hashing

`content_hash = sha256(normalized(title) + "\n" + normalized(body))`
Normalize = strip, collapse whitespace, maybe lowercase (decide and document — case
changes probably shouldn't count as "changed"). Hash is computed once per Node row at
ingestion time and stored.

## 6. Staleness detection

For a stored generation:
1. Look at `source_snapshot` (list of `logical_node_id` + `content_hash` at generation time).
2. For each entry, find the corresponding Node in the **latest** DocumentVersion via
   `logical_node_id`.
3. If not found → node was removed → generation flagged `stale: true`,
   reason: `"source section removed in a later version"`.
4. If found and `content_hash` differs → `stale: true`, reason: `"source section text
   changed"`, plus a lightweight diff (`difflib.unified_diff` or `ndiff`, truncated) between old
   and new body for that node.
5. If found and hash matches for all snapshot entries → `stale: false`.

**Honesty requirement (per assignment):** explicitly document in the approach doc that this
is a binary, whole-body hash comparison — it cannot distinguish a typo fix from a changed
safety threshold. State what a better version would do (e.g. numeric/unit-aware diffing,
or an LLM-based "is this change clinically material" pass) and why it's out of scope here.

## 7. LLM generation — prompt & robustness

**Prompt design (put in `generation/prompt.py`):**
- System/instruction: role = QA engineer for a regulated medical device; input = one or more
  document sections (title + body); output = **strict JSON only**, array of 3–5 objects each
  with `title`, `steps` (array of strings), `expected_result`.
- Include the exact JSON schema in the prompt and an example, and explicitly instruct
  "no prose, no markdown fences, JSON array only."

**Structured-output validation & retry policy:**
1. Call LLM.
2. Try `json.loads` on the raw response (after stripping code fences defensively).
3. Validate against a Pydantic model (`list[TestCaseIdea]`, 3–5 items, non-empty
   steps/expected_result).
4. If parse or validation fails → one retry with a corrective follow-up message that includes
   the error and the original bad output, asking for corrected JSON only.
5. If still failing after retry → store the raw response with
   `validation_status: "failed"`, return a clear API error (`422`) to the caller rather than
   fabricating fake test cases. Never silently invent output to make it "look" successful.
6. If retry succeeds → `validation_status: "repaired"` and note that in the record.

**Idempotency policy for "same selection submitted twice":**
Proposed default: **allow multiple generations per selection** (each is its own timestamped
record, e.g. useful if LLM is retried by the user, or the underlying doc changed). The
retrieval API returns all generations for a selection, most recent first, each independently
flagged for staleness. Alternative (return cached result unless `force=true` query param) is
acceptable if justified — pick one and defend it in the decision log.

## 8. API surface (draft — FastAPI routers)

```
POST   /documents/ingest              # multipart or path -> new Document + Version 1
POST   /documents/{doc_id}/versions   # ingest new version of existing doc

GET    /documents/{doc_id}/sections?version=latest        # top-level nodes
GET    /nodes/{node_id}                                    # full node incl. children
GET    /nodes/search?q=...&version=latest
GET    /nodes/{logical_node_id}/changes?from=1&to=2        # diff summary

POST   /selections                    # {name, items: [{node_id}, ...]}
GET    /selections/{selection_id}

POST   /selections/{selection_id}/generate     # run LLM, store result
GET    /generations/by-selection/{selection_id}
GET    /generations/by-node/{logical_node_id}
GET    /generations/{generation_id}             # includes staleness block
```

Each generation response includes:
```json
{
  "generation_id": "...",
  "test_cases": [...],
  "staleness": {"stale": true, "reasons": ["source section text changed"], "diffs": [...]}
}
```

## 9. Parser irregularities — process, not a fixed list

Do NOT hardcode a list of irregularities before reading the file. Required process:
1. Open `data/ct200_manual.md` and read it fully, by hand, before writing the parser.
2. Write a first-pass parser assuming clean `#`/`##`/`###` heading structure.
3. Run it, dump the resulting tree, and manually diff node count / structure against a manual
   read of the file's headings (`grep '^#' data/ct200_manual.md` as a cross-check).
4. Note every mismatch (e.g. skipped heading levels, a heading-looking line inside a code
   block or table, duplicate section titles, a bullet list item styled like a heading, front-matter,
   an appendix with different numbering). Write these down as you find them.
5. For each irregularity found, write one targeted unit test that reproduces it in a small
   markdown fixture, then fix the parser to pass it.
6. Record the whole before/after story (what broke, how it was found, how it was fixed) in
   the approach doc — this is explicitly graded.

## 10. Testing requirements

- `tests/test_parser_irregularities.py` — minimum 3 tests, each tied to a real irregularity
  found in `ct200_manual.md` (not synthetic ones invented for convenience).
- `tests/test_versioning.py` — at least: unchanged node recognized as same logical node;
  changed body flagged; removed node flagged; duplicate-heading siblings matched by order.
- `tests/test_staleness.py` — generation against unchanged node → not stale; against
  changed node → stale with correct reason.
- `tests/test_generation_contract.py` — mock the LLM client to return malformed JSON,
  valid-but-wrong-shape JSON, and valid JSON; assert the retry/failure/store behavior in §7.

## 11. Approach doc requirements (checklist)

- Data model diagram/explanation.
- Tree-parsing decisions + irregularities found and how (per §9).
- Version-matching strategy + known failure modes (per §4).
- LLM prompt design + structured-output/retry strategy (per §7).
- Decision log — 3 required questions from the PDF, answered specifically, not generically:
  1. What's most likely to silently give wrong results without erroring, and how would you
     catch it? (Suggested honest answer to develop: the fuzzy title-match fallback in §4 —
     it can silently misclassify a reworded section as remove+add. Catch it by logging every
     fuzzy-match decision with its similarity score for manual audit.)
  2. Where did you choose simplicity over correctness because of time, and what breaks
     first in production? (Candidate answer: whole-body hash staleness, §6 — a cosmetic
     edit trips the same flag as a safety-critical change; in production this would either be
     ignored by alert fatigue or would need materiality scoring.)
  3. One input you didn't handle, and what the system does when it sees it. (Candidate
     answer: a section moved under a different parent — currently registers as remove+add,
     not "moved"; document this rather than hiding it.)
- What you'd do differently with more time.

## 12. What NOT to build (reiterated from PRD)

No auth, no generic arbitrary-markdown parser, no auto-regeneration of stale test cases, no UI.
Keep the scope tight — depth on parsing/versioning/staleness is what's graded.
