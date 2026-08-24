"""Tests for the session-only document/image text-extraction dispatcher."""

from __future__ import annotations

import io

import pytest
from docx import Document
from pypdf import PdfWriter

from platform_registry.core import document_extraction as de


def _docx_bytes(paragraphs: list[str]) -> bytes:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _blank_pdf_bytes(page_count: int = 1) -> bytes:
    """A PDF with real pages but no text layer — exercises the OCR-fallback branch."""
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_kind_for_filename_dispatches_by_extension() -> None:
    """Each supported extension maps to its expected DocumentKind."""
    assert de.kind_for_filename("a.txt") == "text"
    assert de.kind_for_filename("a.md") == "markdown"
    assert de.kind_for_filename("a.docx") == "docx"
    assert de.kind_for_filename("a.pdf") == "pdf"
    assert de.kind_for_filename("a.png") == "image"


def test_kind_for_filename_rejects_unknown_extension() -> None:
    """An unrecognized extension raises UnsupportedDocumentError."""
    with pytest.raises(de.UnsupportedDocumentError):
        de.kind_for_filename("archive.zip")


def test_extract_document_txt_passthrough() -> None:
    """A .txt file's bytes are decoded and returned as-is."""
    result = de.extract_document("notes.txt", b"plain text content")
    assert result.kind == "text"
    assert result.text == "plain text content"
    assert result.truncated is False
    assert result.used_ocr is False


def test_extract_document_md_passthrough() -> None:
    """A .md file's bytes are decoded and returned as-is (no markdown rendering)."""
    result = de.extract_document("readme.md", b"# Heading\n\nSome body text.")
    assert result.kind == "markdown"
    assert "# Heading" in result.text


def test_extract_document_truncates_oversized_text() -> None:
    """Text past MAX_EXTRACTED_CHARS is capped and flagged truncated, not dropped."""
    data = ("a" * (de.MAX_EXTRACTED_CHARS + 500)).encode()
    result = de.extract_document("big.txt", data)
    assert result.truncated is True
    assert len(result.text) == de.MAX_EXTRACTED_CHARS


def test_extract_document_docx_reads_paragraphs() -> None:
    """A .docx file's paragraph text is concatenated in order."""
    data = _docx_bytes(["First paragraph.", "Second paragraph."])
    result = de.extract_document("report.docx", data)
    assert result.kind == "docx"
    assert "First paragraph." in result.text
    assert "Second paragraph." in result.text


def test_extract_document_pdf_with_no_text_layer_falls_back_to_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blank/scanned PDF page (empty text layer) is routed through OCR, not left empty.

    Real Tesseract/poppler binaries aren't assumed to be installed in the test
    environment, so both `convert_from_bytes` and `pytesseract.image_to_string` are
    monkeypatched — this test verifies the dispatcher's branching, not OCR quality.
    """
    monkeypatch.setattr(de, "convert_from_bytes", lambda data, first_page, last_page: [object()])
    monkeypatch.setattr(de.pytesseract, "image_to_string", lambda image: "ocr'd page text")

    result = de.extract_document("scanned.pdf", _blank_pdf_bytes(page_count=1))
    assert result.kind == "pdf"
    assert result.used_ocr is True
    assert result.text == "ocr'd page text"
    assert result.page_count == 1


def test_extract_document_image_always_uses_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """An image attachment always goes through OCR (the caller only sends images here
    when the active model has no vision support).
    """
    from PIL import Image

    monkeypatch.setattr(de.pytesseract, "image_to_string", lambda image: "text from the photo")

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color="white").save(buffer, format="PNG")

    result = de.extract_document("photo.png", buffer.getvalue())
    assert result.kind == "image"
    assert result.used_ocr is True
    assert result.text == "text from the photo"
