from __future__ import annotations

import argparse
import json
import re
import zipfile
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree

try:
    import fitz
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyMuPDF is required for PDF extraction. Install pymupdf or use this repo's configured Python.") from exc


@dataclass(frozen=True)
class TextUnit:
    source_file: Path
    source_label: str
    locator: str
    text: str


def clean_text(value: str) -> str:
    value = unescape(value)
    value = value.replace("\u00a0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def compact_label(path: Path) -> str:
    stem = re.sub(r"[_-]+", " ", path.stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem[:90]


def extract_pdf(path: Path) -> list[TextUnit]:
    units: list[TextUnit] = []
    doc = fitz.open(path)
    for index, page in enumerate(doc, start=1):
        text = clean_text(page.get_text("text"))
        if len(text.split()) >= 20:
            units.append(TextUnit(path, compact_label(path), f"page {index}", text))
    return units


def slide_sort_key(name: str) -> int:
    match = re.search(r"slide(\d+)\.xml$", name)
    return int(match.group(1)) if match else 0


def extract_pptx(path: Path) -> list[TextUnit]:
    units: list[TextUnit] = []
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        slide_names = sorted(
            [name for name in archive.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", name)],
            key=slide_sort_key,
        )
        for slide_index, name in enumerate(slide_names, start=1):
            root = ElementTree.fromstring(archive.read(name))
            parts = [node.text or "" for node in root.findall(".//a:t", ns)]
            text = clean_text("\n".join(part.strip() for part in parts if part.strip()))
            if len(text.split()) >= 8:
                units.append(TextUnit(path, compact_label(path), f"slide {slide_index}", text))
    return units


def extract_units(paths: Iterable[Path]) -> list[TextUnit]:
    units: list[TextUnit] = []
    for path in paths:
        if not path.exists():
            print(f"Skipping missing file: {path}")
            continue
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            units.extend(extract_pdf(path))
        elif suffix == ".pptx":
            units.extend(extract_pptx(path))
        else:
            print(f"Skipping unsupported file: {path}")
    return units


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"[•▪▫◦]", ". ", text)
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s+", " ", text)
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)
    return [piece.strip(" -\t\r\n") for piece in pieces if piece.strip()]


def normalize_answer(value: str) -> str:
    value = re.sub(r"[•▪▫◦]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" -:;,.")
    value = re.sub(r"^(outline|learning objectives|objectives)\s+", "", value, flags=re.IGNORECASE)
    return value.strip()


def meaningful_sentence(sentence: str) -> bool:
    sentence = normalize_answer(sentence)
    lowered = sentence.lower()
    forbidden = (
        "able to:",
        "aka, phd",
        "amino acid sequences that have been left blank",
        "by the end of this unit",
        "covenantuniversity",
        "department of business management",
        "dna mrna trna aa",
        "genetic code chart",
        "raising a new generation",
        "mr damilola",
        "learning objectives",
        "learning outcomes",
        "mcmath",
        "mcin",
        "mRNA transcript",
        "course code",
        "lecture ",
        "outline ",
        "you are to submit",
        "directions:",
        "fill in either",
        "mutated dna",
        "circle any changes",
        "writing python programs",
        "accepts a string",
        "then, determine",
        "mr.",
    )
    forbidden_starts = (
        "apply ",
        "define ",
        "describe ",
        "explain ",
        "identify ",
        "understand ",
        "compare ",
        "discuss ",
        "recognize ",
        "analyze ",
        "analyse ",
        "module one",
        "module two",
        "module three",
        "module four",
        "csc ",
        "sen211",
        "software engineering",
    )
    if len(sentence) < 55 or len(sentence) > 420:
        return False
    if any(token in lowered for token in forbidden):
        return False
    if any(lowered.startswith(token) for token in forbidden_starts):
        return False
    if any(token in lowered for token in ("copyright", "http://", "https://", "www.", "@")):
        return False
    if len(re.findall(r"[A-Za-z]{3,}", sentence)) < 8:
        return False
    if len(re.findall(r"[A-Za-z]", sentence)) / max(len(sentence), 1) < 0.55:
        return False
    return True


def chunk_unit(unit: TextUnit, max_chars: int = 1200) -> list[TextUnit]:
    lines = [line.strip(" -\t") for line in unit.text.splitlines() if line.strip(" -\t")]
    chunks: list[str] = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > max_chars and len(current.split()) >= 35:
            chunks.append(current.strip())
            current = line
        else:
            current = f"{current}\n{line}".strip()
    if len(current.split()) >= 20:
        chunks.append(current.strip())
    return [
        TextUnit(unit.source_file, unit.source_label, f"{unit.locator} section {index}", clean_text(chunk))
        for index, chunk in enumerate(chunks, start=1)
    ]


def select_answer(context: str) -> str | None:
    lines = [normalize_answer(line) for line in context.splitlines() if meaningful_sentence(line)]
    sentences = [normalize_answer(sentence) for sentence in split_sentences(context) if meaningful_sentence(sentence)]
    sentences = lines + [sentence for sentence in sentences if sentence not in set(lines)]
    if not sentences:
        return None
    answer = sentences[0]
    if len(answer) < 130 and len(sentences) > 1 and meaningful_sentence(sentences[1]):
        answer = f"{answer} {normalize_answer(sentences[1])}"
    return answer.strip()


def find_topic(unit: TextUnit, answer: str) -> str:
    for line in unit.text.splitlines():
        cleaned = re.sub(r"^[0-9.\-•\s]+", "", line).strip()
        lowered = cleaned.lower()
        if any(token in lowered for token in ("objectives", "outcomes", "mr damilola", "covenantuniversity")):
            continue
        if lowered.startswith(("apply ", "define ", "describe ", "explain ", "identify ", "understand ")):
            continue
        if 4 <= len(cleaned) <= 80 and len(cleaned.split()) <= 10:
            if cleaned.lower() not in answer.lower():
                return cleaned
    return unit.source_label


def make_question(answer: str, topic: str, source_label: str) -> str:
    simple = normalize_answer(answer)
    patterns = [
        (r"^(.{4,90}?)\s+is\s+", "What is {term}?"),
        (r"^(.{4,90}?)\s+are\s+", "What are {term}?"),
        (r"^(.{4,90}?)\s+refers to\s+", "What does {term} refer to?"),
        (r"^(.{4,90}?)\s+consists of\s+", "What does {term} consist of?"),
        (r"^(.{4,90}?)\s+includes\s+", "What does {term} include?"),
        (r"^(.{4,90}?)\s+involves\s+", "What does {term} involve?"),
    ]
    for pattern, template in patterns:
        match = re.match(pattern, simple, flags=re.IGNORECASE)
        if match:
            term = match.group(1).strip(" :;,.")
            if " " in term and len(term.split()) > 2:
                words = term.split()
                midpoint = len(words) // 2
                if words[:midpoint] == words[midpoint : midpoint * 2]:
                    term = " ".join(words[:midpoint])
            term = term.strip(" \"'“”")
            if 1 <= len(term.split()) <= 10 and not term.lower().startswith(("it ", "this ", "these ", "they ", "many ", "some ", "the following")):
                return template.format(term=term)
    topic = topic.strip(" :;,.")
    if topic and topic.lower() not in {"overview", "introduction", "objectives", "outline"}:
        return f"What key point does the source make about {topic}?"
    return f"What important concept is explained in {source_label}?"


def build_samples(units: list[TextUnit], size: int) -> list[dict]:
    chunks: list[TextUnit] = []
    for unit in units:
        chunks.extend(chunk_unit(unit))

    by_source: dict[str, list[TextUnit]] = {}
    for chunk in chunks:
        by_source.setdefault(str(chunk.source_file), []).append(chunk)

    samples: list[dict] = []
    seen_questions: set[str] = set()
    source_keys = sorted(by_source)
    cursor_by_source = {key: 0 for key in source_keys}

    while len(samples) < size and source_keys:
        progressed = False
        for source_key in source_keys:
            source_chunks = by_source[source_key]
            cursor = cursor_by_source[source_key]
            if cursor >= len(source_chunks):
                continue
            cursor_by_source[source_key] += 1
            unit = source_chunks[cursor]
            answer = select_answer(unit.text)
            if not answer:
                continue
            topic = find_topic(unit, answer)
            question = make_question(answer, topic, unit.source_label)
            question_key = question.lower()
            if question_key in seen_questions:
                question = f"According to {unit.source_label}, what should a student know about {topic}?"
                question_key = question.lower()
            if question_key in seen_questions:
                continue
            seen_questions.add(question_key)
            sample_number = len(samples) + 1
            samples.append(
                {
                    "sample_key": f"eduground-doc-{sample_number:03d}",
                    "question": question,
                    "expected_answer": answer,
                    "retrieved_context": unit.text,
                    "model_answer": answer,
                    "source_file": str(unit.source_file),
                    "source_label": unit.source_label,
                    "locator": unit.locator,
                    "topic": topic,
                    "source_context": unit.text,
                    "generation_note": "Seed sample: model_answer and retrieved_context should be replaced by deployed Eduground run output before final scoring.",
                }
            )
            progressed = True
            if len(samples) >= size:
                break
        if not progressed:
            break
    return samples


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a golden QA seed dataset from Eduground course documents.")
    parser.add_argument("--manifest", type=Path, default=Path("tools/evaluation/eduground_source_docs.json"))
    parser.add_argument("--output", type=Path, default=Path("apps/evaluator/app/datasets/eduground_100q.seed.json"))
    parser.add_argument("--size", type=int, default=100)
    args = parser.parse_args()

    paths = [Path(item) for item in json.loads(args.manifest.read_text(encoding="utf-8"))]
    units = extract_units(paths)
    samples = build_samples(units, args.size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(samples, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "sample_count": len(samples), "source_unit_count": len(units)}, indent=2))


if __name__ == "__main__":
    main()
