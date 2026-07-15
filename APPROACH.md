# System Approach & Architecture

## Data Model Diagram

```mermaid
erDiagram
    Document ||--o{ DocumentVersion : has
    Document {
        string id PK
        string title
        datetime created_at
    }
    DocumentVersion ||--o{ Node : contains
    DocumentVersion {
        int id PK
        string document_id FK
        int version_number
        datetime created_at
    }
    Node {
        int id PK
        int document_version_id FK
        string logical_node_id
        int parent_id FK
        string title
        string body
        string content_hash
    }
    Node ||--o{ Node : "parent/child"

    Selection ||--o{ SelectionItem : includes
    Selection {
        int id PK
        string name
        datetime created_at
    }
    SelectionItem {
        int id PK
        int selection_id FK
        int node_id FK
        string logical_node_id
        string content_hash_at_selection
    }
```

> [!NOTE]
> The **Generation Store** operates asynchronously alongside this schema. Generations are physically decoupled into a filesystem `data/generations/{generation_id}.json` store per TRD Section 3. Staleness bridges this gap dynamically by querying the current `Node` table utilizing the JSON record's `source_snapshot` footprint.

## Tree-Parsing Decisions & Irregularities

Due to an out-of-band specification update, we pivoted to ingest native PDF files rather than standard Markdown. To accomplish this without error-prone visual OCR, we leveraged `PyMuPDF` to slice structural heading blocks strictly by font-size and weight heuristics.

During initial parsing experiments, we discovered several critical **Irregularities** that broke the standard hierarchy, which were explicitly solved and individually unit-tested:
1. **Level-Skip Normalisation**: We encountered sections like `2.1.1.1` occurring immediately underneath `2.1` with no intervening parent. The parser was originally discarding these as orphans or injecting them as siblings. We patched this by forcing level-skips to recursively mount to the highest available chronological parent.
2. **Numbered Lists Mimicking Headings**: Section 3.3 Classification contained lists that parsed identically to headings (e.g., `1. Type BF Applied Part`). We implemented structural heuristic checks to ensure inline numbered items inside text bodies were explicitly rejected as logical node partitions.
3. **Table Header Suppression**: The raw text extraction of the Error Code table parsed `Code` and `Meaning` as top-level headers, corrupting the document tree. We patched the heuristic to aggressively suppress short title blocks adjacent to known table structures.

## Version-Matching Strategy

New Document ingestion initiates our `VersioningMatcher`. The strategy employs a prioritized, cascading waterfall approach:
1. **Direct Path + Title Match**: Absolute confidence.
2. **Fuzzy Fallback**: If titles mutate slightly, we apply a `.sequenceMatcher` similarity score. If it passes `>80%`, we classify it as the identical logical node, absorbing the mutation.
3. **Duplicate Disambiguation**: When encountering repeated sibling titles (e.g., two distinct "Warnings" blocks under a parent), we anchor them securely utilizing sequential order indexing.

**Known Failure Modes**: The fuzzy match heuristic is highly effective but incredibly aggressive. A drastic rewrite of a title combined with a drastic rewrite of the body will fall beneath the 80% threshold, causing the matcher to silently classify the behavior as a total "node removal" and a subsequent "new node addition", rather than a clean update.

## LLM Prompt Design & Resiliency

Test-case generation relies on a strict loop enforcing rigid schema structure (located in `app/generation/service.py`):
- **Prompt Architecture**: Instructs the LLM via a specialized system role ("QA engineer for regulated medical devices"), injecting context directly into isolated markdown blocks.
- **Failover Strategy**: Utilizes `Pydantic` `ValidationError` structures as a real-time feedback mechanism. If the model outputs malformed JSON, the pipeline catches the failure, strips the stack trace, and recursively queries the LLM a second time, providing its own exact error so it may self-correct. Irrecoverable failures are stored as raw text dumps with `status=failed`.

---

## Decision Log

**1. What's most likely to silently give wrong results without erroring, and how would you catch it?**
The fuzzy title-match fallback in the versioning layer. It attempts to gracefully handle typos (e.g., `Battery Life` to `Battery Life Under Typical Use`). If it improperly binds two unrelated sections because of a high word-overlap, it will silently misclassify them as the same logical node, corrupting the staleness downstream. We would catch this by formally logging every fuzzy-match decision and its similarity score to a specialized audit-table for human review.

**2. Where did you choose simplicity over correctness because of time, and what breaks first in production?**
The Staleness whole-body hashing mechanism (`content_hash = md5(title + body)`). By checking absolute binary hashing, a cosmetic typo fix (fixing a misspelled "the") trips the exact same `stale: True` flag as a life-threatening safety-critical change (e.g., changing Deflation Time from 10s to 15s). In production, this breaks user trust incredibly quickly via *alert fatigue*. Engineers would simply ignore the staleness warnings. A robust production version must leverage an LLM-based materiality scoring pass to ask: "Is this semantic change actually clinically material?".

**3. One input you didn't handle, and what the system does when it sees it.**
A section physically moved under a distinctly different parent (e.g., moving `2.1.1 Battery Life` out of `2.1 Hardware` into `3.0 Specifications`). Our version matching anchors primarily on parent-hierarchy paths. If the parent moves, the system will not register it as a "Move" operation; it will bluntly flag the old section as "Removed", and the newly located section as a brand new "Added" node.

## Future Improvements (With More Time)

- **Vector-Based Semantic Search**: Transitioning from exact-string search (`GET /nodes/search`) to dense embeddings leveraging PGVector or a standalone vector store to dramatically increase search relevancy.
- **Materiality Scoring**: Replacing exact hashing staleness with an LLM evaluation pipeline capable of classifying document changes into "Critical", "Moderate", or "Cosmetic" buckets.
- **Interactive Review UI**: A frontend application dedicated solely to diff-resolution, allowing QA engineers to interactively resolve and accept the generated diffs outputted by the Staleness checks.
