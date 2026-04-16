from __future__ import annotations

from app.integrations.parser.base import ParsedDocument, ParsedSegment, ParserAdapter


class TextParser(ParserAdapter):
    def parse(self, raw_bytes: bytes, filename: str) -> ParsedDocument:
        text = raw_bytes.decode("utf-8", errors="replace")
        return ParsedDocument(
            text=text,
            segments=[ParsedSegment(segment_type="section", segment_number=1, title=filename, text=text)],
        )

