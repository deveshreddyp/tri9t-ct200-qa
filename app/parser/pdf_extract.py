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
from typing import List, Dict, Any


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


def extract_blocks(pdf_path: str) -> List[Dict[str, Any]]:
    """
    Open *pdf_path* and return a flat list of text-block dicts across all pages.

    Each dict:
        page_num   (int)   1-indexed
        block_idx  (int)   position of block on that page
        text       (str)   full text content of the block
        font_size  (float) dominant font size in pt
        is_bold    (bool)  True if any span is bold
        bbox       (tuple) (x0, y0, x1, y1)
    """
    doc = fitz.open(pdf_path)
    all_blocks: List[Dict[str, Any]] = []

    for page_num, page in enumerate(doc, start=1):
        raw = page.get_text("dict", sort=True)  # sort=True → reading order
        for block_idx, block in enumerate(raw.get("blocks", [])):
            if block.get("type") != 0:  # 0 = text, 1 = image
                continue
            text = _block_text(block)
            if not text.strip():
                continue
            all_blocks.append(
                {
                    "page_num": page_num,
                    "block_idx": block_idx,
                    "text": text,
                    "font_size": _dominant_font_size(block),
                    "is_bold": _is_bold(block),
                    "bbox": tuple(block.get("bbox", (0, 0, 0, 0))),
                }
            )

    doc.close()
    return all_blocks
