"""
- Title:    Recursive-character text chunking
- Author:   ai-circus-framework contributors

A minimal recursive-character splitter: tries progressively finer separators
(paragraph, line, sentence, word) so chunks break on natural boundaries where
possible, falling back to a hard character cut only when no separator fits.
"""

from __future__ import annotations

_SEPARATORS = ["\n\n", "\n", ". ", " "]


def chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Split text into overlapping chunks of at most `chunk_size` characters.

    Args:
        text: The full document text.
        chunk_size: Maximum characters per chunk.
        chunk_overlap: Characters of overlap between consecutive chunks (context
            continuity for the embedding/retrieval step).

    Returns:
        Non-empty, whitespace-trimmed chunks, in order.
    """
    pieces = _split_recursive(text.strip(), chunk_size, list(_SEPARATORS))
    return _merge_with_overlap(pieces, chunk_size, chunk_overlap)


def _split_recursive(text: str, chunk_size: int, separators: list[str]) -> list[str]:
    """Split `text` on the first separator that yields pieces within chunk_size."""
    if len(text) <= chunk_size:
        return [text] if text else []

    if not separators:
        # No separator fit — hard-cut as a last resort.
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]

    separator, *rest = separators
    parts = [p for p in text.split(separator) if p]
    if len(parts) <= 1:
        return _split_recursive(text, chunk_size, rest)

    result = []
    for part in parts:
        result.extend(_split_recursive(part, chunk_size, rest) if len(part) > chunk_size else [part])
    return result


def _merge_with_overlap(pieces: list[str], chunk_size: int, chunk_overlap: int) -> list[str]:
    """Greedily pack small pieces into chunks up to chunk_size, with a trailing overlap."""
    chunks: list[str] = []
    current = ""

    for piece in pieces:
        candidate = f"{current} {piece}".strip() if current else piece
        if len(candidate) <= chunk_size:
            current = candidate
            continue

        if current:
            chunks.append(current)
        current = f"{current[-chunk_overlap:]} {piece}".strip() if chunk_overlap and current else piece

    if current:
        chunks.append(current)
    return chunks
