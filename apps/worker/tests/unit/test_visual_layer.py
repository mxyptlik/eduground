"""Visual layer: detection, provider-agnostic vision, cards and enrichment."""

from __future__ import annotations

import io
import json
from decimal import Decimal
from hashlib import sha256

import pytest

from app.ledger import BudgetLedger, LedgerError, LedgerGuard, PriceRegistry
from app.services import visual as visual_module
from app.services.figures import FigureInventory, FigureRecord, extract_figures, insert_figure_segments
from app.services.types import NormalizedDocument, NormalizedSegment
from app.services.vision import (
    VISION_PROMPT_VERSION,
    VisionClient,
    VisionError,
    VisionUnderstanding,
    parse_vision_json,
)
from app.settings import settings as worker_settings


def tiny_png() -> bytes:
    import fitz

    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8))
    return pixmap.tobytes("png")


def make_guard(tmp_path, *, provider="openrouter", model="pinned/vlm", operation="visual_understanding"):
    payload = {
        "version": "1.0",
        "schedules": [{
            "provider": provider, "model": model, "operation": operation,
            "effective_from": "2026-01-01T00:00:00+00:00", "currency": "USD", "source": "fixture-only",
            "prices": [
                {"unit": "input_tokens", "amount": "0.3", "quantity": 1_000_000},
                {"unit": "output_tokens", "amount": "0.6", "quantity": 1_000_000},
            ],
        }],
    }
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return LedgerGuard(BudgetLedger(tmp_path / "budget.sqlite3", "10"), PriceRegistry.from_json(path))


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def openrouter_body(text: str, *, model="pinned/vlm", prompt_tokens=120, completion_tokens=40):
    return {
        "model": model,
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


VALID_JSON = json.dumps({
    "figure_type": "diagram",
    "verbatim_text": "FORCE = MASS x ACCELERATION",
    "table_markdown": None,
    "description": "An arrow diagram relating force to mass and acceleration.",
    "relationships": ["force depends on mass", "force depends on acceleration"],
    "uncertainty": [],
})


def test_parse_vision_json_accepts_valid_and_fenced_payloads():
    understanding = parse_vision_json(VALID_JSON)
    assert understanding.figure_type == "diagram"
    assert understanding.verbatim_text == "FORCE = MASS x ACCELERATION"
    assert len(understanding.relationships) == 2
    fenced = parse_vision_json(f"```json\n{VALID_JSON}\n```")
    assert fenced == understanding


def test_parse_vision_json_rejects_incomplete_or_wrong_typed_payloads():
    with pytest.raises(VisionError, match="missing verbatim_text"):
        parse_vision_json(json.dumps({"figure_type": "diagram", "description": "d"}))
    with pytest.raises(VisionError, match="unsupported figure_type"):
        parse_vision_json(json.dumps({"figure_type": "photograph", "verbatim_text": "v",
                                      "description": "d"}))
    with pytest.raises(VisionError, match="list of strings"):
        parse_vision_json(json.dumps({"figure_type": "diagram", "verbatim_text": "v",
                                      "description": "d", "relationships": 42}))
    with pytest.raises(VisionError, match="valid JSON"):
        parse_vision_json("not json at all")


def test_vision_client_cache_makes_identical_images_free(tmp_path, monkeypatch):
    guard = make_guard(tmp_path)
    calls = {"n": 0}

    def opener(req, timeout):
        calls["n"] += 1
        return FakeResponse(openrouter_body(VALID_JSON))

    client = VisionClient(provider="openrouter", model="pinned/vlm", api_key="k", guard=guard,
                          cache_dir=tmp_path / "cache", opener=opener)
    image = tiny_png()
    first = client.describe(image, mime="image/png")
    second = client.describe(image, mime="image/png")
    assert first == second and calls["n"] == 1, "second identical figure must come from cache"
    assert (tmp_path / "cache" / f"{client.cache_key(image)}.json").is_file()
    state = guard.ledger.state()
    assert state.settled_usd > 0 and state.unknown_attempts == 0
    # The cached file records the pinned provider/model/prompt versions.
    payload = json.loads((tmp_path / "cache" / f"{client.cache_key(image)}.json").read_text(encoding="utf-8"))
    assert payload["provider"] == "openrouter" and payload["model"] == "pinned/vlm"
    assert payload["prompt_version"] == VISION_PROMPT_VERSION


def test_vision_client_rejects_model_drift_and_blocks_ledger(tmp_path):
    guard = make_guard(tmp_path)
    client = VisionClient(provider="openrouter", model="pinned/vlm", api_key="k", guard=guard,
                          cache_dir=tmp_path / "cache",
                          opener=lambda req, timeout: FakeResponse(openrouter_body(VALID_JSON, model="other/vlm")))
    with pytest.raises(LedgerError, match="not the pinned"):
        client.describe(tiny_png(), mime="image/png")
    state = guard.ledger.state()
    assert state.unknown_attempts == 1 and state.settled_usd == 0


def test_vision_client_malformed_content_is_a_failure_not_a_partial_card(tmp_path):
    guard = make_guard(tmp_path)
    client = VisionClient(provider="openrouter", model="pinned/vlm", api_key="k", guard=guard,
                          cache_dir=tmp_path / "cache",
                          opener=lambda req, timeout: FakeResponse(openrouter_body("not json")))
    with pytest.raises(VisionError):
        client.describe(tiny_png(), mime="image/png")
    state = guard.ledger.state()
    assert state.unknown_attempts == 0, "usage was verified, so the call settles as known"
    assert state.settled_usd > 0


def test_pptx_inventory_finds_pictures_and_charts_but_not_tables():
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    textbox = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1))
    textbox.text_frame.text = "Lecture introduction text about circuits"
    png = tiny_png()
    slide.shapes.add_picture(io.BytesIO(png), Inches(1), Inches(2), Inches(1), Inches(1))
    slide.shapes.add_table(2, 2, Inches(1), Inches(3), Inches(2), Inches(1))
    chart_data = CategoryChartData()
    chart_data.categories = ["Alpha", "Beta"]
    chart_data.add_series("Series 1", (1.0, 2.0))
    slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(4), Inches(2), Inches(3), Inches(2), chart_data)
    buffer = io.BytesIO()
    presentation.save(buffer)

    inventory = extract_figures(buffer.getvalue(), "lecture.pptx", max_figures=10, min_area_fraction=0.0)

    kinds = sorted(record.kind for record in inventory.records)
    assert kinds == ["chart", "picture"]
    picture = next(record for record in inventory.records if record.kind == "picture")
    assert picture.page == 1 and picture.image_sha256 == sha256(png).hexdigest()
    chart = next(record for record in inventory.records if record.kind == "chart")
    assert "Series 1" in chart.chart_markdown and "Alpha" in chart.chart_markdown


