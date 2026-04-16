"""
Structure-preserving document loaders that maintain tables, lists, and formatting.
"""
import os
import re
import logging
from typing import List, Dict, Any, Optional, Tuple
from langchain_core.documents import Document

logger = logging.getLogger(__name__)


class StructurePreservingLoader:
    """
    Loads documents while preserving structural elements like:
    - Tables (converted to Markdown format)
    - Bullet points and numbered lists
    - Roman numerals
    - Indentation and hierarchy
    """
    
    # Markers for preserved structures (helps the splitter avoid breaking them)
    TABLE_START = "\n<!-- TABLE_START -->\n"
    TABLE_END = "\n<!-- TABLE_END -->\n"
    LIST_START = "\n<!-- LIST_START -->\n"
    LIST_END = "\n<!-- LIST_END -->\n"
    
    def __init__(self, preserve_markers: bool = False):
        """
        Args:
            preserve_markers: If True, adds markers around structures to help
                            the text splitter avoid breaking them
        """
        self.preserve_markers = preserve_markers
    
    # ==================== DOCX LOADING ====================
    
    def load_docx(self, file_path: str) -> List[Document]:
        """
        Load a DOCX file while preserving tables and list structures.
        
        Args:
            file_path: Path to the .docx file
            
        Returns:
            List of Document objects with preserved structure
        """
        try:
            from docx import Document as DocxDocument
            from docx.table import Table
            from docx.text.paragraph import Paragraph
            from docx.oxml.ns import qn
        except ImportError:
            logger.error("python-docx not installed. Install with: pip install python-docx")
            raise
        
        logger.info(f"Loading DOCX with structure preservation: {file_path}")
        
        doc = DocxDocument(file_path)
        content_parts = []
        
        # Process document body elements in order
        for element in doc.element.body:
            if element.tag.endswith('tbl'):
                # This is a table
                table = self._find_table_by_element(doc, element)
                if table:
                    table_md = self._table_to_markdown(table)
                    if self.preserve_markers:
                        content_parts.append(f"{self.TABLE_START}{table_md}{self.TABLE_END}")
                    else:
                        content_parts.append(table_md)
                        
            elif element.tag.endswith('p'):
                # This is a paragraph
                para = self._find_paragraph_by_element(doc, element)
                if para:
                    formatted_para = self._format_paragraph(para)
                    if formatted_para.strip():
                        content_parts.append(formatted_para)
        
        content = "\n".join(content_parts)
        
        # Post-process to group lists together
        content = self._group_list_items(content)
        
        metadata = {
            "source": file_path,
            "file_type": "docx",
            "structure_preserved": True,
            "tables_count": content.count(self.TABLE_START) if self.preserve_markers else content.count("| ---"),
        }
        
        logger.info(f"Successfully loaded DOCX with preserved structure")
        return [Document(page_content=content, metadata=metadata)]
    
    def _find_table_by_element(self, doc, element):
        """Find the Table object corresponding to an XML element."""
        from docx.table import Table
        for table in doc.tables:
            if table._tbl is element:
                return table
        return None
    
    def _find_paragraph_by_element(self, doc, element):
        """Find the Paragraph object corresponding to an XML element."""
        for para in doc.paragraphs:
            if para._p is element:
                return para
        return None
    
    def _table_to_markdown(self, table) -> str:
        """
        Convert a DOCX table to Markdown format.
        
        Args:
            table: A python-docx Table object
            
        Returns:
            Markdown-formatted table string
        """
        if not table.rows:
            return ""
        
        rows_data = []
        max_cols = 0
        
        for row in table.rows:
            row_cells = []
            for cell in row.cells:
                # Get cell text, preserving internal line breaks
                cell_text = cell.text.strip().replace('\n', ' ').replace('|', '\\|')
                row_cells.append(cell_text)
            rows_data.append(row_cells)
            max_cols = max(max_cols, len(row_cells))
        
        # Normalize all rows to have the same number of columns
        for row in rows_data:
            while len(row) < max_cols:
                row.append("")
        
        if not rows_data:
            return ""
        
        # Build Markdown table
        md_lines = []
        
        # Header row
        header = "| " + " | ".join(rows_data[0]) + " |"
        md_lines.append(header)
        
        # Separator row
        separator = "| " + " | ".join(["---"] * max_cols) + " |"
        md_lines.append(separator)
        
        # Data rows
        for row in rows_data[1:]:
            data_row = "| " + " | ".join(row) + " |"
            md_lines.append(data_row)
        
        return "\n".join(md_lines)
    
    def _format_paragraph(self, para) -> str:
        """
        Format a paragraph, preserving list markers and indentation.
        
        Args:
            para: A python-docx Paragraph object
            
        Returns:
            Formatted paragraph string
        """
        from docx.oxml.ns import qn
        
        text = para.text.strip()
        if not text:
            return ""
        
        # Check for list formatting
        p_element = para._p
        numPr = p_element.find(qn('w:pPr'))
        
        prefix = ""
        indent = ""
        
        if numPr is not None:
            numId_elem = numPr.find(qn('w:numPr'))
            if numId_elem is not None:
                # This is a list item
                ilvl_elem = numId_elem.find(qn('w:ilvl'))
                level = int(ilvl_elem.get(qn('w:val'))) if ilvl_elem is not None else 0
                indent = "  " * level  # 2 spaces per indent level
                
                # Try to detect list type from text patterns
                if not self._has_list_marker(text):
                    prefix = "• "  # Default to bullet if no marker detected
        
        # Check paragraph style for list detection
        style_name = para.style.name.lower() if para.style else ""
        if 'list' in style_name or 'bullet' in style_name:
            if not self._has_list_marker(text) and not prefix:
                prefix = "• "
        
        # Preserve existing markers
        formatted_text = self._normalize_list_markers(text)
        
        return f"{indent}{prefix}{formatted_text}"
    
    def _has_list_marker(self, text: str) -> bool:
        """Check if text already starts with a list marker."""
        patterns = [
            r'^[\u2022\u2023\u25E6\u2043\u2219•●○◦▪▸►]\s*',  # Bullet characters
            r'^\d+[\.\)]\s*',  # Numbered: 1. or 1)
            r'^[a-zA-Z][\.\)]\s*',  # Lettered: a. or a)
            r'^[ivxIVX]+[\.\)]\s*',  # Roman numerals: i. ii. iii. or I. II. III.
            r'^[-–—]\s*',  # Dashes
            r'^\*\s+',  # Asterisk bullets
        ]
        
        for pattern in patterns:
            if re.match(pattern, text):
                return True
        return False
    
    def _normalize_list_markers(self, text: str) -> str:
        """
        Normalize list markers for consistency while preserving the type.
        
        Args:
            text: Text that may contain list markers
            
        Returns:
            Text with normalized markers
        """
        # Replace various bullet characters with a standard one
        bullet_chars = ['●', '○', '◦', '▪', '▸', '►', '■', '□', '◆', '◇']
        for char in bullet_chars:
            text = text.replace(char, '•')
        
        return text
    
    def _group_list_items(self, content: str) -> str:
        """
        Group consecutive list items together with markers.
        
        Args:
            content: Full document content
            
        Returns:
            Content with list groups marked
        """
        if not self.preserve_markers:
            return content
        
        lines = content.split('\n')
        result_lines = []
        in_list = False
        list_buffer = []
        
        list_patterns = [
            r'^[\s]*[\u2022\u2023\u25E6\u2043\u2219•●○◦▪▸►]\s*',  # Bullets
            r'^[\s]*\d+[\.\)]\s*',  # Numbered
            r'^[\s]*[a-zA-Z][\.\)]\s*',  # Lettered
            r'^[\s]*[ivxIVX]+[\.\)]\s*',  # Roman numerals
            r'^[\s]*[-–—]\s+',  # Dashes
            r'^[\s]*\*\s+',  # Asterisks
        ]
        
        def is_list_item(line):
            for pattern in list_patterns:
                if re.match(pattern, line):
                    return True
            return False
        
        for line in lines:
            if is_list_item(line):
                if not in_list:
                    in_list = True
                    list_buffer = []
                list_buffer.append(line)
            else:
                if in_list:
                    # End of list, flush buffer
                    result_lines.append(self.LIST_START)
                    result_lines.extend(list_buffer)
                    result_lines.append(self.LIST_END)
                    in_list = False
                    list_buffer = []
                result_lines.append(line)
        
        # Don't forget any remaining list items
        if in_list and list_buffer:
            result_lines.append(self.LIST_START)
            result_lines.extend(list_buffer)
            result_lines.append(self.LIST_END)
        
        return '\n'.join(result_lines)
    

    # ==================== PDF LOADING ====================
    
    def load_pdf(self, file_path: str) -> List[Document]:
        """
        Load a PDF file while attempting to preserve tables and lists.
        
        Args:
            file_path: Path to the .pdf file
            
        Returns:
            List of Document objects with preserved structure
        """
        logger.info(f"Loading PDF with structure preservation: {file_path}")
        
        documents = []
        
        # Try pdfplumber first (better table detection)
        try:
            documents = self._load_pdf_with_pdfplumber(file_path)
            if documents:
                logger.info("Successfully loaded PDF using pdfplumber")
                return documents
        except ImportError:
            logger.info("pdfplumber not available, trying PyMuPDF")
        except Exception as e:
            logger.warning(f"pdfplumber failed: {e}, trying PyMuPDF")
        
        # Fallback to PyMuPDF
        try:
            documents = self._load_pdf_with_pymupdf(file_path)
            if documents:
                logger.info("Successfully loaded PDF using PyMuPDF")
                return documents
        except ImportError:
            logger.info("PyMuPDF not available, falling back to PyPDF")
        except Exception as e:
            logger.warning(f"PyMuPDF failed: {e}, falling back to PyPDF")
        
        # Final fallback to PyPDF (basic extraction)
        return self._load_pdf_with_pypdf(file_path)
    
    def _load_pdf_with_pdfplumber(self, file_path: str) -> List[Document]:
        """Load PDF using pdfplumber for better table extraction."""
        import pdfplumber
        
        content_parts = []
        tables_found = 0
        
        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                page_content = []
                
                # Extract tables first
                tables = page.extract_tables()
                table_bboxes = [table.bbox for table in page.find_tables()] if tables else []
                
                for table in tables:
                    if table and any(any(cell for cell in row) for row in table):
                        table_md = self._list_table_to_markdown(table)
                        if self.preserve_markers:
                            page_content.append(f"{self.TABLE_START}{table_md}{self.TABLE_END}")
                        else:
                            page_content.append(table_md)
                        tables_found += 1
                
                # Extract text (excluding table areas if possible)
                text = page.extract_text() or ""
                
                # Process text to preserve list formatting
                processed_text = self._process_pdf_text(text)
                if processed_text.strip():
                    page_content.append(processed_text)
                
                if page_content:
                    content_parts.append(f"\n--- Page {page_num} ---\n" + "\n\n".join(page_content))
        
        content = "\n".join(content_parts)
        content = self._group_list_items(content)
        
        metadata = {
            "source": file_path,
            "file_type": "pdf",
            "structure_preserved": True,
            "tables_count": tables_found,
            "extraction_method": "pdfplumber"
        }
        
        return [Document(page_content=content, metadata=metadata)]
    
    def _load_pdf_with_pymupdf(self, file_path: str) -> List[Document]:
        """Load PDF using PyMuPDF (fitz) for better text extraction."""
        import fitz  # PyMuPDF
        
        content_parts = []
        
        doc = fitz.open(file_path)
        
        for page_num, page in enumerate(doc, 1):
            # Extract text with better formatting preservation
            blocks = page.get_text("dict")["blocks"]
            page_content = []
            
            for block in blocks:
                if block["type"] == 0:  # Text block
                    block_text = ""
                    for line in block.get("lines", []):
                        line_text = ""
                        for span in line.get("spans", []):
                            line_text += span.get("text", "")
                        block_text += line_text + "\n"
                    
                    processed = self._process_pdf_text(block_text)
                    if processed.strip():
                        page_content.append(processed)
                        
                elif block["type"] == 1:  # Image block
                    pass  # Skip images for now
            
            if page_content:
                content_parts.append(f"\n--- Page {page_num} ---\n" + "\n".join(page_content))
        
        doc.close()
        
        content = "\n".join(content_parts)
        content = self._group_list_items(content)
        
        metadata = {
            "source": file_path,
            "file_type": "pdf",
            "structure_preserved": True,
            "extraction_method": "pymupdf"
        }
        
        return [Document(page_content=content, metadata=metadata)]
    
    def _load_pdf_with_pypdf(self, file_path: str) -> List[Document]:
        """Fallback PDF loading using PyPDF."""
        from langchain_community.document_loaders import PyPDFLoader
        
        loader = PyPDFLoader(file_path)
        docs = loader.load()
        
        # Post-process to preserve list formatting
        for doc in docs:
            doc.page_content = self._process_pdf_text(doc.page_content)
            doc.page_content = self._group_list_items(doc.page_content)
            doc.metadata["structure_preserved"] = True
            doc.metadata["extraction_method"] = "pypdf"
        
        return docs
    
    def _list_table_to_markdown(self, table: List[List[str]]) -> str:
        """Convert a list-based table to Markdown format."""
        if not table or not table[0]:
            return ""
        
        # Clean cells
        cleaned_table = []
        max_cols = 0
        
        for row in table:
            cleaned_row = []
            for cell in row:
                cell_text = str(cell).strip() if cell else ""
                cell_text = cell_text.replace('\n', ' ').replace('|', '\\|')
                cleaned_row.append(cell_text)
            cleaned_table.append(cleaned_row)
            max_cols = max(max_cols, len(cleaned_row))
        
        # Normalize columns
        for row in cleaned_table:
            while len(row) < max_cols:
                row.append("")
        
        # Build Markdown
        md_lines = []
        
        # Header
        md_lines.append("| " + " | ".join(cleaned_table[0]) + " |")
        md_lines.append("| " + " | ".join(["---"] * max_cols) + " |")
        
        # Data rows
        for row in cleaned_table[1:]:
            md_lines.append("| " + " | ".join(row) + " |")
        
        return "\n".join(md_lines)
    
    def _process_pdf_text(self, text: str) -> str:
        """
        Process PDF text to preserve and normalize list formatting.
        
        Args:
            text: Raw text from PDF extraction
            
        Returns:
            Text with normalized list markers
        """
        lines = text.split('\n')
        processed_lines = []
        
        for line in lines:
            line = line.rstrip()
            
            # Skip empty lines but preserve them for spacing
            if not line.strip():
                processed_lines.append("")
                continue
            
            # Detect and normalize various list formats
            
            # Roman numerals (i., ii., iii., iv., etc. or I., II., III.)
            roman_match = re.match(r'^(\s*)([ivxlcdmIVXLCDM]+)[\.\)]\s*(.*)$', line)
            if roman_match:
                indent, numeral, content = roman_match.groups()
                processed_lines.append(f"{indent}{numeral}. {content}")
                continue
            
            # Numbered lists (1., 2., 3. or 1), 2), 3))
            num_match = re.match(r'^(\s*)(\d+)[\.\)]\s*(.*)$', line)
            if num_match:
                indent, number, content = num_match.groups()
                processed_lines.append(f"{indent}{number}. {content}")
                continue
            
            # Lettered lists (a., b., c. or a), b), c))
            letter_match = re.match(r'^(\s*)([a-zA-Z])[\.\)]\s*(.*)$', line)
            if letter_match:
                indent, letter, content = letter_match.groups()
                processed_lines.append(f"{indent}{letter}. {content}")
                continue
            
            # Bullet points (various characters)
            bullet_match = re.match(r'^(\s*)[•●○◦▪▸►■□◆◇\-–—\*]\s*(.*)$', line)
            if bullet_match:
                indent, content = bullet_match.groups()
                processed_lines.append(f"{indent}• {content}")
                continue
            
            # Default: keep line as-is
            processed_lines.append(line)
        
        return '\n'.join(processed_lines)

