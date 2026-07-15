"""
tests/test_parser_irregularities.py

Three tests, each reproducing a specific irregularity found by inspecting
the real ct200_manual.pdf tree dump.  Each test uses a minimal synthetic
block fixture that exactly reproduces the structure that caused the bug.

Irregularity index
------------------
#1  Numbered list mis-classified as heading
    Source: Section 3.3 in both PDFs.  The classification list
      "1. Normal: systolic < 120 and diastolic < 80
       2. Elevated: systolic 120–129 …
       …"
    was matched by the numbering regex and promoted to a top-level LEVEL1
    sibling of "4. Alarms", splitting the tree incorrectly.

#2  Level-skip: 2.1.1.1 as direct child of 2.1 (no 2.1.1 exists)
    Source: Section 2.1.1.1 in both PDFs.  Raw parse yields level=4 (four
    dot-segments). With no 2.1.1 intermediate node the parser would either
    (a) mis-parent it if it only pops by exact level, or (b) silently skip
    a level.  Fix: clamp to last_heading_level + 1.

#3  Table-header block mis-classified as heading
    Source: Section 4.2 "Error Codes" table.  The block
      "Code\\nMeaning\\nDevice Behavior"
    is bold and short, causing it to be treated as a heading node.  It
    should instead be body text of section 4.2.
"""

import sys
import os
import pytest

