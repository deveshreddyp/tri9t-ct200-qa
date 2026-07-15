"""
pdf_tree.py — First-pass PDF-to-tree parser.

Strategy
--------
PDFs have no markdown syntax, so we must infer structure from visual
and typographic cues. This module uses a two-signal approach:

1. **Numbering pattern** (primary)
   A block whose first non-whitespace token matches the regex
     ^\\d+(\\.(\\d+))*\\.?\\s
   is treated as a numbered heading, and the number of dot-separated
   segments determines the level (e.g. "2." → level 1, "2.1" → level 2,
   "2.1.3" → level 3, "2.1.3.1" → level 4).

2. **Font-size / bold heuristic** (secondary / tie-break)
   Among blocks that do NOT carry a numbering prefix, we look at the
   distribution of font sizes across the whole document.  Blocks with
   font_size >= (body_median + threshold) OR is_bold=True are treated
   as headings, assigned level based on their relative font size bucket.

   This covers front-matter, appendix headings, and un-numbered sections.

Known limitations (documented, not hidden)
------------------------------------------
- Multi-line numbered headings: if a heading wraps across two PyMuPDF
  blocks (rare but possible in some PDFs), only the first block is
  captured as the title; the remainder lands in the following body.
- Tables: table cells sometimes come through as individual tiny blocks;
  they will appear as body text of the preceding section.
- Headers / footers: running page headers share the same font size as
  body text or headings; they are suppressed by a y-position filter
  (top 5 % and bottom 5 % of page height are excluded).
- Purely image-based pages produce no blocks and are silently skipped.

Tree shape
----------
Each node is a plain dataclass (no ORM dependency) so this module can
be used in scripts and tests without a database:

    ParsedNode(
        node_id:      str   (uuid4, assigned at parse time)
        logical_node_id: str  (same as node_id for v1; overwritten by matcher for v2+)
        parent_id:    str | None
        level:        int   (1 = top-level)
        title:        str
        body:         str   (text of all body blocks under this heading)
        order_index:  int   (0-based, among siblings)
        content_hash: str   (sha256 per hashing.py)
    )
"""

import re
import uuid
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.parser.hashing import calculate_content_hash

# ---------------------------------------------------------------------------
# Numbering-pattern regex
# ---------------------------------------------------------------------------
# Matches:   "1. ", "2.3 ", "3.1.4.2 ", "A.1 " (appendix), "10.2.1 "
# Does NOT match ordinary sentences that start with a number ("3 cups of…").
_NUMBERED_HEADING_RE = re.compile(
    r"^([A-Z]|\d+)(\.\d+)*\.?\s"
)

def _parse_numbering_level(text: str) -> Optional[int]:
    """
    If *text* starts with a section-number prefix, return the heading level
    (number of dot-separated components).  Otherwise return None.

    Examples:
        "1. Introduction"     → 1
        "2.3 Safety"          → 2
        "3.1.4 Sub-section"   → 3
        "A Appendix"          → 1   (single letter)
        "A.1 Sub-appendix"    → 2
        "Hello world"         → None
    """
    m = _NUMBERED_HEADING_RE.match(text.strip())
    if not m:
        return None
    prefix = m.group(0).strip().rstrip(".")
    # Count segments: "2.3.1" → 3 segments → level 3
    return len(prefix.split("."))


# ---------------------------------------------------------------------------
# Header/footer filter
# ---------------------------------------------------------------------------
def _is_header_footer(block: Dict[str, Any], page_height: float, margin: float = 0.05) -> bool:
    """Return True if the block sits in the top or bottom margin of the page."""
    if page_height <= 0:
        return False
    _, y0, _, y1 = block["bbox"]
    return y1 <= page_height * margin or y0 >= page_height * (1 - margin)


# ---------------------------------------------------------------------------
# Font-size bucket classifier (secondary signal)
# ---------------------------------------------------------------------------
def _build_font_buckets(blocks: List[Dict[str, Any]]) -> Dict[float, int]:
    """
    Cluster unique font sizes into heading levels based on descending rank.

    Returns a dict mapping font_size → heading_level (1-based).
    Body-text size → mapped to 0 (body, not a heading).

    Heuristic:
      - Body size = the median font size across all blocks weighted by
        character count (most text is body).
      - Anything >= body_size + 1pt is considered a potential heading.
      - We assign levels by ranking distinct heading-size values descending
        (largest = level 1, next = level 2, …).
    """
    size_chars: Dict[float, int] = {}
    for b in blocks:
        fs = b["font_size"]
        char_count = len(b["text"])
        size_chars[fs] = size_chars.get(fs, 0) + char_count

    if not size_chars:
        return {}

    # Weighted median
    sizes_sorted = sorted(size_chars.keys())
    total = sum(size_chars.values())
    cumulative = 0
    median_size = sizes_sorted[0]
    for s in sizes_sorted:
        cumulative += size_chars[s]
        if cumulative >= total / 2:
            median_size = s
            break

    threshold = 1.0  # pt above median to count as heading
    heading_sizes = sorted(
        [s for s in size_chars if s >= median_size + threshold],
        reverse=True,
    )
    return {s: i + 1 for i, s in enumerate(heading_sizes)}


