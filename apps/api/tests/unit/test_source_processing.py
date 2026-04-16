from __future__ import annotations

import pytest

from app.services.source_processing import _group_list_blocks


pytestmark = pytest.mark.unit


def test_group_list_blocks_handles_dash_bullets_without_regex_failure() -> None:
    text = "- First item\n- Second item\n\nBody paragraph"

    grouped = _group_list_blocks(text)

    assert "<!-- LIST_START -->" in grouped
    assert "\u2022 First item" in grouped
    assert "\u2022 Second item" in grouped
    assert "Body paragraph" in grouped


def test_group_list_blocks_handles_star_bullets_without_regex_failure() -> None:
    text = "* First item\n* Second item"

    grouped = _group_list_blocks(text)

    assert "\u2022 First item" in grouped
    assert "\u2022 Second item" in grouped
