from __future__ import annotations

import sys
from pathlib import Path

import pytest


WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))
for module_name in list(sys.modules):
    if module_name == "app" or module_name.startswith("app."):
        del sys.modules[module_name]


@pytest.fixture
def markdown_source_bytes() -> bytes:
    return b"""# Lecture 1

This is a grounded tutoring source with enough content to create chunks for the ingestion pipeline tests.

- first concept
- second concept
- third concept

| term | definition |
| cell | unit of life |
| dna | genetic material |

Closing paragraph with extra tokens so chunk construction has stable material to work with.
"""
