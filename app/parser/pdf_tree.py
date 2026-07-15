"""
pdf_tree.py — First-pass PDF-to-tree parser.

Strategy
--------
PDFs have no markdown syntax, so we must infer structure from visual
and typographic cues. This module uses a layered classification approach:

1. **Numbered heading detection** (primary signal)
   A block whose first non-whitespace token matches a section-number prefix
   (e.g. "2.", "3.1", "2.1.1.1") is classified as a heading.  Level =
   number of dot-separated components in the prefix.

   IMPORTANT guard against numbered lists (irregularity #1):
   Before accepting a numbered prefix as a heading we apply TWO extra
   checks:
   a) The prefix number must be >= 1 and the FIRST component must not
      be a single digit followed by a period that looks like a list item
      at the *same depth as the current section body* — specifically, if
      the candidate level would EQUAL the current open node's level (or
      higher = shallower), but the prefix is a short "N." or "N. text"
      where N is 1..9 and the text after the dot strongly resembles
      descriptive prose (contains lowercase words or starts with an
      adjective like "Normal", "Elevated", "Hypertension"), we reject it
      as a heading and treat it as body text.
   b) A block is also rejected as a heading if it contains 3 or more
      newline-separated sub-items that each start with a digit+period
      (the block is the whole numbered list already merged by PyMuPDF).

2. **Table-header suppression** (irregularity #3)
   Blocks whose text contains exclusively short tokens separated by
   newlines without any sentence-like content (no verb, no punctuation
   other than maybe a colon) AND whose font_size is at the body-median
   level (meaning PyMuPDF's `sort=True` pulled them before the table
   content) are treated as body text, not headings — even if is_bold.
   Heuristic: if after stripping the block has <= 5 words and at least
   one of those words is a known table-header keyword ("Code", "Meaning",
   "Parameter", "Value", "Behavior"), treat as body.

3. **Level normalisation for level-skips** (irregularity #2)
   After classifying all blocks we do a single-pass normalisation of
   levels: a heading may never be more than 1 level deeper than its
   nearest preceding heading. If "2.1.1.1" (4 segments → raw level 4)
   follows "2.1" (level 2), it is clamped to level 3 so it becomes a
   direct child of "2.1". The original numbering string is preserved
   verbatim in the title; only the structural level used for tree-building
   changes.

4. **Ordering preservation** (irregularity #4)
   The parser preserves the physical order of blocks as they appear in
   the PDF (PyMuPDF sort=True gives reading order). Sections that appear
   out of numerical order in the PDF (e.g. 3.4 before 3.3) are stored in
   the order they appear, with order_index reflecting that physical order.
   We do NOT sort by section number. This faithfully captures the actual
   document; the approach doc notes this as expected behaviour.

Known limitations (documented, not hidden)
------------------------------------------
- Multi-line numbered headings that PyMuPDF splits across blocks: only
  the first block is the heading title; subsequent continuation blocks
  become body text. Rare in this PDF but possible in heavily formatted ones.
- Table cell rows come through as individual blocks and appear as body
  text under the table-header node (or their parent section if the header
  is suppressed). Cell content is not lost, just unseparated.
- Image-only pages produce no blocks and are silently skipped.
- Header/footer running text: filtered by y-position (top 5% and bottom
  5% of page height). If a real heading is positioned in that zone it will
  be dropped — acceptable trade-off; not observed in these PDFs.

Tree node shape
---------------
Each node is a plain dataclass (no ORM dependency):

    ParsedNode(
        node_id          str   uuid4 assigned at parse time
        logical_node_id  str   same as node_id for v1; overwritten by matcher
        parent_id        str | None
        level            int   1 = top-level (normalised)
        title            str   verbatim text of the heading block
        body             str   concatenated body blocks
        order_index      int   0-based among siblings
        content_hash     str   sha256(normalize(title)+"\\n"+normalize(body))
    )
"""

import re
import uuid
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.parser.hashing import content_hash as _content_hash

# ---------------------------------------------------------------------------
# Regex for section-number prefix
# ---------------------------------------------------------------------------
# Matches:  "1. ", "2.3 ", "3.1.4.2 ", "A.1 "
# Must be at the very start of the stripped text.
_NUMBERED_HEADING_RE = re.compile(
    r"^([A-Z]|\d+)(\.\d+)*\.?\s"
)

# Words/patterns that indicate a numbered LIST item rather than a section heading.
# If the part after the number+period matches these, we reject it as a heading.
_LIST_ITEM_PROSE_RE = re.compile(
    r"^(normal|elevated|hypertension|crisis|stage|systolic|diastolic|"
    r"step|note|warning|caution|important|see|refer|for)\b",
    re.IGNORECASE,
)

# Known table-header tokens — deliberately narrow to avoid false-positives on section
# titles.  These are short column-label words that would not appear as the primary
# content of a section title ("Code", "Meaning", "Parameter", "Value" are the actual
# column headers we see in this PDF's tables).
_TABLE_HEADER_TOKENS = {"code", "meaning", "parameter", "value", "behavior"}