def test_pdf_inventory_finds_images_and_flags_scanned_pages():
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "A lecture page with more than fifty characters of real text content.")
    page.insert_image(fitz.Rect(100, 200, 300, 400), stream=tiny_png())
    scan_page = doc.new_page()
    scan_page.insert_image(fitz.Rect(0, 0, scan_page.rect.width, scan_page.rect.height), stream=tiny_png())
    raw = doc.tobytes()

    inventory = extract_figures(raw, "lecture.pdf", max_figures=10)

    assert inventory.page_count == 2
    page_one = [record for record in inventory.records if record.page == 1]
    assert any(record.kind == "picture" for record in page_one)
    assert inventory.scanned_pages >= 1
    assert any(record.kind == "scan" for record in [*inventory.records] if record.page == 2) or \
        any(record.kind == "picture" and record.page == 2 for record in inventory.records)


def _document() -> NormalizedDocument:
    return NormalizedDocument(text="", segments=[
        NormalizedSegment("content", 1, None, "page one text", {"structure_kind": "paragraph"}),
        NormalizedSegment("content", 2, None, "page two text", {"structure_kind": "paragraph"}),
    ], metadata={})


def _record(page: int, ordinal: int, kind: str, *, image: bytes | None = b"img", chart: str | None = None):
    return FigureRecord(
        page=page, ordinal=ordinal, kind=kind, image_bytes=image,
        image_sha256=sha256(image).hexdigest() if image else None,
        nearby_text="nearby context", chart_markdown=chart,
    )


def test_enrichment_off_by_default_changes_nothing(monkeypatch):
    monkeypatch.setattr(worker_settings, "visual_enabled", False)
    document, summary = visual_module.enrich_with_figures(
        _document(), b"raw", "lecture.pdf", settings=worker_settings, guard=None, vision=None)
    assert summary == {"status": "disabled"}
    assert len(document.segments) == 2


