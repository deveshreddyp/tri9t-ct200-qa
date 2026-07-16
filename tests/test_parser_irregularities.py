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
    _is_heading_candidate,
    _is_table_header,
    _parse_numbering_level,
    ParsedNode,
    parse_pdf_to_tree,
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
# Test 1b — Font-priority gate: body-font depth-1 block must not become heading
# ===========================================================================

class TestFontPriorityGate:
    """
    Reproduces the exact ct200 bug: "4. Alarms and Safety Behavior" appears
    immediately after the "5. Hypertensive Crisis" list item in section 3.3.
    Because the PDF renders it at the same 10pt body font as the list items,
    the numbering-pattern signal alone is insufficient to classify it as a
    heading.  The font-priority rule must veto it and keep it as body text.

    Separately, we confirm that a truly styled heading (larger font or bold)
    at the same textual position is still promoted correctly, proving the fix
    is not a blanket suppressor of depth-1 headings.
    """

    ALARMS_TEXT = "4. Alarms and Safety Behavior"

    # ------------------------------------------------------------------
    # Unit-level: _is_heading_candidate
    # ------------------------------------------------------------------

    def _font_buckets_body_only(self):
        """Simulate a document where only 12pt+ counts as a heading font."""
        # body median = 10pt; 12pt is above threshold
        return {12.0: 1, 14.0: 1}  # two heading sizes; 10pt absent

    def test_alarms_heading_at_body_font_rejected(self):
        """
        '4. Alarms and Safety Behavior' at 10pt (body size, not bold)
        must NOT be accepted as a heading candidate.
        """
        block = _make_block(self.ALARMS_TEXT, font_size=10.0, is_bold=False)
        font_buckets = self._font_buckets_body_only()
        assert not _is_heading_candidate(self.ALARMS_TEXT, block, font_buckets), (
            "'4. Alarms' at body font size must be rejected as a heading candidate "
            "(font-priority rule)"
        )

    def test_alarms_heading_with_bold_accepted(self):
        """
        The same text, but now rendered bold, must be accepted as a heading.
        Bold is a typographic heading signal that overrides the font-size gate.
        """
        block = _make_block(self.ALARMS_TEXT, font_size=10.0, is_bold=True)
        font_buckets = self._font_buckets_body_only()
        assert _is_heading_candidate(self.ALARMS_TEXT, block, font_buckets), (
            "'4. Alarms' rendered bold must be accepted as a heading candidate"
        )

    def test_alarms_heading_with_larger_font_accepted(self):
        """
        The same text at a larger font size (above body median) must be accepted.
        """
        block = _make_block(self.ALARMS_TEXT, font_size=12.0, is_bold=False)
        font_buckets = self._font_buckets_body_only()
        assert _is_heading_candidate(self.ALARMS_TEXT, block, font_buckets), (
            "'4. Alarms' at heading font size must be accepted as a heading candidate"
        )

    def test_subsection_at_body_font_still_accepted(self):
        """
        Depth-2 (and deeper) numbered blocks are unambiguous structural markers
        and must always be accepted, even at body font size.
        Multi-component numbers like '4.1' cannot be list-item numbering.
        """
        block = _make_block("4.1 Overpressure Protection", font_size=10.0, is_bold=False)
        font_buckets = self._font_buckets_body_only()
        assert _is_heading_candidate("4.1 Overpressure Protection", block, font_buckets), (
            "'4.1 Overpressure Protection' at body font must still be a heading "
            "(depth-2 is unambiguous)"
        )

    # ------------------------------------------------------------------
    # Full-pipeline: the real ct200 tree structure
    # ------------------------------------------------------------------

    def test_full_pipeline_section4_not_orphaned(self):
        """
        Reproduces the exact ct200 tree layout that triggered the bug.

        Block sequence (in PDF reading order):
          [12pt bold]  "3.3 Result Display and Classification"
          [10pt body]  body prose
          [10pt body]  classification list (1. Normal … 5. Hypertensive Crisis)
          [10pt body]  "4. Alarms and Safety Behavior"   <- the bug block
          [10pt bold]  "4.1 Overpressure Protection"     <- sub-section

        Expected tree:
          LEVEL1 "3.3 Result Display ..."   (parent of 4.x due to no styled '4.' node)
            body: contains '4. Alarms and Safety Behavior'
          LEVEL2 "4.1 Overpressure Protection"

        Old behaviour: "4." became a spurious LEVEL1 node, orphaning 4.1-4.3
        under section 3 and omitting a real section 4 node.
        """
        long_body = (
            "After a completed measurement the device displays systolic pressure, "
            "diastolic pressure, and pulse rate simultaneously. " * 4
        )
        classification_list = (
            "1. Normal: systolic < 120 and diastolic < 80\n"
            "2. Elevated: systolic 120\u2013129 and diastolic < 80\n"
            "3. Hypertension Stage 1: systolic 130\u2013139 or diastolic 80\u201389\n"
            "4. Hypertension Stage 2: systolic \u2265 140 or diastolic \u2265 90\n"
            "5. Hypertensive Crisis: systolic > 180 or diastolic > 120"
        )
        blocks = [
            _make_block("3.3 Result Display and Classification",
                        font_size=12.0, is_bold=True, block_idx=0),
            _make_block(long_body, font_size=10.0, block_idx=1),
            _make_block(classification_list, font_size=10.0, block_idx=2),
            # The bug block: "4. Alarms" at body font — must NOT become a node
            _make_block(self.ALARMS_TEXT, font_size=10.0, is_bold=False, block_idx=3),
            # Sub-section 4.1 — must become a LEVEL2 node under 3.3
            _make_block("4.1 Overpressure Protection",
                        font_size=10.0, is_bold=True, block_idx=4),
        ]
        nodes = _run_pipeline(blocks)
        titles = [n.title for n in nodes]

        # '4. Alarms' must NOT appear as its own tree node
        assert not any("Alarms" in t and t.strip().startswith("4") for t in titles), (
            f"'4. Alarms and Safety Behavior' must not be a tree node (font-priority "
            f"rule). Found titles: {titles}"
        )

        # '4. Alarms' text must be in the body of section 3.3
        section33 = next(
            (n for n in nodes if "Result" in n.title or "3.3" in n.title), None
        )
        assert section33 is not None, (
            f"Section 3.3 node must exist. Titles: {titles}"
        )
        assert "Alarms" in section33.body, (
            "'4. Alarms and Safety Behavior' text must appear in section 3.3's body"
        )

        # '4.1 Overpressure' must exist as a proper node
        node_41 = next(
            (n for n in nodes if "Overpressure" in n.title), None
        )
        assert node_41 is not None, (
            f"'4.1 Overpressure Protection' must be a tree node. Titles: {titles}"
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


# ===========================================================================
# Test 4 — Real PDF E2E Validation
# ===========================================================================

class TestRealDocumentEndToEnd:
    """
    Validates the parser against the actual physical PDF for section 4,
    ensuring that the combination of numbering, font priority, and table
    header exclusion correctly extracts the tricky 'Alarms' block without
    relying on synthetic fixtures.
    """

    def test_section_4_and_classification_list(self):
        pdf_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "ct200_manual.pdf"
        )
        assert os.path.exists(pdf_path), f"Test requires real PDF at {pdf_path}"

        nodes = parse_pdf_to_tree(pdf_path)

        # 1. Section 4 must exist as a LEVEL1 node
        section_4 = next((n for n in nodes if "4. Alarms" in n.title and n.level == 1), None)
        assert section_4 is not None, "Section 4. Alarms must exist as a level-1 node"

        # 2. It must have exactly 3 children: 4.1, 4.2, 4.3
        children = [n for n in nodes if n.parent_id == section_4.node_id]
        assert len(children) == 3, f"Expected 3 children for section 4, got {len(children)}"
        titles = [n.title for n in children]
        assert any("4.1" in t for t in titles), "Missing 4.1 Overpressure Protection"
        assert any("4.2" in t for t in titles), "Missing 4.2 Error Codes"
        assert any("4.3" in t for t in titles), "Missing 4.3 Alarm Thresholds"

        # 3. "Hypertensive Crisis" must be inside 3.3's body and NOWHERE else
        section_3_3 = next((n for n in nodes if "3.3" in n.title), None)
        assert section_3_3 is not None, "Section 3.3 must exist"
        assert "Hypertensive Crisis" in section_3_3.body, "3.3 must contain 'Hypertensive Crisis'"

        for n in nodes:
            if n.node_id != section_3_3.node_id:
                assert "Hypertensive Crisis" not in n.title, f"List item leaked into title of {n.title}"
                assert "Hypertensive Crisis" not in n.body, f"List item leaked into body of {n.title}"

    def test_table_markdown_formatting(self):
        pdf_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "ct200_manual.pdf"
        )
        nodes = parse_pdf_to_tree(pdf_path)

        error_codes_node = next((n for n in nodes if "4.2 Error Codes" in n.title), None)
        assert error_codes_node is not None, "Section 4.2 Error Codes must exist"
        
        body = error_codes_node.body
        
        # Verify markdown table structure
        assert "| Code | Meaning | Device Behavior |" in body, "Missing markdown header row"
        assert "| --- | --- | --- |" in body, "Missing markdown separator row"
        
        # Verify rows are correctly associated and pipe-delimited
        assert "| E1 | Cuff not connected or leak detected | Aborts measurement, displays E1 |" in body
        assert "| E2 | Motion artifact detected during measurement | Aborts measurement, displays E2, prompts retry |" in body
        assert "| E5 | Internal sensor fault | Device disables measurement function, displays E5 until serviced |" in body