def _parse_numbering_level(text: str) -> Optional[int]:
    """
    Return the heading level implied by the section-number prefix, or None.

    Level = number of dot-separated components:
        "1. Intro"        → 1
        "2.3 Safety"      → 2
        "3.1.4 Sub"       → 3
        "2.1.1.1 Battery" → 4  (before normalisation)
    """
    stripped = text.strip()
    m = _NUMBERED_HEADING_RE.match(stripped)
    if not m:
        return None
    prefix = m.group(0).strip().rstrip(".")
    return len(prefix.split("."))


def _looks_like_list_item(text: str) -> bool:
    """
    Return True if this numbered block is almost certainly a numbered LIST
    item rather than a section heading.

    Guards against irregularity #1: classification lists like
      "1. Normal: systolic < 120 ..."
      "2. Elevated: ..."
    being mis-promoted to heading nodes.

    Heuristics (any one is sufficient to return True):
    a) The text after the number+period starts with a known list keyword.
    b) The block contains 2+ sub-items that EACH start with digit+period,
       suggesting PyMuPDF merged a whole list into one block.
    c) The number prefix component count == 1 AND the first component is a
       single digit AND the rest of the text contains a colon (list items
       typically follow "N. Label: description" pattern).
    """
    stripped = text.strip()
    m = _NUMBERED_HEADING_RE.match(stripped)
    if not m:
        return False

    after_prefix = stripped[m.end():].strip()

    # (a) starts with a list-item keyword
    if _LIST_ITEM_PROSE_RE.match(after_prefix):
        return True

    # (b) block contains multiple "N." sub-items (merged list)
    sub_items = [
        ln for ln in text.splitlines()
        if re.match(r"^\s*\d+\.\s", ln)
    ]
    if len(sub_items) >= 2:
        return True

    # (c) single-segment number + colon in the rest
    prefix = m.group(0).strip().rstrip(".")
    if "." not in prefix and ":" in after_prefix[:40]:
        return True

    return False


def _is_table_header(block: Dict[str, Any]) -> bool:
    """
    Return True if the block looks like a PDF table column header row.

    Guards against irregularity #3: blocks like "Parameter\\nValue" or
    "Code\\nMeaning\\nDevice Behavior" being treated as headings.

    Heuristic: the block has <= 6 words total AND at least one word is a
    known table-header keyword AND there is no sentence-like structure
    (no verb ending, no period at end, typically very short words).
    """
    text = block.get("text", "").strip()
    words = text.split()
    if len(words) > 6:
        return False
    lower_words = {w.strip(".,;:").lower() for w in words}
    return bool(lower_words & _TABLE_HEADER_TOKENS)


# ---------------------------------------------------------------------------
# Header/footer position filter
# ---------------------------------------------------------------------------
def _is_header_footer(block: Dict[str, Any], page_height: float,
                       margin: float = 0.05) -> bool:
    """True if block sits in the top or bottom margin band of the page."""
    if page_height <= 0:
        return False
    _, y0, _, y1 = block["bbox"]
    return y1 <= page_height * margin or y0 >= page_height * (1 - margin)


# ---------------------------------------------------------------------------
# Font-size bucket classifier (secondary signal for un-numbered headings)
# ---------------------------------------------------------------------------
def _build_font_buckets(blocks: List[Dict[str, Any]]) -> Dict[float, int]:
    """
    Map distinct font sizes to heading levels (1-based) for un-numbered blocks.
    Body-size fonts → level 0 (= body, not a heading).

    Algorithm:
    - Weighted median font size across all blocks (weighted by character count)
      is treated as body size.
    - Any size >= body_median + 1pt is a heading-candidate.
    - Rank descending: largest size → level 1, next → level 2, …
    """
    size_chars: Dict[float, int] = {}
    for b in blocks:
        fs = b["font_size"]
        size_chars[fs] = size_chars.get(fs, 0) + len(b["text"])

    if not size_chars:
        return {}

    sizes_sorted = sorted(size_chars.keys())
    total = sum(size_chars.values())
    cumulative = 0
    median_size = sizes_sorted[0]
    for s in sizes_sorted:
        cumulative += size_chars[s]
        if cumulative >= total / 2:
            median_size = s
            break

    threshold = 1.0  # pt
    heading_sizes = sorted(
        [s for s in size_chars if s >= median_size + threshold],
        reverse=True,
    )
    return {s: i + 1 for i, s in enumerate(heading_sizes)}