# ---------------------------------------------------------------------------
# ParsedNode dataclass
# ---------------------------------------------------------------------------
@dataclass
class ParsedNode:
    node_id: str
    logical_node_id: str
    parent_id: Optional[str]
    level: int
    title: str
    body: str
    order_index: int
    content_hash: str = field(init=False)

    def __post_init__(self):
        self.content_hash = calculate_content_hash(self.title, self.body)


# ---------------------------------------------------------------------------
# Block classification
# ---------------------------------------------------------------------------
def _classify_blocks(
    blocks: List[Dict[str, Any]],
    font_buckets: Dict[float, int],
) -> List[Tuple[str, int, str]]:
    """
    Return a list of (kind, level, text) for each block.
      kind = "heading" | "body"
      level = 1..N for headings, 0 for body
    """
    classified = []
    for b in blocks:
        text = b["text"].strip()
        if not text:
            continue

        # --- Signal 1: numbering pattern ---
        num_level = _parse_numbering_level(text)
        if num_level is not None:
            classified.append(("heading", num_level, text))
            continue

        # --- Signal 2: font-size / bold heuristic ---
        fs = b["font_size"]
        level_by_size = font_buckets.get(fs, 0)
        if level_by_size > 0 or b["is_bold"]:
            # Bold with body-size font → treat as level = max_font_level + 1
            if level_by_size == 0 and b["is_bold"]:
                level_by_size = (max(font_buckets.values()) + 1) if font_buckets else 1
            classified.append(("heading", level_by_size, text))
            continue

        classified.append(("body", 0, text))

    return classified


# ---------------------------------------------------------------------------
# Tree assembly
# ---------------------------------------------------------------------------
def _build_tree(classified: List[Tuple[str, int, str]]) -> List[ParsedNode]:
    """
    Walk the classified blocks and assemble the tree.

    heading blocks open a new node.
    body blocks accumulate as the body of the most-recently opened node.
    """
    nodes: List[ParsedNode] = []
    # Stack of (node_id, level) for ancestor tracking
    ancestor_stack: List[Tuple[str, int]] = []
    sibling_counters: Dict[Optional[str], int] = {}  # parent_id → next order_index
    # Body accumulation for the current open node
    current_node: Optional[ParsedNode] = None
    current_body_lines: List[str] = []

    def _flush_current():
        nonlocal current_node, current_body_lines
        if current_node is not None:
            body = "\n\n".join(current_body_lines).strip()
            # Rebuild with final body and recompute hash
            current_node.body = body
            current_node.content_hash = calculate_content_hash(
                current_node.title, body
            )
            nodes.append(current_node)
        current_node = None
        current_body_lines = []

    # Text before the first heading becomes a synthetic root node ("Preamble")
    preamble_lines: List[str] = []

    for kind, level, text in classified:
        if kind == "body":
            if current_node is None:
                preamble_lines.append(text)
            else:
                current_body_lines.append(text)
            continue

        # --- heading block ---
        _flush_current()

        # Pop ancestor stack until we find a parent at level < current level
        while ancestor_stack and ancestor_stack[-1][1] >= level:
            ancestor_stack.pop()

        parent_id = ancestor_stack[-1][0] if ancestor_stack else None

        order_index = sibling_counters.get(parent_id, 0)
        sibling_counters[parent_id] = order_index + 1

        nid = str(uuid.uuid4())
        current_node = ParsedNode(
            node_id=nid,
            logical_node_id=nid,
            parent_id=parent_id,
            level=level,
            title=text,
            body="",  # will be filled on flush
            order_index=order_index,
        )
        current_body_lines = []
        ancestor_stack.append((nid, level))

    # Flush final node
    _flush_current()

    # Prepend preamble if any
    if preamble_lines:
        preamble_body = "\n\n".join(preamble_lines).strip()
        pnid = str(uuid.uuid4())
        preamble = ParsedNode(
            node_id=pnid,
            logical_node_id=pnid,
            parent_id=None,
            level=0,
            title="[Preamble]",
            body=preamble_body,
            order_index=0,
        )
        nodes.insert(0, preamble)
        # Shift order_index of all other root nodes
        for n in nodes[1:]:
            if n.parent_id is None:
                n.order_index += 1

    return nodes


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def parse_pdf_to_tree(pdf_path: str) -> List[ParsedNode]:
    """
    Parse *pdf_path* into a list of ParsedNode objects representing the
    document's heading-based tree.

    This is a standalone function (no DB, no ORM) — suitable for scripts,
    tests, and later wrapping in the ingest endpoint.
    """
    from app.parser.pdf_extract import extract_blocks

    blocks = extract_blocks(pdf_path)

    if not blocks:
        return []

    # Per-page heights for header/footer filtering
    import fitz
    doc = fitz.open(pdf_path)
    page_heights = {i + 1: doc[i].rect.height for i in range(len(doc))}
    doc.close()

    # Filter header/footer blocks
    filtered = [
        b for b in blocks
        if not _is_header_footer(b, page_heights.get(b["page_num"], 0))
    ]

    font_buckets = _build_font_buckets(filtered)
    classified = _classify_blocks(filtered, font_buckets)
    return _build_tree(classified)