# ==================== STRUCTURE-AWARE TEXT SPLITTER ====================

class StructureAwareTextSplitter:
    """
    A text splitter that respects structural boundaries like tables and lists.
    """
    
    def __init__(
        self,
        chunk_size: int = 2000,
        chunk_overlap: int = 200,
        respect_tables: bool = True,
        respect_lists: bool = True
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.respect_tables = respect_tables
        self.respect_lists = respect_lists
        
        # Markers
        self.TABLE_START = StructurePreservingLoader.TABLE_START
        self.TABLE_END = StructurePreservingLoader.TABLE_END
        self.LIST_START = StructurePreservingLoader.LIST_START
        self.LIST_END = StructurePreservingLoader.LIST_END
    
    def split_documents(self, documents: List[Document]) -> List[Document]:
        """
        Split documents while preserving structural elements.
        
        Args:
            documents: List of Document objects to split
            
        Returns:
            List of split Document objects
        """
        all_chunks = []
        
        for doc in documents:
            chunks = self._split_text(doc.page_content, doc.metadata)
            all_chunks.extend(chunks)
        
        return all_chunks
    
    def _split_text(self, text: str, metadata: dict) -> List[Document]:
        """Split text while respecting structural boundaries."""
        
        # First, identify protected regions (tables and lists)
        protected_regions = self._identify_protected_regions(text)
        
        # Split text into segments, keeping protected regions intact
        segments = self._segment_text(text, protected_regions)
        
        # Now split each non-protected segment normally
        chunks = []
        current_chunk = ""
        
        for segment in segments:
            if segment['protected']:
                # If adding this protected segment exceeds chunk size significantly,
                # flush current chunk first
                if current_chunk and len(current_chunk) + len(segment['text']) > self.chunk_size * 1.5:
                    chunks.append(self._create_chunk(current_chunk, metadata))
                    current_chunk = ""
                
                # Add protected segment (possibly as its own chunk if very large)
                if len(segment['text']) > self.chunk_size * 2:
                    # Protected segment is very large, add it as its own chunk
                    if current_chunk:
                        chunks.append(self._create_chunk(current_chunk, metadata))
                        current_chunk = ""
                    chunks.append(self._create_chunk(segment['text'], metadata, is_structure=True))
                else:
                    current_chunk += segment['text']
            else:
                # Regular text - split normally
                sub_chunks = self._split_regular_text(segment['text'])
                
                for i, sub_chunk in enumerate(sub_chunks):
                    if len(current_chunk) + len(sub_chunk) <= self.chunk_size:
                        current_chunk += sub_chunk
                    else:
                        if current_chunk:
                            chunks.append(self._create_chunk(current_chunk, metadata))
                        current_chunk = sub_chunk
        
        # Don't forget the last chunk
        if current_chunk.strip():
            chunks.append(self._create_chunk(current_chunk, metadata))
        
        return chunks
    
    def _identify_protected_regions(self, text: str) -> List[Tuple[int, int, str]]:
        """
        Identify regions that should not be split.
        
        Returns:
            List of (start, end, type) tuples
        """
        regions = []
        
        if self.respect_tables:
            # Find all table regions
            for match in re.finditer(
                re.escape(self.TABLE_START) + r'(.*?)' + re.escape(self.TABLE_END),
                text,
                re.DOTALL
            ):
                regions.append((match.start(), match.end(), 'table'))
        
        if self.respect_lists:
            # Find all list regions
            for match in re.finditer(
                re.escape(self.LIST_START) + r'(.*?)' + re.escape(self.LIST_END),
                text,
                re.DOTALL
            ):
                regions.append((match.start(), match.end(), 'list'))
        
        # Sort by start position
        regions.sort(key=lambda x: x[0])
        
        return regions
    
    def _segment_text(self, text: str, protected_regions: List[Tuple[int, int, str]]) -> List[Dict]:
        """Segment text into protected and non-protected parts."""
        segments = []
        current_pos = 0
        
        for start, end, region_type in protected_regions:
            # Add non-protected text before this region
            if current_pos < start:
                non_protected = text[current_pos:start]
                if non_protected.strip():
                    segments.append({'text': non_protected, 'protected': False, 'type': 'text'})
            
            # Add protected region (clean up markers for final output)
            protected_text = text[start:end]
            protected_text = protected_text.replace(self.TABLE_START, '\n')
            protected_text = protected_text.replace(self.TABLE_END, '\n')
            protected_text = protected_text.replace(self.LIST_START, '\n')
            protected_text = protected_text.replace(self.LIST_END, '\n')
            
            segments.append({'text': protected_text, 'protected': True, 'type': region_type})
            current_pos = end
        
        # Add remaining text
        if current_pos < len(text):
            remaining = text[current_pos:]
            if remaining.strip():
                segments.append({'text': remaining, 'protected': False, 'type': 'text'})
        
        return segments
    
    def _split_regular_text(self, text: str) -> List[str]:
        """Split regular (non-protected) text into chunks."""
        if len(text) <= self.chunk_size:
            return [text]
        
        # Use natural separators
        separators = ["\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " "]
        
        chunks = []
        current = ""
        
        # Split by paragraphs first
        paragraphs = text.split("\n\n")
        
        for para in paragraphs:
            if len(current) + len(para) + 2 <= self.chunk_size:
                current += para + "\n\n"
            else:
                if current:
                    chunks.append(current.strip())
                
                # If paragraph itself is too large, split it further
                if len(para) > self.chunk_size:
                    sub_chunks = self._split_large_paragraph(para)
                    chunks.extend(sub_chunks[:-1])
                    current = sub_chunks[-1] + "\n\n" if sub_chunks else ""
                else:
                    current = para + "\n\n"
        
        if current.strip():
            chunks.append(current.strip())
        
        return chunks
    
    def _split_large_paragraph(self, para: str) -> List[str]:
        """Split a large paragraph by sentences."""
        # Split by sentence endings
        sentences = re.split(r'(?<=[.!?])\s+', para)
        
        chunks = []
        current = ""
        
        for sentence in sentences:
            if len(current) + len(sentence) + 1 <= self.chunk_size:
                current += sentence + " "
            else:
                if current:
                    chunks.append(current.strip())
                current = sentence + " "
        
        if current.strip():
            chunks.append(current.strip())
        
        return chunks
    
    def _create_chunk(self, text: str, metadata: dict, is_structure: bool = False) -> Document:
        """Create a Document chunk with metadata."""
        chunk_metadata = metadata.copy()
        chunk_metadata['chunk_size'] = len(text)
        chunk_metadata['contains_structure'] = is_structure
        
        # Clean up any remaining markers
        text = text.replace(self.TABLE_START, '\n')
        text = text.replace(self.TABLE_END, '\n')
        text = text.replace(self.LIST_START, '\n')
        text = text.replace(self.LIST_END, '\n')
        text = re.sub(r'\n{3,}', '\n\n', text)  # Reduce excessive newlines
        
        return Document(page_content=text.strip(), metadata=chunk_metadata)


# Singleton instance for easy import
structure_splitter = StructureAwareTextSplitter()
structure_loader = StructurePreservingLoader(preserve_markers=False)
structure_loader_with_markers = StructurePreservingLoader(preserve_markers=True)