def test_enrichment_inserts_cards_after_their_page_without_vision(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_settings, "visual_enabled", True)
    inventory = FigureInventory(records=[
        _record(1, 1, "picture"), _record(2, 1, "drawing", image=None)], page_count=2)
    monkeypatch.setattr(visual_module, "extract_figures", lambda *args, **kwargs: inventory)

    document, summary = visual_module.enrich_with_figures(
        _document(), b"raw", "lecture.pdf", settings=worker_settings, guard=None, vision=None)

    assert summary["figures"] == 2 and summary["not_described"] == 2
    assert summary["inserted_segments"] == 2
    assert [segment.segment_number for segment in document.segments] == [1, 1, 2, 2]
    card = document.segments[1]
    assert card.segment_type == "figure_card" and card.metadata["structure_kind"] == "figure"
    assert "figure 1 (picture)" in card.text and "[visual content not described]" in card.text
    assert card.metadata["figure"]["image_sha256"]


def test_enrichment_deduplicates_identical_images(monkeypatch):
    monkeypatch.setattr(worker_settings, "visual_enabled", True)
    same = b"same-bytes"
    inventory = FigureInventory(records=[
        _record(1, 1, "picture", image=same), _record(1, 2, "picture", image=same)], page_count=1)
    monkeypatch.setattr(visual_module, "extract_figures", lambda *args, **kwargs: inventory)
    document, summary = visual_module.enrich_with_figures(
        _document(), b"raw", "lecture.pdf", settings=worker_settings, guard=None, vision=None)
    assert summary["deduped"] == 1 and summary["inserted_segments"] == 1


class FakeVision:
    def __init__(self):
        self.batch_calls = 0

    def describe(self, image, *, mime, context=""):
        return VisionUnderstanding("diagram", "FORCE = MASS x ACCELERATION", None,
                                   "Arrow diagram relating force, mass and acceleration.",
                                   ("force depends on mass",), ())

    def describe_many(self, items):
        self.batch_calls += 1
        return [self.describe(item["image"], mime=item["mime"], context=item.get("context", ""))
                for item in items]


def test_enrichment_uses_vision_descriptions_when_available(monkeypatch):
    monkeypatch.setattr(worker_settings, "visual_enabled", True)
    inventory = FigureInventory(records=[_record(1, 1, "picture"), _record(1, 2, "chart", image=None,
                                                                chart="| A | B |\n|---|---|\n| 1 | 2 |")], page_count=1)
    monkeypatch.setattr(visual_module, "extract_figures", lambda *args, **kwargs: inventory)
    document, summary = visual_module.enrich_with_figures(
        _document(), b"raw", "lecture.pdf", settings=worker_settings, guard=None, vision=FakeVision())
    assert summary["described"] == 2 and summary["not_described"] == 0
    described_card = next(segment for segment in document.segments if segment.segment_type == "figure_card")
    assert "Visible text: FORCE = MASS x ACCELERATION" in described_card.text
    assert "Description: Arrow diagram" in described_card.text
    chart_card = next(segment for segment in document.segments if segment.segment_type == "figure_chart")
    assert "| A | B |" in chart_card.text


def test_enrichment_contains_vision_failures_and_oversize_images(monkeypatch):
    monkeypatch.setattr(worker_settings, "visual_enabled", True)
    monkeypatch.setattr(worker_settings, "visual_max_image_bytes", 2)

    class BrokenVision:
        def describe(self, image, *, mime, context=""):
            raise VisionError("provider exploded")

    inventory = FigureInventory(records=[_record(1, 1, "picture")], page_count=1)
    monkeypatch.setattr(visual_module, "extract_figures", lambda *args, **kwargs: inventory)
    document, summary = visual_module.enrich_with_figures(
        _document(), b"raw", "lecture.pdf", settings=worker_settings, guard=None, vision=BrokenVision())
    assert summary["oversize"] == 1 and summary["not_described"] == 1
    assert "[visual content not described]" in document.segments[1].text


def test_image_format_normalization_passthrough_and_unsupported():
    import fitz

    from app.services import figures as figures_module

    png = tiny_png()
    assert figures_module._normalize_image(png, "image/png") == (png, "image/png")
    jpeg = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8)).tobytes("jpg")
    assert figures_module._normalize_image(jpeg, "image/jpeg") == (jpeg, "image/jpeg")
    assert figures_module._normalize_image(b"<svg/>", "image/svg+xml") is None
    assert figures_module._normalize_image(b"emf-bytes", "image/x-emf") is None
    assert figures_module._normalize_image(b"not an image", None) is None
    assert figures_module.canonical_mime("JPEG") == "image/jpeg"
    assert figures_module.canonical_mime("jpx") is None


