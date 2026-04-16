from __future__ import annotations

from app.services.database import _build_qdrant_point_id, _normalize_psycopg_conninfo, _normalize_segment_type, _sanitize_json_value, _strip_nul_bytes


def test_normalize_psycopg_conninfo_removes_sqlalchemy_driver_suffix() -> None:
    assert (
        _normalize_psycopg_conninfo("postgresql+psycopg://eduground:eduground@localhost:5432/eduground")
        == "postgresql://eduground:eduground@localhost:5432/eduground"
    )


def test_normalize_psycopg_conninfo_leaves_plain_postgres_url_unchanged() -> None:
    assert (
        _normalize_psycopg_conninfo("postgresql://eduground:eduground@localhost:5432/eduground")
        == "postgresql://eduground:eduground@localhost:5432/eduground"
    )


def test_normalize_segment_type_uses_database_enum_names() -> None:
    assert _normalize_segment_type("section") == "SECTION"
    assert _normalize_segment_type("page") == "PAGE"
    assert _normalize_segment_type("unknown") == "SECTION"


def test_strip_nul_bytes_removes_invalid_postgres_characters() -> None:
    assert _strip_nul_bytes("ab\x00cd") == "abcd"
    assert _strip_nul_bytes(None) is None


def test_sanitize_json_value_strips_nul_bytes_recursively() -> None:
    payload = {"heading_path": ["Unit\x00 1"], "meta": {"label": "A\x00B"}}

    assert _sanitize_json_value(payload) == {"heading_path": ["Unit 1"], "meta": {"label": "AB"}}


def test_build_qdrant_point_id_is_uuid_and_deterministic() -> None:
    point_id = _build_qdrant_point_id("4f22c61e-d2f1-484e-8c81-d70e623fcab4", 1)

    assert point_id == _build_qdrant_point_id("4f22c61e-d2f1-484e-8c81-d70e623fcab4", 1)
    assert len(point_id) == 36
    assert point_id.count("-") == 4
