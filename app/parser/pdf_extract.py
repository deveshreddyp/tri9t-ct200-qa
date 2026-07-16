"""
pdf_extract.py — Raw text + layout extraction from PDF using PyMuPDF.

Returns per-page lists of text blocks, each with:
  - text:       the string content of the block
  - font_size:  dominant font size in the block (pt)
  - is_bold:    True if any span in the block uses a bold font
  - bbox:       (x0, y0, x1, y1) bounding box on the page
  - page_num:   1-indexed page number
  - block_idx:  index of the block on its page (for order preservation)
"""

import fitz  # PyMuPDF
from typing import List, Dict, Any, Optional


def _dominant_font_size(block: dict) -> float:
    """Return the font size that covers the most characters in a block."""
    size_chars: Dict[float, int] = {}
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            fs = span.get("size", 0.0)
            text = span.get("text", "")
            size_chars[fs] = size_chars.get(fs, 0) + len(text)
    if not size_chars:
        return 0.0
    return max(size_chars, key=lambda s: size_chars[s])


def _is_bold(block: dict) -> bool:
    """Return True if any span in the block uses a bold font name."""
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            flags = span.get("flags", 0)
            font = span.get("font", "").lower()
            # PyMuPDF flag bit 4 (value 16) = bold
            if (flags & 16) or "bold" in font:
                return True
    return False


def _block_text(block: dict) -> str:
    """Concatenate all span texts in a block, preserving intra-line spacing."""
    lines = []
    for line in block.get("lines", []):
        line_text = "".join(span.get("text", "") for span in line.get("spans", []))
        if line_text.strip():
            lines.append(line_text)
    return "\n".join(lines)


def _rect_contains(outer: tuple, inner: tuple) -> bool:
    """True if inner bbox is fully contained within outer bbox."""
    ox0, oy0, ox1, oy1 = outer
    ix0, iy0, ix1, iy1 = inner
    return ox0 <= ix0 and oy0 <= iy0 and ox1 >= ix1 and oy1 >= iy1


def _rect_intersects_heavily(b1: tuple, b2: tuple) -> bool:
    """True if b1 and b2 intersect and the overlap is >= 50% of b1's area."""
    x0 = max(b1[0], b2[0])
    y0 = max(b1[1], b2[1])
    x1 = min(b1[2], b2[2])
    y1 = min(b1[3], b2[3])

    if x1 <= x0 or y1 <= y0:
        return False
        
    overlap_area = (x1 - x0) * (y1 - y0)
    b1_area = (b1[2] - b1[0]) * (b1[3] - b1[1])
    if b1_area == 0:
        return False
        
    return (overlap_area / b1_area) >= 0.5


def _table_to_markdown(table) -> str:
    """Convert a PyMuPDF Table object into a markdown pipe-delimited string."""
    data = table.extract()
    if not data:
        return ""
        
    lines = []
    # Find the maximum number of columns to pad missing cells
    max_cols = max(len(row) for row in data)
    
    for i, row in enumerate(data):
        clean_row = []
        for c in row:
            # Clean up newlines within cells and replace unicode errors if any
            cell_text = str(c).replace("\n", " ").strip() if c is not None else ""
            clean_row.append(cell_text)
            
        # Pad with empty strings if row is short
        while len(clean_row) < max_cols:
            clean_row.append("")
            
        lines.append("| " + " | ".join(clean_row) + " |")
        
        # Add markdown separator after header row
        if i == 0:
            separator = "| " + " | ".join(["---"] * max_cols) + " |"
            lines.append(separator)
            
    return "\n".join(lines)


def extract_blocks(pdf_path: str) -> List[Dict[str, Any]]:
    """
    Open *pdf_path* and return a flat list of text-block dicts across all pages.

    Each dict:
        type       (str)   "text" or "table"
        page_num   (int)   1-indexed
        block_idx  (int)   position of block on that page
        text       (str)   full text content of the block (or markdown table)
        font_size  (float) dominant font size in pt
        is_bold    (bool)  True if any span is bold
        bbox       (tuple) (x0, y0, x1, y1)
    """
    doc = fitz.open(pdf_path)
    all_blocks: List[Dict[str, Any]] = []

    for page_num, page in enumerate(doc, start=1):
        # 1. Detect tables on this page
        raw_tables = page.find_tables()
        valid_tables = []
        if raw_tables and raw_tables.tables:
            for t in raw_tables:
                # Filter out sub-tables (cells mistakenly identified as distinct tables)
                is_contained = False
                for other in raw_tables:
                    if t == other: continue
                    if _rect_contains(other.bbox, t.bbox):
                        is_contained = True
                        break
                if not is_contained:
                    valid_tables.append(t)
                    
        emitted_tables = set()

        # 2. Extract reading-order blocks
        raw = page.get_text("dict", sort=True)
        for block_idx, block in enumerate(raw.get("blocks", [])):
            if block.get("type") != 0:  # 0 = text, 1 = image
                continue
                
            text = _block_text(block)
            if not text.strip():
                continue
                
            bbox = tuple(block.get("bbox", (0, 0, 0, 0)))
            
            # 3. Check for table intersection
            intersecting_table = None
            for i, t in enumerate(valid_tables):
                if _rect_intersects_heavily(bbox, t.bbox):
                    intersecting_table = (i, t)
                    break
                    
            if intersecting_table:
                t_idx, t = intersecting_table
                # If we haven't emitted this table yet, synthesize a block for it now
                if t_idx not in emitted_tables:
                    table_md = _table_to_markdown(t)
                    if table_md.strip():
                        all_blocks.append(
                            {
                                "type": "table",
                                "page_num": page_num,
                                "block_idx": block_idx,
                                "text": table_md,
                                "font_size": 0.0,  # body text fallback
                                "is_bold": False,
                                "bbox": t.bbox,
                            }
                        )
                    emitted_tables.add(t_idx)
                # Suppress the raw overlapping text block since it's inside the table
                continue
                
            # 4. Standard text block
            all_blocks.append(
                {
                    "type": "text",
                    "page_num": page_num,
                    "block_idx": block_idx,
                    "text": text,
                    "font_size": _dominant_font_size(block),
                    "is_bold": _is_bold(block),
                    "bbox": bbox,
                }
            )

    doc.close()
    return all_blocks