def test_pdf_jpeg_image_keeps_its_mime_and_exotic_codec_falls_back_to_rendering(monkeypatch):
    import fitz

    from app.services import figures as figures_module

    jpeg = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8)).tobytes("jpg")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(fitz.Rect(50, 50, 150, 150), stream=jpeg)
    raw = doc.tobytes()

    inventory = extract_figures(raw, "scan.pdf", min_area_fraction=0.0)
    record = inventory.records[0]
    assert record.image_mime == "image/jpeg" and record.image_original_mime == "image/jpeg"

    # Simulate an unreadable embedded codec (JBIG2/CCITT/JPX): the page render
    # fallback still produces vision-readable PNG bytes.
    monkeypatch.setattr(figures_module, "_normalize_image", lambda data, mime: None)
    fallback = extract_figures(raw, "scan.pdf", min_area_fraction=0.0)
    rendered = fallback.records[0]
    assert rendered.image_mime == "image/png"
    assert rendered.image_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_unsupported_embedded_format_is_reported_not_silently_lost(monkeypatch):
    monkeypatch.setattr(worker_settings, "visual_enabled", True)
    inventory = FigureInventory(records=[
        FigureRecord(page=1, ordinal=1, kind="picture", image_original_mime="image/x-emf")], page_count=1)
    monkeypatch.setattr(visual_module, "extract_figures", lambda *args, **kwargs: inventory)
    document, summary = visual_module.enrich_with_figures(
        _document(), b"raw", "lecture.pptx", settings=worker_settings, guard=None, vision=None)
    assert summary["unsupported_format"] == 1 and summary["not_described"] == 1
    card = document.segments[1].text
    assert "[visual content not described]" in card and "image/x-emf" in card


def test_vision_batch_request_carries_system_prompt_and_images_and_caches_each(tmp_path):
    guard = make_guard(tmp_path)
    captured = {}

    def opener(req, timeout):
        captured["body"] = json.loads(req.data)
        return FakeResponse({
            "model": "pinned/vlm",
            "choices": [{"message": {"content": json.dumps({"figures": [json.loads(VALID_JSON), json.loads(VALID_JSON)]})}}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 120},
        })

    client = VisionClient(provider="openrouter", model="pinned/vlm", api_key="k", guard=guard,
                          cache_dir=tmp_path / "cache", batch_size=2, opener=opener)
    images = [tiny_png(), b"second-image-bytes"]
    results = client.describe_many([
        {"image": images[0], "mime": "image/png", "context": "slide 1"},
        {"image": images[1], "mime": "image/jpeg", "context": "slide 2"},
    ])
    assert len(results) == 2 and all(not isinstance(r, VisionError) for r in results)
    body = captured["body"]
    assert body["messages"][0]["role"] == "system"
    assert "never invent" in body["messages"][0]["content"]
    parts = body["messages"][1]["content"]
    assert parts[0]["type"] == "text"
    assert sum(1 for part in parts if part["type"] == "image_url") == 2
    assert (tmp_path / "cache" / f"{client.cache_key(images[0])}.json").is_file()
    assert (tmp_path / "cache" / f"{client.cache_key(images[1])}.json").is_file()

    calls = {"n": 0}

    def opener_again(req, timeout):  # pragma: no cover - must not be invoked
        calls["n"] += 1
        return FakeResponse({})

    cached_client = VisionClient(provider="openrouter", model="pinned/vlm", api_key="k", guard=guard,
                                 cache_dir=tmp_path / "cache", batch_size=2, opener=opener_again)
    again = cached_client.describe_many([
        {"image": images[0], "mime": "image/png", "context": ""},
        {"image": images[1], "mime": "image/jpeg", "context": ""},
    ])
    assert calls["n"] == 0 and len(again) == 2


