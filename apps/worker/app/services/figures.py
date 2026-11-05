"""Deterministic per-page/slide visual-content inventory (Layer 1).

Pure geometry/XML inspection: no model calls, no network. Produces figure
records with locators (page/slide, ordinal, bbox), extracted image bytes and
hashes, captions/nearby text, and — where the format allows it for free —
chart data as markdown. Failures are contained: a damaged file yields an empty
inventory rather than breaking ingestion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from app.services.types import NormalizedDocument, NormalizedSegment


@dataclass(frozen=True)
class FigureRecord:
    page: int
    ordinal: int
    kind: str  # picture | chart | drawing | scan
    image_bytes: bytes | None = None
    image_sha256: str | None = None
    image_mime: str = "image/png"
    image_original_mime: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    caption: str | None = None
    nearby_text: str = ""
    chart_markdown: str | None = None
    scanned_page: bool = False

    @property
    def figure_id(self) -> str:
        return f"fig-{self.page:04d}-{self.ordinal:02d}"


@dataclass
class FigureInventory:
    records: list[FigureRecord] = field(default_factory=list)
    page_count: int = 0
    scanned_pages: int = 0
    status: str = "ok"

    @property
    def scanned_page_ratio(self) -> float:
        return self.scanned_pages / self.page_count if self.page_count else 0.0


_MIN_CHARS_FOR_TEXT_PAGE = 50
_SCAN_AREA_FRACTION = 0.6
_DRAWING_MIN_PATHS = 24
_PPTX_DRAWING_SHAPES = 8
_MAX_IMAGE_EDGE = 2048

_ALLOWED_VISION_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_UNCONVERTIBLE_MIMES = {"image/svg+xml", "image/emf", "image/x-emf", "image/wmf", "image/x-wmf"}
_EXTENSION_MIMES = {
    "png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg",
    "webp": "image/webp", "gif": "image/gif",
}


def canonical_mime(extension: str) -> str | None:
    return _EXTENSION_MIMES.get(extension.lower().lstrip("."))


def _shrink(pixmap):
    try:
        longest = max(pixmap.width, pixmap.height)
        if longest > _MAX_IMAGE_EDGE:
            factor = (longest + _MAX_IMAGE_EDGE - 1) // _MAX_IMAGE_EDGE
            pixmap.shrink(factor)
    except Exception:
        pass
    return pixmap


def _normalize_image(data: bytes | None, mime: str | None) -> tuple[bytes, str] | None:
    """Return bytes in a format every vision API accepts, or None.

    PNG/JPEG/WebP/GIF pass through untouched. Formats PyMuPDF can decode are
    re-encoded as PNG. Office metafiles and SVG have no local renderer and are
    reported as unsupported instead of being sent as unreadable bytes.
    """
    if not isinstance(data, bytes) or not data:
        return None
    if mime in _ALLOWED_VISION_MIMES:
        return data, mime
    if mime in _UNCONVERTIBLE_MIMES:
        return None
    try:
        import fitz

        pixmap = _shrink(fitz.Pixmap(stream=data))
        return pixmap.tobytes("png"), "image/png"
    except Exception:
        return None


def _render_rect_png(page, rect) -> tuple[bytes, str] | None:
    """Fallback for exotic codecs (JBIG2/CCITT/JPX): render what the page shows."""
    try:
        import fitz

        pixmap = page.get_pixmap(clip=fitz.Rect(rect), matrix=fitz.Matrix(2, 2))
        return _shrink(pixmap).tobytes("png"), "image/png"
    except Exception:
        return None


def extract_figures(
    raw_bytes: bytes, filename: str, *, max_figures: int = 40,
    min_area_fraction: float = 0.02,
) -> FigureInventory:
    """Inventory visual content; never raises on malformed input."""
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    try:
        if suffix == "pdf":
            inventory = _pdf_inventory(raw_bytes, min_area_fraction=min_area_fraction)
        elif suffix == "pptx":
            inventory = _pptx_inventory(raw_bytes, min_area_fraction=min_area_fraction)
        else:
            return FigureInventory(status="unsupported_format")
    except Exception:
        return FigureInventory(status="extraction_failed")
    if len(inventory.records) > max_figures:
        inventory.records = inventory.records[:max_figures]
        if inventory.status == "ok":
            inventory.status = "truncated"
    return inventory


def _image_hash(data: bytes) -> str:
    return sha256(data).hexdigest()


def _nearest_caption(blocks: list[tuple], rect: tuple[float, float, float, float], page_height: float) -> str | None:
    x0, y0, x1, y1 = rect
    below = [b for b in blocks if b[1] >= y1 - 2 and b[1] - y1 <= page_height * 0.18]
    above = [b for b in blocks if b[3] <= y0 + 2 and y0 - b[3] <= page_height * 0.10]
    candidate = min(below, key=lambda b: b[1]) if below else (max(above, key=lambda b: b[3]) if above else None)
    if candidate is None:
        return None
    text = " ".join(str(candidate[4]).split())
    return text[:300] or None


def _pdf_inventory(raw_bytes: bytes, *, min_area_fraction: float) -> FigureInventory:
    import fitz

    inventory = FigureInventory()
    with fitz.open(stream=raw_bytes, filetype="pdf") as document:
        inventory.page_count = document.page_count
        for page_number, page in enumerate(document, start=1):
            page_area = max(page.rect.width * page.rect.height, 1.0)
            blocks = [b for b in page.get_text("blocks") if len(b) >= 5]
            text_blocks = [b for b in blocks if b[6] == 0 and str(b[4]).strip()]
            text_chars = sum(len(str(b[4])) for b in text_blocks)
            page_text = " ".join(" ".join(str(b[4]).split()) for b in text_blocks)[:600]
            ordinal = 0
            largest_area = 0.0
            seen_hashes: set[str] = set()
            for info in page.get_images(full=True):
                xref = info[0]
                for rect in page.get_image_rects(xref):
                    area = max(rect.width, 0) * max(rect.height, 0)
                    largest_area = max(largest_area, area)
                    if area / page_area < min_area_fraction:
                        continue
                    try:
                        extracted = document.extract_image(xref)
                    except Exception:
                        extracted = None
                    raw_data = extracted.get("image") if isinstance(extracted, dict) else None
                    original_mime = canonical_mime(str(extracted.get("ext", ""))) if isinstance(extracted, dict) else None
                    normalized = _normalize_image(raw_data, original_mime)
                    if normalized is None:
                        normalized = _render_rect_png(page, rect)
                    if normalized is None:
                        continue
                    data, mime = normalized
                    digest = _image_hash(data)
                    ordinal += 1
                    if digest in seen_hashes:
                        continue
                    seen_hashes.add(digest)
                    bbox = (rect.x0, rect.y0, rect.x1, rect.y1)
                    inventory.records.append(FigureRecord(
                        page=page_number, ordinal=ordinal, kind="picture",
                        image_bytes=data, image_sha256=digest, image_mime=mime,
                        image_original_mime=original_mime,
                        bbox=bbox, caption=_nearest_caption(text_blocks, bbox, page.rect.height),
                        nearby_text=page_text,
                    ))
            scanned = largest_area / page_area >= _SCAN_AREA_FRACTION and text_chars < _MIN_CHARS_FOR_TEXT_PAGE
            if scanned:
                inventory.scanned_pages += 1
            drawings = page.get_drawings()
            if len(drawings) >= _DRAWING_MIN_PATHS:
                try:
                    clip = fitz.Rect(drawings[0]["rect"])
                    for path in drawings[1:]:
                        clip |= fitz.Rect(path["rect"])
                    if clip.width * clip.height / page_area >= min_area_fraction:
                        pixmap = page.get_pixmap(clip=clip, matrix=fitz.Matrix(2, 2))
                        data = pixmap.tobytes("png")
                        digest = _image_hash(data)
                        if digest not in seen_hashes:
                            seen_hashes.add(digest)
                            ordinal += 1
                            bbox = (clip.x0, clip.y0, clip.x1, clip.y1)
                            inventory.records.append(FigureRecord(
                                page=page_number, ordinal=ordinal, kind="drawing",
                                image_bytes=data, image_sha256=digest, image_mime="image/png",
                                bbox=bbox, caption=_nearest_caption(text_blocks, bbox, page.rect.height),
                                nearby_text=page_text,
                            ))
                except Exception:
                    pass
            if scanned and not any(record.page == page_number for record in inventory.records):
                inventory.records.append(FigureRecord(
                    page=page_number, ordinal=1, kind="scan", scanned_page=True,
                    nearby_text=page_text,
                ))
    return inventory


def _pptx_chart_markdown(chart: Any) -> str | None:
    try:
        plot = chart.plots[0]
        categories = [str(category) for category in plot.categories]
        series = []
        for entry in chart.series:
            series.append((str(entry.name or "Series"), ["" if value is None else str(value) for value in entry.values]))
        if not categories or not series:
            return None
        header = "| Category | " + " | ".join(name for name, _ in series) + " |"
        divider = "|---" * (len(series) + 1) + "|"
        rows = [
            f"| {categories[index] if index < len(categories) else ''} | "
            + " | ".join(values[index] if index < len(values) else "" for _, values in series) + " |"
            for index in range(len(categories))
        ]
        return "\n".join([header, divider, *rows])
    except Exception:
        return None


def _pptx_inventory(raw_bytes: bytes, *, min_area_fraction: float) -> FigureInventory:
    import io

    from pptx import Presentation

    inventory = FigureInventory()
    presentation = Presentation(io.BytesIO(raw_bytes))
    inventory.page_count = len(presentation.slides)
    for slide_number, slide in enumerate(presentation.slides, start=1):
        slide_text = " ".join(
            shape.text.strip() for shape in slide.shapes
            if getattr(shape, "has_text_frame", False) and shape.text.strip()
        )[:600]
        drawing_shapes = 0
        picture_records: list[FigureRecord] = []
        chart_records: list[FigureRecord] = []

        def walk(shapes) -> None:
            nonlocal drawing_shapes
            for shape in shapes:
                shape_type = getattr(shape, "shape_type", None)
                type_name = str(shape_type).split(" ", 1)[0] if shape_type is not None else ""
                if getattr(shape, "shapes", None) is not None:
                    walk(shape.shapes)
                    continue
                if getattr(shape, "has_chart", False):
                    markdown = _pptx_chart_markdown(shape.chart)
                    if markdown:
                        chart_records.append(FigureRecord(
                            page=slide_number, ordinal=0, kind="chart",
                            caption=slide_text[:300] or None, nearby_text=slide_text,
                            chart_markdown=markdown,
                        ))
                    continue
                if type_name == "PICTURE":
                    try:
                        image = shape.image
                        original_mime = image.content_type or None
                        normalized = _normalize_image(image.blob, original_mime)
                        if normalized is None:
                            # EMF/WMF/SVG metafiles have no local renderer: keep
                            # the record so the card reports the loss explicitly.
                            picture_records.append(FigureRecord(
                                page=slide_number, ordinal=0, kind="picture",
                                image_original_mime=original_mime,
                                caption=slide_text[:300] or None, nearby_text=slide_text,
                            ))
                            continue
                        data, mime = normalized
                        digest = _image_hash(data)
                        picture_records.append(FigureRecord(
                            page=slide_number, ordinal=0, kind="picture",
                            image_bytes=data, image_sha256=digest, image_mime=mime,
                            image_original_mime=original_mime,
                            bbox=(float(shape.left), float(shape.top), float(shape.left + shape.width), float(shape.top + shape.height)),
                            caption=slide_text[:300] or None, nearby_text=slide_text,
                        ))
                    except Exception:
                        continue
                    continue
                if type_name in {"AUTO_SHAPE", "LINE", "FREEFORM", "CONNECTOR"}:
                    drawing_shapes += 1

        walk(slide.shapes)
        page_area_emu = 9144000 * 6858000
        page_records = [record for record in (*picture_records, *chart_records)
                        if record.bbox is None
                        or ((record.bbox[2] - record.bbox[0]) * (record.bbox[3] - record.bbox[1]) / page_area_emu
                            >= min_area_fraction)]
        if drawing_shapes >= _PPTX_DRAWING_SHAPES:
            page_records.append(FigureRecord(
                page=slide_number, ordinal=0, kind="drawing",
                caption=slide_text[:300] or None, nearby_text=slide_text,
            ))
        for ordinal, record in enumerate(page_records, start=1):
            inventory.records.append(FigureRecord(
                page=record.page, ordinal=ordinal, kind=record.kind,
                image_bytes=record.image_bytes, image_sha256=record.image_sha256,
                image_mime=record.image_mime, image_original_mime=record.image_original_mime,
                bbox=record.bbox, caption=record.caption,
                nearby_text=record.nearby_text, chart_markdown=record.chart_markdown,
            ))
    return inventory


def scanned_page_ratio(raw_bytes: bytes, filename: str, *, max_pages: int | None = None) -> float | None:
    """Fraction of pages that are full-page images with almost no text.

    Cheap pre-check for OCR routing: geometry only, no image bytes extracted.
    With ``max_pages``, evenly spaced pages give a deterministic estimate for
    large PDFs; callers must label that result as sampled.
    Returns None when the format or file cannot be assessed.
    """
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be positive")
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix != "pdf":
        return None
    try:
        import fitz

        with fitz.open(stream=raw_bytes, filetype="pdf") as document:
            if not document.page_count:
                return None
            if max_pages is None or document.page_count <= max_pages:
                page_indices = range(document.page_count)
            elif max_pages == 1:
                page_indices = [(document.page_count - 1) // 2]
            else:
                page_indices = sorted({round(i * (document.page_count - 1) / (max_pages - 1))
                                       for i in range(max_pages)})
            scanned = 0
            for index in page_indices:
                page = document[index]
                page_area = max(page.rect.width * page.rect.height, 1.0)
                text_chars = sum(len(str(block[4])) for block in page.get_text("blocks")
                                 if len(block) >= 5 and block[6] == 0)
                largest = 0.0
                for info in page.get_images(full=True):
                    for rect in page.get_image_rects(info[0]):
                        largest = max(largest, max(rect.width, 0) * max(rect.height, 0))
                if largest / page_area >= _SCAN_AREA_FRACTION and text_chars < _MIN_CHARS_FOR_TEXT_PAGE:
                    scanned += 1
            return scanned / len(page_indices)
    except Exception:
        return None


def insert_figure_segments(
    document: NormalizedDocument, records: list[FigureRecord], card_texts: dict[str, str],
) -> int:
    """Insert figure cards after the last segment of their page/slide."""
    inserted = 0
    for record in sorted(records, key=lambda item: (item.page, item.ordinal)):
        card = card_texts.get(record.figure_id)
        if not card:
            continue
        segment = NormalizedSegment(
            segment_type="figure_card" if record.kind != "chart" else "figure_chart",
            segment_number=record.page,
            title=record.caption,
            text=card,
            metadata={
                "structure_kind": "figure",
                "contains_structure": True,
                "figure": {
                    "figure_id": record.figure_id,
                    "kind": record.kind,
                    "page": record.page,
                    "ordinal": record.ordinal,
                    "bbox": list(record.bbox) if record.bbox else None,
                    "image_sha256": record.image_sha256,
                    "caption": record.caption,
                },
            },
        )
        position = len(document.segments)
        for index, existing in enumerate(document.segments):
            if existing.segment_number == record.page:
                position = index + 1
        document.segments.insert(position, segment)
        inserted += 1
    return inserted


def render_card(record: FigureRecord, understanding: Any | None, *, filename: str, described: bool) -> str:
    """Stable text-card format; the [figure] prefix marks figure-derived evidence."""
    locator = f"page {record.page}" if not filename.lower().endswith(".pptx") else f"slide {record.page}"
    lines = [f"[figure] {filename} — {locator}, figure {record.ordinal} ({record.kind})"]
    if record.caption:
        lines.append(f"Caption: {record.caption}")
    if record.chart_markdown:
        lines.append(f"Data:\n{record.chart_markdown}")
    if understanding is not None:
        if understanding.verbatim_text:
            lines.append(f"Visible text: {understanding.verbatim_text}")
        if understanding.table_markdown:
            lines.append(f"Table:\n{understanding.table_markdown}")
        if understanding.description:
            lines.append(f"Description: {understanding.description}")
        if understanding.relationships:
            lines.append("Relationships: " + "; ".join(understanding.relationships))
        if understanding.uncertainty:
            lines.append("Uncertainty: " + "; ".join(understanding.uncertainty))
        if getattr(understanding, "described_by", None):
            lines.append(f"Described by: {understanding.described_by}")
    elif not record.chart_markdown:
        if record.nearby_text and not record.caption:
            lines.append(f"Nearby text: {record.nearby_text[:240]}")
        if not described:
            detail = f" ({record.image_original_mime} has no local renderer)" if record.image_original_mime else ""
            lines.append(f"[visual content not described]{detail}")
    return "\n".join(lines)
