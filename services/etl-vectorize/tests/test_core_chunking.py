"""Tests for the recursive-character text chunker."""

from __future__ import annotations

from itertools import pairwise

from etl_vectorize.core.chunking import chunk_text


def test_short_text_returns_single_chunk() -> None:
    """Text shorter than chunk_size is returned as one chunk."""
    assert chunk_text("hello world", chunk_size=100, chunk_overlap=10) == ["hello world"]


def test_empty_text_returns_no_chunks() -> None:
    """Empty (or whitespace-only) text produces no chunks."""
    assert chunk_text("   ", chunk_size=100, chunk_overlap=10) == []


def test_splits_on_paragraph_boundaries_when_possible() -> None:
    """Paragraphs that individually fit stay intact rather than being hard-cut."""
    text = "Paragraph one is short.\n\nParagraph two is also short.\n\nParagraph three too."

    chunks = chunk_text(text, chunk_size=40, chunk_overlap=0)

    assert all(len(c) <= 40 for c in chunks)
    assert "Paragraph one is short." in chunks[0]


def test_long_text_produces_multiple_chunks_all_within_size() -> None:
    """A long document is split into multiple chunks, none exceeding chunk_size."""
    text = " ".join(f"word{i}" for i in range(500))

    chunks = chunk_text(text, chunk_size=100, chunk_overlap=20)

    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)


def test_consecutive_chunks_overlap() -> None:
    """Consecutive chunks share trailing/leading content per chunk_overlap."""
    text = " ".join(f"word{i}" for i in range(200))

    chunks = chunk_text(text, chunk_size=80, chunk_overlap=20)

    # The overlap tail of one chunk should appear at the start of the next.
    for first, second in pairwise(chunks):
        assert first[-10:] in second or first.split()[-1] in second.split()[0:3]


def test_no_chunk_is_empty_or_whitespace_only() -> None:
    """Every returned chunk has real content."""
    text = "Sentence one. Sentence two. Sentence three. " * 20

    chunks = chunk_text(text, chunk_size=50, chunk_overlap=5)

    assert all(c.strip() for c in chunks)