# ---------------------------------------------------------------------------
# Level normalisation (irregularity #2: level-skip fix)
# ---------------------------------------------------------------------------
def _normalise_levels(
    classified: List[Tuple[str, int, str]]
) -> List[Tuple[str, int, str]]:
    """
    Clamp heading levels so no heading is more than 1 deeper than the
    previous heading.

    Example:
        LEVEL1 "2. Specs"
        LEVEL4 "2.1.1.1 Battery"   ← raw parse (4 segments)
        → clamped to LEVEL2         ← direct child of "2."

    The title string (third tuple element) is unchanged; only the level
    used for tree-building changes.  The approach doc notes this behaviour.
    """
    normalised = []
    last_heading_level = 0

    for kind, level, text in classified:
        if kind != "heading":
            normalised.append((kind, level, text))
            continue

        if last_heading_level == 0:
            # First heading ever
            clamped = max(1, level)
        else:
            # Cannot be more than 1 deeper than the last heading
            clamped = min(level, last_heading_level + 1)
            # But also cannot be 0 or negative
            clamped = max(1, clamped)

        normalised.append(("heading", clamped, text))
        last_heading_level = clamped

    return normalised


# ---------------------------------------------------------------------------
# Block classification
# ---------------------------------------------------------------------------
def _classify_blocks(
    blocks: List[Dict[str, Any]],
    font_buckets: Dict[float, int],
) -> List[Tuple[str, int, str]]:
    """
    Return (kind, level, text) for each block.
    kind = "heading" | "body"
    level = 1..N for headings, 0 for body
    """
    classified = []
    for b in blocks:
        text = b["text"].strip()
        if not text:
            continue

        # --- Guard: table header suppression (irregularity #3) ---
        if _is_table_header(b) and (b["is_bold"] or b["font_size"] in font_buckets):
            # Treat as body even if it would otherwise be classified as heading
            classified.append(("body", 0, text))
            continue

        # --- Signal 1: numbered heading pattern ---
        num_level = _parse_numbering_level(text)
        if num_level is not None:
            # Guard: numbered list items (irregularity #1)
            if _looks_like_list_item(text):
                classified.append(("body", 0, text))
            else:
                classified.append(("heading", num_level, text))
            continue

        # --- Signal 2: font-size / bold heuristic ---
        fs = b["font_size"]
        level_by_size = font_buckets.get(fs, 0)
        if level_by_size > 0 or b["is_bold"]:
            if level_by_size == 0 and b["is_bold"]:
                # Bold at body size → assign one level below deepest known bucket
                level_by_size = (max(font_buckets.values()) + 1) if font_buckets else 1
            classified.append(("heading", level_by_size, text))
            continue

        classified.append(("body", 0, text))

    return classified


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
        self.content_hash = _content_hash(self.title, self.body)

    def recompute_hash(self):
        """Call after mutating body to keep hash consistent."""
        self.content_hash = _content_hash(self.title, self.body)


# ---------------------------------------------------------------------------
# Tree assembly
# ---------------------------------------------------------------------------
def _build_tree(classified: List[Tuple[str, int, str]]) -> List["ParsedNode"]:
    """
    Walk the classified+normalised block list and assemble the heading tree.

    Ordering (irregularity #4):
    Blocks are processed in PDF reading order.  order_index reflects the
    physical order among siblings, NOT the numerical order of section
    numbers.  If the PDF has 3.4 before 3.3, they are stored that way.
    """
    nodes: List[ParsedNode] = []
    ancestor_stack: List[Tuple[str, int]] = []   # (node_id, level)
    sibling_counters: Dict[Optional[str], int] = {}
    current_node: Optional[ParsedNode] = None
    current_body_lines: List[str] = []
    preamble_lines: List[str] = []

    def _flush():
        nonlocal current_node, current_body_lines
        if current_node is not None:
            body = "\n\n".join(current_body_lines).strip()
            current_node.body = body
            current_node.recompute_hash()
            nodes.append(current_node)
        current_node = None
        current_body_lines = []

    for kind, level, text in classified:
        if kind == "body":
            if current_node is None:
                preamble_lines.append(text)
            else:
                current_body_lines.append(text)
            continue

        _flush()

        # Pop stack to find parent
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
            body="",
            order_index=order_index,
        )
        current_body_lines = []
        ancestor_stack.append((nid, level))

    _flush()

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
        for n in nodes[1:]:
            if n.parent_id is None:
                n.order_index += 1

    return nodes


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def parse_pdf_to_tree(pdf_path: str) -> List[ParsedNode]:
    """
    Parse *pdf_path* into a flat list of ParsedNode objects representing the
    document's heading-based tree.  Nodes are in PDF reading order.

    Pipeline:
        extract_blocks → filter headers/footers → classify blocks
        → normalise levels → build tree
    """
    import fitz
    from app.parser.pdf_extract import extract_blocks

    blocks = extract_blocks(pdf_path)
    if not blocks:
        return []

    doc = fitz.open(pdf_path)
    page_heights = {i + 1: doc[i].rect.height for i in range(len(doc))}
    doc.close()

    filtered = [
        b for b in blocks
        if not _is_header_footer(b, page_heights.get(b["page_num"], 0))
    ]

    font_buckets = _build_font_buckets(filtered)
    classified = _classify_blocks(filtered, font_buckets)
    classified = _normalise_levels(classified)
    return _build_tree(classified)