def test_vision_batch_count_mismatch_fails_all_figures_and_settles_known(tmp_path):
    guard = make_guard(tmp_path)

    def opener(req, timeout):
        return FakeResponse({
            "model": "pinned/vlm",
            "choices": [{"message": {"content": json.dumps({"figures": [json.loads(VALID_JSON)]})}}],
            "usage": {"prompt_tokens": 300, "completion_tokens": 60},
        })

    client = VisionClient(provider="openrouter", model="pinned/vlm", api_key="k", guard=guard,
                          cache_dir=tmp_path / "cache", batch_size=2, opener=opener)
    results = client.describe_many([{"image": b"one", "mime": "image/png", "context": ""},
                                    {"image": b"two", "mime": "image/png", "context": ""}])
    assert len(results) == 2 and all(isinstance(r, VisionError) for r in results)
    assert not list((tmp_path / "cache").glob("*.json"))
    state = guard.ledger.state()
    assert state.unknown_attempts == 0 and state.settled_usd > 0


def test_gemini_batch_request_uses_system_instruction_and_inline_images(tmp_path):
    guard = make_guard(tmp_path, provider="gemini")
    captured = {}

    def opener(req, timeout):
        captured["body"] = json.loads(req.data)
        return FakeResponse({
            "modelVersion": "pinned/vlm",
            "candidates": [{"content": {"parts": [{"text": json.dumps(
                {"figures": [json.loads(VALID_JSON), json.loads(VALID_JSON)]})}]}}],
            "usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 80, "thoughtsTokenCount": 0},
        })

    client = VisionClient(provider="gemini", model="pinned/vlm", api_key="k", guard=guard,
                          cache_dir=tmp_path / "cache", batch_size=2, opener=opener,
                          base_url="https://generativelanguage.googleapis.com/v1beta")
    results = client.describe_many([{"image": tiny_png(), "mime": "image/png", "context": ""},
                                    {"image": b"img-two", "mime": "image/jpeg", "context": ""}])
    assert len(results) == 2 and all(not isinstance(r, VisionError) for r in results)
    body = captured["body"]
    assert "never invent" in body["systemInstruction"]["parts"][0]["text"]
    assert sum(1 for part in body["contents"][0]["parts"] if "inline_data" in part) == 2
    assert body["generationConfig"]["responseSchema"]["properties"]["figures"]["type"] == "array"


def test_enrichment_batches_all_vision_items_in_one_pass(monkeypatch):
    monkeypatch.setattr(worker_settings, "visual_enabled", True)
    inventory = FigureInventory(records=[
        _record(1, 1, "picture", image=b"first-image"), _record(1, 2, "picture", image=b"second-image")],
        page_count=1)
    monkeypatch.setattr(visual_module, "extract_figures", lambda *args, **kwargs: inventory)
    vision = FakeVision()
    document, summary = visual_module.enrich_with_figures(
        _document(), b"raw", "lecture.pdf", settings=worker_settings, guard=None, vision=vision)
    assert vision.batch_calls == 1 and summary["described"] == 2


def test_model_output_validator_rejects_every_non_compliant_shape():
    from app.services.model_output import ModelOutputError, validate_model_json

    ok = validate_model_json('{"a": 1, "b": "x"}', required_keys=("a", "b"))
    assert ok == {"a": 1, "b": "x"}
    assert validate_model_json('```json\n{"a": 1}\n```', required_keys=("a",))["a"] == 1
    assert validate_model_json('[{"a": 1}]', expect="array")[0]["a"] == 1

    bad_inputs = [
        ("", "empty", ("a",)),
        ("   \n\t", "empty", ("a",)),
        ("```json\n```", "empty after removing code fences", ("a",)),
        ("not json at all", "not valid JSON", ("a",)),
        ('{"a": NaN}', "non-finite JSON constant", ("a",)),
        ('{"a": 1, "a": 2}', "repeats JSON key", ("a",)),
        ('[1, 2]', "must be a JSON object", ("a",)),
        ('{}', "empty JSON object", ("a",)),
        ('{"a": 1}', "missing required keys: b", ("a", "b")),
        ('{"a": "   "}', "empty required keys: a", ("a",)),
    ]
    for payload, match, required in bad_inputs:
        with pytest.raises(ModelOutputError, match=match):
            validate_model_json(payload, required_keys=required)
    with pytest.raises(ModelOutputError, match="only apply to JSON objects"):
        validate_model_json('[1]', expect="array", required_keys=("a",))


