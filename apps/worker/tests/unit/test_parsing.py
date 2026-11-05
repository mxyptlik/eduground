from __future__ import annotations

from app.services.parsing import parse_source_bytes


def test_parse_markdown_preserves_heading_paths_and_structure() -> None:
    document = parse_source_bytes(
        b"""# Week 1

## Cells

- membrane
- nucleus
""",
        "week1.md",
    )

    heading_segment = next(segment for segment in document.segments if segment.segment_type == "heading")
    list_segment = next(segment for segment in document.segments if segment.metadata["structure_kind"] == "list")

    assert heading_segment.text == "Week 1"
    assert list_segment.metadata["contains_structure"] is True
    assert list_segment.metadata["heading_path"] == ["Week 1", "Cells"]


def test_parse_text_source_marks_plain_text_extraction() -> None:
    document = parse_source_bytes(b"plain text body", "notes.txt")

    assert document.text == "plain text body"
    assert document.metadata["extraction_method"] == "plain-text"
    assert document.metadata["requires_fallback_ocr"] is False


def test_image_only_pdf_requests_ocr_despite_generated_page_markers() -> None:
    import fitz

    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8)).tobytes("png")
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_image(page.rect, stream=image)

    document = parse_source_bytes(pdf.tobytes(), "scanned.pdf")

    assert document.metadata["extraction_method"] in {"pdfplumber", "pymupdf", "pypdf"}
    assert document.metadata["scanned_page_ratio"] == 1.0
    assert document.metadata["requires_fallback_ocr"] is True
