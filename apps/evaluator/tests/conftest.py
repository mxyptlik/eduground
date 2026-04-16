from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest


EVALUATOR_ROOT = Path(__file__).resolve().parents[1]
if str(EVALUATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(EVALUATOR_ROOT))
for module_name in list(sys.modules):
    if module_name == "app" or module_name.startswith("app."):
        del sys.modules[module_name]


@pytest.fixture
def sample_dataset_file() -> Path:
    temp_dir = EVALUATOR_ROOT / ".pytest_tmp"
    temp_dir.mkdir(exist_ok=True)
    path = temp_dir / f"golden_qa_{uuid4().hex}.json"
    path.write_text(
        json.dumps(
            [
                {
                    "sample_key": "sample-1",
                    "question": "What is grounding?",
                    "expected_answer": "Grounding ties answers to notebook evidence.",
                    "retrieved_context": "Notebook evidence ties grounded answers to cited source material.",
                    "model_answer": "Grounding ties answers to cited notebook evidence.",
                },
                {
                    "sample_key": "sample-2",
                    "question": "What is mastery?",
                    "expected_answer": "Mastery should reflect quiz performance.",
                    "retrieved_context": "Mastery updates should reflect quiz attempt outcomes.",
                    "model_answer": "Mastery updates should reflect quiz attempt outcomes.",
                },
            ]
        ),
        encoding="utf-8",
    )
    try:
        yield path
    finally:
        if path.exists():
            path.unlink()