def test_vision_empty_response_body_is_rejected_after_a_known_settlement(tmp_path):
    guard = make_guard(tmp_path)
    client = VisionClient(provider="openrouter", model="pinned/vlm", api_key="k", guard=guard,
                          cache_dir=tmp_path / "cache",
                          opener=lambda req, timeout: FakeResponse({
                              "model": "pinned/vlm",
                              "choices": [{"message": {"content": "   "}}],
                              "usage": {"prompt_tokens": 10, "completion_tokens": 1},
                          }))
    with pytest.raises(VisionError, match="no text content"):
        client.describe(tiny_png(), mime="image/png")
    state = guard.ledger.state()
    assert state.unknown_attempts == 0 and state.settled_usd > 0
    assert not list((tmp_path / "cache").glob("*.json"))


def test_fallback_vision_engages_only_for_provider_failures():
    from app.ledger import LedgerError
    from app.services.vision import FallbackVisionClient

    class Stub:
        def __init__(self, outcomes):
            self.outcomes = list(outcomes)
            self.calls = 0

        def describe(self, image, *, mime, context=""):
            self.calls += 1
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        def describe_many(self, items):
            results = []
            for item in items:
                try:
                    results.append(self.describe(item["image"], mime=item["mime"],
                                                 context=item.get("context", "")))
                except Exception as exc:  # mirrors the real per-figure contract
                    results.append(exc)
            return results

    good = VisionUnderstanding("diagram", "TEXT", None, "desc", (), (), "openrouter/fallback-model")
    primary = Stub([VisionError("provider down"), good])
    fallback = Stub([good])
    client = FallbackVisionClient(primary, fallback)
    results = client.describe_many([{"image": b"one", "mime": "image/png"},
                                    {"image": b"two", "mime": "image/png"}])
    assert not any(isinstance(r, VisionError) for r in results)
    assert fallback.calls == 1 and primary.calls == 2  # both items hit primary; one failed over

    blocked_primary = Stub([LedgerError("unreconciled usage blocks new calls")])
    fallback_unused = Stub([good])
    client2 = FallbackVisionClient(blocked_primary, fallback_unused)
    try:
        client2.describe(b"one", mime="image/png")
        raise AssertionError("ledger errors must not fall back")
    except LedgerError:
        pass
    assert fallback_unused.calls == 0


def test_pipeline_public_path_includes_figure_cards(monkeypatch, markdown_source_bytes: bytes):
    """Layer 4 through the real pipeline entry point, not just the helper."""
    from app.pipelines import ingestion_pipeline as pipeline
    from app.services.figures import FigureInventory, FigureRecord

    monkeypatch.setattr(worker_settings, "visual_enabled", True)
    inventory = FigureInventory(records=[
        FigureRecord(page=1, ordinal=1, kind="drawing", nearby_text="box A -> box B")], page_count=1)
    monkeypatch.setattr(visual_module, "extract_figures", lambda *args, **kwargs: inventory)
    monkeypatch.setattr(pipeline, "embed_texts", lambda texts, **kwargs: [[0.1, 0.2] for _ in texts])
    monkeypatch.setattr(pipeline, "ensure_collection", lambda size: None)
    monkeypatch.setattr(pipeline, "upsert_points", lambda points: None)

    result = pipeline.run_ingestion_pipeline(
        markdown_source_bytes, "lecture.md", notebook_id="n1", institution_id="i1",
        source_id="s1", source_version_id="v1", mime_type="text/markdown")

    assert any("[figure]" in chunk.text for chunk in result["chunks"]), result["chunks"]
    assert any("[visual content not described]" in chunk.text for chunk in result["chunks"])


def test_scanned_page_ratio_flags_scan_only_documents():
    import fitz

    from app.services.figures import scanned_page_ratio

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "A digital page with plenty of extractable text for comparison.")
    digital = doc.tobytes()
    scan_doc = fitz.open()
    for _ in range(2):
        scan_page = scan_doc.new_page()
        scan_page.insert_image(fitz.Rect(0, 0, scan_page.rect.width, scan_page.rect.height), stream=tiny_png())
    scans = scan_doc.tobytes()

    assert scanned_page_ratio(scans, "notes.pdf") == 1.0
    assert scanned_page_ratio(scans, "notes.pdf", max_pages=1) == 1.0
    assert scanned_page_ratio(digital, "notes.pdf") == 0.0
    assert scanned_page_ratio(b"raw", "notes.pptx") is None


def test_insert_figure_segments_keeps_locator_metadata():
    document = _document()
    records = [_record(2, 1, "picture")]
    insert_figure_segments(document, records, {records[0].figure_id: "card text"})
    assert len(document.segments) == 3
    figure = document.segments[2]
    assert figure.segment_number == 2
    assert figure.metadata["figure"]["page"] == 2