# Ensure repo root on path for direct test runs
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.parser.pdf_tree import (
    _classify_blocks,
    _normalise_levels,
    _build_tree,
    _looks_like_list_item,
    _is_table_header,
    _parse_numbering_level,
    ParsedNode,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_block(text: str, font_size: float = 10.0, is_bold: bool = False,
                bbox=(50, 100, 400, 120), page_num: int = 1,
                block_idx: int = 0) -> dict:
    """Minimal block dict matching the shape produced by pdf_extract.py."""
    return {
        "text": text,
        "font_size": font_size,
        "is_bold": is_bold,
        "bbox": bbox,
        "page_num": page_num,
        "block_idx": block_idx,
    }


def _run_pipeline(blocks: list) -> list:
    """Run classify → normalise → build, returning list of ParsedNode."""
    # Build font_buckets: anything > body_size gets a level
    from app.parser.pdf_tree import _build_font_buckets
    font_buckets = _build_font_buckets(blocks)
    classified = _classify_blocks(blocks, font_buckets)
    classified = _normalise_levels(classified)
    return _build_tree(classified)


# ===========================================================================
# Test 1 — Irregularity #1: Numbered list items must NOT become headings
# ===========================================================================

class TestNumberedListNotHeading:
    """
    Reproduces: section 3.3 classification list being hoisted to LEVEL1.

    The block text starts with "1. Normal: systolic < 120 …" which matches
    _NUMBERED_HEADING_RE but is body content, not a section heading.
    """

    LIST_BLOCK_SINGLE = (
        "1. Normal: systolic < 120 and diastolic < 80\n"
        "2. Elevated: systolic 120–129 and diastolic < 80\n"
        "3. Hypertension Stage 1: systolic 130–139 or diastolic 80–89"
    )

    LIST_BLOCK_ITEM = "1. Normal: systolic < 120 and diastolic < 80"

    def test_merged_list_block_is_body(self):
        """A block containing multiple 'N. text' sub-items is body, not heading."""
        assert _looks_like_list_item(self.LIST_BLOCK_SINGLE), (
            "Merged numbered list block should be detected as a list item"
        )

    def test_single_list_item_with_prose_keyword_is_body(self):
        """'1. Normal: …' contains a known list keyword → body."""
        assert _looks_like_list_item(self.LIST_BLOCK_ITEM), (
            "'1. Normal:' should be detected as a list item, not a section heading"
        )

    def test_list_does_not_appear_as_node_in_tree(self):
        """
        Full pipeline: a section heading followed by a numbered list block
        should produce exactly ONE node (the heading), with the list content
        in its body, NOT a second node for the list.

        Fixture uses enough body-text blocks (font_size=10) to push the
        weighted median to 10pt, making the 12pt heading block clearly
        above the threshold.
        """
        # Large body-text block so the median font size is clearly 10pt
        long_body = (
            "After a completed measurement the device displays systolic pressure "
            "diastolic pressure and pulse rate simultaneously along with a "
            "classification indicator based on the most recent joint clinical "
            "guidance available at time of manufacture. " * 3
        )
        blocks = [
            _make_block("3.3 Result Display and Classification",
                        font_size=12.0, is_bold=True, block_idx=0),
            # body text establishes 10pt as the median
            _make_block(long_body, font_size=10.0, block_idx=1),
            # the actual list item under test
            _make_block(self.LIST_BLOCK_ITEM, font_size=10.0, block_idx=2),
        ]
        nodes = _run_pipeline(blocks)

        titles = [n.title for n in nodes]
        assert not any("Normal" in t for t in titles), (
            f"List item text appeared as a heading node title. Titles: {titles}"
        )
        # The list content should be in the body of the section node
        section_node = next(
            (n for n in nodes if "Result" in n.title or "3.3" in n.title), None
        )
        assert section_node is not None, (
            f"Section 3.3 node should exist. Found titles: {titles}"
        )
        assert "Normal" in section_node.body, (
            "List content should be part of section 3.3's body"
        )


# ===========================================================================
# Test 2 — Irregularity #2: Level-skip (2.1.1.1 with no 2.1.1)
# ===========================================================================

class TestLevelSkipNormalisation:
    """
    Reproduces: "2.1.1.1 Battery Life Under Typical Use" following "2.1 General
    Specifications" with no intermediate "2.1.1" node.

    Raw parse gives level=4 for 2.1.1.1 (four segments).
    After normalisation it must be clamped to level=3 (one deeper than 2.1's
    level=2), making it a direct child of 2.1, not a distant descendant.
    """

    def test_level_skip_clamped_to_direct_child(self):
        """Level 4 heading after level 2 must be clamped to level 3."""
        classified_input = [
            ("heading", 2, "2.1 General Specifications"),
            ("heading", 4, "2.1.1.1 Battery Life Under Typical Use"),
        ]
        normalised = _normalise_levels(classified_input)

        levels = [level for kind, level, _ in normalised if kind == "heading"]
        assert levels == [2, 3], (
            f"Expected [2, 3] after normalisation, got {levels}. "
            "Level-skip should clamp 4 → 3 (direct child of level 2)."
        )

    def test_level_skip_node_is_child_of_parent_not_sibling(self):
        """In the tree, 2.1.1.1 must be parented under 2.1, not be a root sibling."""
        blocks = [
            _make_block("2. Physical Specifications", font_size=14.0, block_idx=0),
            _make_block("2.1 General Specifications", font_size=12.0, block_idx=1),
            _make_block("2.1.1.1 Battery Life Under Typical Use",
                        font_size=10.0, is_bold=True, block_idx=2),
        ]
        nodes = _run_pipeline(blocks)

        battery_node = next(
            (n for n in nodes if "Battery" in n.title), None
        )
        assert battery_node is not None, "Battery Life node should exist in tree"

        general_node = next(
            (n for n in nodes if "General" in n.title), None
        )
        assert general_node is not None, "General Specifications node should exist"

        assert battery_node.parent_id == general_node.node_id, (
            f"Battery Life node (level {battery_node.level}) should be a child of "
            f"General Specifications (level {general_node.level}), "
            f"but parent_id={battery_node.parent_id} != {general_node.node_id}"
        )

    def test_level_skip_preserves_title_verbatim(self):
        """The original '2.1.1.1' numbering must remain in the title unchanged."""
        classified_input = [
            ("heading", 2, "2.1 General Specifications"),
            ("heading", 4, "2.1.1.1 Battery Life Under Typical Use"),
        ]
        normalised = _normalise_levels(classified_input)
        titles = [text for _, _, text in normalised]
        assert "2.1.1.1 Battery Life Under Typical Use" in titles, (
            "Level normalisation must not alter the title text"
        )


# ===========================================================================
# Test 3 — Irregularity #3: Table-header block must not become a heading node
# ===========================================================================

class TestTableHeaderSuppression:
    """
    Reproduces: "Code\\nMeaning\\nDevice Behavior" block in section 4.2
    being classified as a heading node because it is bold and short.

    The block should be suppressed as a heading and appear as body text of
    its parent section instead.
    """

    TABLE_HEADER_TEXTS = [
        "Code\nMeaning\nDevice Behavior",
        "Parameter\nValue",
        "Code\nMeaning",
    ]

    @pytest.mark.parametrize("text", TABLE_HEADER_TEXTS)
    def test_table_header_block_detected(self, text):
        """_is_table_header should return True for known header row patterns."""
        block = _make_block(text, font_size=10.0, is_bold=True)
        assert _is_table_header(block), (
            f"Block '{text!r}' should be detected as a table header"
        )

    def test_table_header_becomes_body_not_node(self):
        """
        Full pipeline: section heading + bold table-header block + table rows
        should produce ONE node (the section), with the table content in its body.
        """
        blocks = [
            _make_block("4.2 Error Codes", font_size=12.0, is_bold=True, block_idx=0),
            _make_block("Code\nMeaning\nDevice Behavior",
                        font_size=10.0, is_bold=True, block_idx=1),
            _make_block("E1\nCuff not connected", font_size=10.0, block_idx=2),
            _make_block("E2\nMotion artifact", font_size=10.0, block_idx=3),
        ]
        nodes = _run_pipeline(blocks)

        titles = [n.title for n in nodes]
        assert not any("Code" in t and "Meaning" in t for t in titles), (
            f"Table header appeared as a heading node. Titles: {titles}"
        )
        error_codes_node = next(
            (n for n in nodes if "Error" in n.title or "4.2" in n.title), None
        )
        assert error_codes_node is not None, "Error Codes section node should exist"
        assert "E1" in error_codes_node.body or "E2" in error_codes_node.body, (
            "Table row content should appear in the section body, not be lost"
        )
