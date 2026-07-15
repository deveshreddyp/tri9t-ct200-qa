#!/usr/bin/env python3
"""
scripts/dump_tree.py — Ingest a PDF manual and print the parsed tree indented,
so you can visually verify it against the real document.

Usage:
    python scripts/dump_tree.py                       # uses data/ct200_manual.pdf
    python scripts/dump_tree.py data/ct200_manual_v2.pdf
    python scripts/dump_tree.py --both                # compare v1 and v2 side by side

Output format for each node:
    <indent>LEVEL<n>  [idx]  <title truncated to 80 chars>
                      hash: <first 12 chars of content_hash>
                      body: <first 120 chars of body>
"""

import sys
import os
import textwrap
import argparse

# Ensure repo root is on the path when run from the project root
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.parser.pdf_tree import parse_pdf_to_tree, ParsedNode
from typing import List


INDENT_UNIT = "  "
MAX_TITLE = 100
MAX_BODY = 140


def _indent(level: int) -> str:
    # Level 0 = preamble, level 1 = top-level, etc.
    return INDENT_UNIT * max(0, level)


def print_tree(nodes: List[ParsedNode], label: str) -> None:
    print(f"\n{'='*70}")
    print(f"  TREE: {label}")
    print(f"  Total nodes: {len(nodes)}")
    print(f"{'='*70}\n")

    for node in nodes:
        ind = _indent(node.level)
        level_tag = f"LEVEL{node.level}" if node.level > 0 else "PREAMBLE"
        title_display = node.title[:MAX_TITLE].replace("\n", " ↵ ")
        if len(node.title) > MAX_TITLE:
            title_display += "…"

        print(f"{ind}[{level_tag}] [{node.order_index}] {title_display}")
        print(f"{ind}  hash:{node.content_hash[:12]}  parent:{str(node.parent_id)[:8] if node.parent_id else 'ROOT'}")

        if node.body.strip():
            body_preview = node.body.strip()[:MAX_BODY].replace("\n", " ↵ ")
            if len(node.body.strip()) > MAX_BODY:
                body_preview += "…"
            # Wrap body preview for readability
            wrapped = textwrap.fill(body_preview, width=80, initial_indent=ind + "  > ",
                                    subsequent_indent=ind + "    ")
            print(wrapped)
        print()


def dump_pdf(pdf_path: str) -> None:
    label = os.path.basename(pdf_path)
    print(f"\nParsing: {pdf_path}", flush=True)
    nodes = parse_pdf_to_tree(pdf_path)
    print_tree(nodes, label)

    # Also write to a scratch txt file alongside the script
    scratch_dir = os.path.join(repo_root, "scripts", "output")
    os.makedirs(scratch_dir, exist_ok=True)
    out_path = os.path.join(scratch_dir, label.replace(".pdf", "_tree.txt"))
    with open(out_path, "w", encoding="utf-8") as f:
        for node in nodes:
            ind = "  " * max(0, node.level)
            f.write(f"{ind}[LEVEL{node.level}] [{node.order_index}] {node.title}\n")
            if node.body.strip():
                for bline in node.body.strip().splitlines():
                    f.write(f"{ind}    {bline}\n")
            f.write("\n")
    print(f"\n  → Also saved to: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump parsed PDF tree to stdout.")
    parser.add_argument(
        "pdf",
        nargs="?",
        default=os.path.join(repo_root, "data", "ct200_manual.pdf"),
        help="Path to PDF file (default: data/ct200_manual.pdf)",
    )
    parser.add_argument(
        "--both",
        action="store_true",
        help="Also parse ct200_manual_v2.pdf for comparison",
    )
    args = parser.parse_args()

    if not os.path.exists(args.pdf):
        print(f"ERROR: PDF not found: {args.pdf}")
        print("Please place the PDF(s) in the data/ directory and re-run.")
        sys.exit(1)

    dump_pdf(args.pdf)

    if args.both:
        v2_path = os.path.join(repo_root, "data", "ct200_manual_v2.pdf")
        if not os.path.exists(v2_path):
            print(f"\nWARNING: v2 PDF not found at {v2_path}, skipping.")
        else:
            dump_pdf(v2_path)


if __name__ == "__main__":
    main()
