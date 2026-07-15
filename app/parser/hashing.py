import hashlib
import re

def normalize_text(text: str) -> str:
    """
    Normalize text by stripping leading/trailing whitespace, converting to lowercase,
    and collapsing multiple whitespace characters into a single space.
    """
    if not text:
        return ""
    text = text.strip().lower()
    text = re.sub(r'\s+', ' ', text)
    return text

def calculate_content_hash(title: str, body: str) -> str:
    """
    Calculate SHA-256 hash of the normalized title and body.
    """
    normalized_title = normalize_text(title)
    normalized_body = normalize_text(body)
    combined = f"{normalized_title}\n{normalized_body}"
    return hashlib.sha256(combined.encode('utf-8')).hexdigest()
