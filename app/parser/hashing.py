"""
hashing.py — Content hashing per TRD.md section 5.

content_hash = sha256(normalize(title) + "\\n" + normalize(body))

Normalization:
  - strip leading/trailing whitespace
  - lowercase  (case changes do NOT count as a meaningful change)
  - collapse internal whitespace runs to a single space

The same normalization is applied to both title and body so that purely
cosmetic rewrapping of a paragraph does not change the hash.
"""

import hashlib
import re


def normalize_text(text: str) -> str:
    """
    Normalize *text* for hashing:
      1. strip
      2. lowercase
      3. collapse whitespace (spaces, tabs, newlines) to single space
    """
    if not text:
        return ""
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def content_hash(title: str, body: str) -> str:
    """
    Return the SHA-256 hex digest of normalize(title) + "\\n" + normalize(body).

    This is the canonical function name used throughout the codebase.
    """
    combined = normalize_text(title) + "\n" + normalize_text(body)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


# Backwards-compat alias (the scaffolded orm.py and tests may reference this)
calculate_content_hash = content_hash
