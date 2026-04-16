
import os
import logging
from dotenv import load_dotenv
from langchain_core.documents import Document
from typing import List, Dict, Any, Optional, Union
from langchain_community.document_loaders import (
    PyPDFLoader,
    Docx2txtLoader,
    TextLoader,
    CSVLoader,
    UnstructuredHTMLLoader,
    UnstructuredPowerPointLoader
)
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing as mp
from functools import partial
import time
from database.pg_database_manager import get_db_connection, update_file_status
from qdrant_service import qdrant_service 
from upload_layer.structure_preserving_loader import (
    StructurePreservingLoader,
    structure_loader_with_markers,
    StructureAwareTextSplitter,
    structure_loader,
    structure_splitter
)
import asyncio
import faulthandler
import psycopg2 
faulthandler.enable() 
# Load environment variables from .env file
load_dotenv()
from config import settings
# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
# --- Configuration ---
DOCS_DIR = settings.company_docs_directory
# EMBEDDING_MODEL is now handled by ChromaService
QDRANT_DB_PATH = settings.QDRANT_DB
# COLLECTION_NAME is now determined dynamically based on access_status
INGEST_BATCH_SIZE = int(os.getenv("INGEST_BATCH_SIZE", "10"))
# Add these optimized settings
OPTIMAL_CHUNK_SIZE = 2000  # Smaller chunks = faster embedding
OPTIMAL_CHUNK_OVERLAP = 200
BATCH_SIZE = 50  # Process embeddings in larger batches
MAX_WORKERS = min(32, (mp.cpu_count() or 1) + 4)  # Optimal thread count
DEFAULT_COLLECTION_NAME = "MoneyQuest"

def load_excel_file(file_path):
    """Load Excel files (.xlsx, .xls) and convert to documents."""
    logger.info(f"Loading Excel file: {file_path}")
    
    try:
        import pandas as pd
        from langchain.docstore.document import Document
        
        # Determine the engine to use
        engine = 'openpyxl'
        try:
            # Test with openpyxl first by reading just sheet names
            excel_file = pd.ExcelFile(file_path, engine='openpyxl')
        except Exception as e:
            # Fallback for older Excel files (.xls)
            logger.info(f"OpenPyXL failed, trying xlrd for older Excel format: {e}")
            engine = 'xlrd'
            excel_file = pd.ExcelFile(file_path, engine='xlrd')
        
        sheet_names = excel_file.sheet_names
        total_sheets = len(sheet_names)
        documents = []
        
        # Process sheets one at a time to reduce memory usage
        for sheet_name in sheet_names:
            df = pd.read_excel(excel_file, sheet_name=sheet_name)
            logger.info(f"Processing sheet: '{sheet_name}' with {len(df)} rows and {len(df.columns)} columns")
            
            # Skip empty sheets
            if df.empty:
                logger.info(f"Sheet '{sheet_name}' is empty, skipping")
                continue
            
            # Convert DataFrame to a readable text format
            sheet_content = []
            
            # Add sheet title
            sheet_content.append(f"SPREADSHEET: {sheet_name}")
            sheet_content.append("=" * (len(sheet_name) + 14))
            
            # Add column headers
            if not df.columns.empty:
                headers = " | ".join([str(col) for col in df.columns])
                sheet_content.append(f"COLUMNS: {headers}")
                sheet_content.append("-" * len(headers))
            
            # Add rows (limit to prevent huge documents)
            max_rows = 1000  # Limit rows to prevent massive documents
            for idx, row in df.head(max_rows).iterrows():
                # Convert row to string, handling NaN values
                row_data = []
                for col, value in row.items():
                    if pd.isna(value):
                        row_data.append("(empty)")
                    else:
                        row_data.append(str(value).strip())
                
                # Only include rows with actual content
                if any(data != "(empty)" and data for data in row_data):
                    sheet_content.append(" | ".join(row_data))
            
            # Add truncation notice if there are more rows
            if len(df) > max_rows:
                sheet_content.append(f"\n[Note: Sheet contains {len(df)} total rows, showing first {max_rows}]")
            
            # Create document for this sheet
            if sheet_content:
                content = "\n".join(sheet_content)
                metadata = {
                    "source": file_path,
                    "sheet_name": sheet_name,
                    "total_sheets": total_sheets,
                    "rows": len(df),
                    "columns": len(df.columns),
                    "file_type": "excel",
                    "content_type": "spreadsheet"
                }
                
                documents.append(Document(
                    page_content=content,
                    metadata=metadata
                ))
            
            # Clear dataframe from memory after processing
            del df
        
        # Close the Excel file handle
        excel_file.close()
        
        if documents:
            logger.info(f"Successfully loaded Excel file: {len(documents)} sheets processed")
            return documents
        else:
            logger.warning("No content extracted from Excel file")
            return []
    
    except ImportError as e:
        logger.error(f"Required libraries not installed for Excel support: {e}")
        logger.info("Please install: pip install pandas openpyxl xlrd")
        raise Exception("Excel support requires pandas, openpyxl, and xlrd libraries")
    except Exception as e:
        logger.error(f"Failed to load Excel file: {e}")
        raise

def load_pptx_with_fallback(file_path):
    """Load PPTX with multiple fallback methods for better reliability."""
    logger.info(f"Loading PPTX file: {file_path}")

    try:
        from pptx import Presentation
        
        prs = Presentation(file_path)
        documents = []
        
        for slide_num, slide in enumerate(prs.slides, 1):
            slide_content = []
            
            # Extract text from all shapes
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    slide_content.append(shape.text.strip())
                
                # Handle tables in slides
                if shape.has_table:
                    table = shape.table
                    for row in table.rows:
                        row_cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                        if row_cells:
                            slide_content.append(" | ".join(row_cells))
            
            # Extract speaker notes
            if slide.notes_slide and slide.notes_slide.notes_text_frame:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    slide_content.append(f"Speaker Notes: {notes}")
            
            if slide_content:
                content = "\n\n".join(slide_content)
                metadata = {
                    "source": file_path,
                    "slide_number": slide_num,
                    "total_slides": len(prs.slides),
                    "file_type": "pptx"
                }
                
                documents.append(Document(
                    page_content=content,
                    metadata=metadata
                ))
        
        if documents:
            logger.info(f"Successfully loaded PPTX using python-pptx fallback: {len(documents)} slides")
            return documents
        else:
            logger.warning("No content extracted from PPTX file using any method")
            return []
    
    except ImportError:
        logger.warning("python-pptx not installed. Falling back to original method only.")
        # Return empty list to trigger the "no content" handling in the main logic
        return []
    except Exception as e:
        logger.error(f"All PPTX loading methods failed: {e}")
        raise

def validate_pptx_file(file_path):
    """Validate PPTX file before processing."""
    try:
        # Check file size (skip very large files that might cause issues)
        file_size = os.path.getsize(file_path)
        max_size = 100 * 1024 * 1024  # 100MB limit
        
        if file_size > max_size:
            logger.warning(f"PPTX file {file_path} is very large ({file_size / 1024 / 1024:.1f}MB). Processing may be slow.")
        
        # Try to open with python-pptx to validate format (if available)
        try:
            from pptx import Presentation
            prs = Presentation(file_path)
            slide_count = len(prs.slides)
            logger.info(f"PPTX validation successful: {slide_count} slides found")
            return True, f"Valid PPTX with {slide_count} slides"
        except ImportError:
            # If python-pptx is not available, just check if file exists and has reasonable size
            logger.info("python-pptx not available for validation, performing basic checks")
            return True, "Basic validation passed (python-pptx not available for detailed validation)"
        except Exception as e:
            return False, f"Invalid PPTX format: {e}"
    
    except Exception as e:
        return False, f"File validation failed: {e}"

text_splitter = StructureAwareTextSplitter(
    chunk_size=OPTIMAL_CHUNK_SIZE,
    chunk_overlap=OPTIMAL_CHUNK_OVERLAP,
    respect_tables=True,
    respect_lists=True
)

def get_document_loader(file_path, file_extension):
    """Returns a document loader for a given file extension with enhanced PPTX support."""
    # Standard loaders for all other file types (preserve existing functionality)
    loaders = {
        ".txt": TextLoader,
        ".csv": CSVLoader,
        ".html": UnstructuredHTMLLoader,
    }
    if not os.path.isabs(file_path):
            file_path = os.path.abspath(file_path)
        
    if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return None
        
    logger.info(f"Loading file: {file_path} (extension: {file_extension})")

    file_ext_lower = file_extension.lower()
    
    # Use structure-preserving loaders for DOCX and PDF
    if file_ext_lower == ".docx":
        try:
            return structure_loader_with_markers.load_docx(file_path)
        except Exception as e:
            logger.warning(f"Structure-preserving DOCX loader failed: {e}, falling back to basic loader")
            return Docx2txtLoader(file_path)
    
    if file_ext_lower == ".pdf":
        try:
            return structure_loader_with_markers.load_pdf(file_path)
        except Exception as e:
            logger.warning(f"Structure-preserving PDF loader failed: {e}, falling back to basic loader")
            return PyPDFLoader(file_path)
        
    # Handle PPTX separately with our enhanced loader
    if file_ext_lower == ".pptx":
        # Return a lambda that calls our enhanced PPTX loader
        return load_pptx_with_fallback(file_path)
    
    if file_ext_lower in [".xlsx", ".xls"]:
        return load_excel_file(file_path)
          
    # For all other file types, use existing logic
    loader_class = loaders.get(file_ext_lower)
    if loader_class:
        try:
            if file_ext_lower == ".txt":
                # Handle potential encoding issues for text files
                try:
                    return loader_class(file_path, encoding='utf-8')
                except:
                    # Fallback to default encoding if utf-8 fails
                    return loader_class(file_path)
            return loader_class(file_path)
        except Exception as e:
            logger.error(f"Error loading file {file_path}: {e}")
            return None
    
    # If no loader found, return None (existing behavior)
    return None

import tempfile
from storage.storage_service import storage_service

def get_pending_files():
    """Fetches files from the database that are pending ingestion."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT 
                        id,
                        sanitized_filename, 
                        department, 
                        access_status,
                        file_hash,
                        original_filename
                    FROM processed_files 
                    WHERE parsing_status = 'pending'
                """)
                
                rows = cursor.fetchall()
                
        pending_files = []
        for row in rows:
            file_record = {
                'id': row['id'], 
                'sanitized_filename': row['sanitized_filename'],
                'department': row['department'] if row['department'] else 'General',
                'access_status': row['access_status'] if row['access_status'] else 'public',
                'file_hash': row['file_hash'],
                'original_filename': row['original_filename']
            }
            # We don't check for local file existence anymore, but for R2 presence (optional)
            pending_files.append(file_record)
        
        logger.info(f"Found {len(pending_files)} pending files to process.")
        return pending_files
        
    except psycopg2.Error as e:
        logger.error(f"Database error in get_pending_files: {e}")
        return []
    except Exception as e:
        logger.error(f"Error getting pending files: {e}")
        return []

def process_single_file_parallel(file_record, collection_name, text_splitter):
    """Process a single file - can be run in parallel"""
    file_id = file_record['id']
    filename = file_record['sanitized_filename']
    file_extension = os.path.splitext(filename)[1].lower()

    # Create a temporary file to download from R2
    with tempfile.NamedTemporaryFile(suffix=file_extension, delete=False) as tmp:
        tmp_path = tmp.name

    try:
        logger.info(f"[File {file_id}] Downloading {filename} from R2 to {tmp_path}")
        if not storage_service.download_file(filename, tmp_path):
            error_msg = f"Failed to download {filename} from R2"
            logger.error(f"❌ {filename}: {error_msg}")
            update_file_status(file_id, 'error', error_message=error_msg)
            return {"success": False, "file_id": file_id, "error": error_msg}

        logger.info(f"[File {file_id}] Processing: {filename}")
        
        # Special validation for PPTX files
        if file_extension == ".pptx":
            is_valid, validation_msg = validate_pptx_file(tmp_path)
            if not is_valid:
                logger.error(f"❌ {filename}: PPTX validation failed: {validation_msg}")
                update_file_status(file_id, 'error', error_message=f"PPTX validation failed: {validation_msg}")
                return {"success": False, "file_id": file_id, "error": f"PPTX validation failed: {validation_msg}"}
        
        try:
            loader = get_document_loader(tmp_path, file_extension)  
        except Exception as e:
            error_msg = f"Failed to get loader: {str(e)}"
            logger.error(f"❌ {filename}: {error_msg}")
            update_file_status(file_id, 'error', error_message=error_msg)
            return {"success": False, "file_id": file_id, "error": error_msg}
        
        if not loader:
            error_msg = f"Unsupported file type: {file_extension}"
            logger.error(f"❌ {file_id}: {error_msg}")
            update_file_status(file_id, 'error', error_message=error_msg)
            return {"success": False, "file_id": file_id, "error": f"Unsupported file type: {file_extension}"}

        try:
            logger.info(f"[File {file_id}] Loading document...")
            if isinstance(loader, list):
                documents = loader
            elif hasattr(loader, 'load') and callable(loader.load):
                documents = loader.load()
            else:
                error_msg = f"Invalid loader object: {type(loader)}"
                logger.error(f"❌ {filename}: {error_msg}")
                update_file_status(file_id, 'error', error_message=error_msg)
                return {"success": False, "file_id": file_id, "error": error_msg}        

            if not isinstance(documents, list):
                documents = [documents] if documents else []

            if not documents:
                logger.warning(f"[File {file_id}] No content extracted from file")
                update_file_status(file_id, 'completed', error_message=None)
                return {"success": True, "file_id": file_id, "chunks": [], "status": "no_content"}
        except Exception as e:
            error_msg = f"Failed to load document: {str(e)}"
            logger.error(f"[File {file_id}] ❌ {error_msg}", exc_info=True)
            update_file_status(file_id, 'error', error_message=error_msg)
            return {"success": False, "file_id": file_id, "error": error_msg}

        valid_documents: List[Document] = [
            doc for doc in documents 
            if isinstance(doc, Document) and doc.page_content and doc.page_content.strip()
        ]
        
        if not valid_documents:
            logger.warning(f"No content extracted from {filename}")
            update_file_status(file_id, 'completed', error_message=None)
            return {"success": True, "file_id": file_id, "chunks": [], "status": "No content extracted"}
        
        try:
            logger.info(f"[File {file_id}] Splitting into chunks...")
            chunks = text_splitter.split_documents(valid_documents)

            for chunk in chunks:
                chunk.metadata['chunk_size'] = len(chunk.page_content)
                chunk.metadata['filename'] = filename
                chunk.metadata['source'] = filename # Store filename as source, not local tmp path
                chunk.metadata["file_id"] = file_id
            
            update_file_status(file_id, 'completed')
            logger.info(f"[File {file_id}] ✓ Successfully created {len(chunks)} chunks")
            return {"success": True, "file_id": file_id, 'filename': filename, "chunks": chunks}
        except Exception as e:
            error_msg = f"Failed to split chunks: {str(e)}"
            logger.error(f"[File {file_id}] ❌ {error_msg}", exc_info=True)
            update_file_status(file_id, 'error', error_message=error_msg)
            return {"success": False, "file_id": file_id, "error": error_msg}
    
    finally:
        # Cleanup temporary file
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
            logger.info(f"[File {file_id}] Cleaned up temporary file: {tmp_path}")

    
def batch_embed_optimized(vector_store, all_chunks, batch_size=BATCH_SIZE):
    """Enhanced batch embedding with better progress tracking"""
    total_chunks = len(all_chunks)
    logger.info(f" Embedding {total_chunks} chunks in optimized batches of {batch_size}")
    
    successfully_ingested_file_ids = set()
    
    for i in range(0, total_chunks, batch_size):
        batch = all_chunks[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (total_chunks + batch_size - 1) // batch_size
        
        try:
            start_time = time.time()
            vector_store.add_documents(documents=batch)
            batch_time = time.time() - start_time
            
            # Track successful file IDs
            batch_file_ids = {chunk.metadata['file_id'] for chunk in batch}
            successfully_ingested_file_ids.update(batch_file_ids)

            progress = (i +len(batch)) / total_chunks * 100
            throughput = len(batch) / batch_time if batch_time > 0 else 0

            logger.info(f" Batch {batch_num}/{total_batches}: {len(batch)} chunks embedded in {batch_time:.1f}s (Progress: {progress:.1f}%, Throughput: {throughput:.1f} chunks/s)")
        except Exception as e:
            logger.error(f"Failed to embed batch {batch_num}: {e}")
            raise

    return successfully_ingested_file_ids

def ingest_documents():
    """
    Loads documents from the specified directory, splits them into chunks,
    generates embeddings, and ingests them into a persistent Qdrant collection
    based on their access status using the ChromaService.
    """
    logger.info("--- Starting document ingestion process ---")
    start_time = time.time()
    
    pending_files = get_pending_files()
    if not pending_files:
        logger.info("No pending files to ingest.")
        return  

    logger.info(f"Found {len(pending_files)} pending files to process.")

    total_processed = 0
    total_chunks = 0
  
    collection_name = DEFAULT_COLLECTION_NAME 
    logger.info(f"Processing {len(pending_files)} files for collection: '{collection_name}'")

    try:
        vector_store = qdrant_service.get_vector_store(collection_name=collection_name)

    except Exception as e:
        logger.error(f"Failed to get vector store for collection '{collection_name}': {e}", exc_info=True)
        for file_record in pending_files:
            update_file_status(file_record['id'], parsing_status='error', error_message=f"Failed to connect to vector store: {e}")
        logger.error("❌ Ingestion aborted: Cannot proceed without vector store")
        return

    # Process and embed in streaming batches to reduce memory usage
    CHUNK_BATCH_THRESHOLD = 500  # Embed when this many chunks accumulated
    all_chunks = []
    file_processing_results = {}
    successfully_ingested_file_ids = set()
    file_processing_start = time.time()

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(pending_files))) as executor:
        # Submit all files for parallel processing
        future_to_file = {
            executor.submit(process_single_file_parallel, file_record, collection_name, text_splitter): file_record
            for file_record in pending_files
        }
        
        # Collect results as they complete
        for future in as_completed(future_to_file):
            file_record = future_to_file[future]
            try:
                result = future.result()
                file_processing_results[result.get("file_id")] = result

                if result["success"]:
                    if result.get("status") == "no_content":
                        update_file_status(result["file_id"], parsing_status='completed', embedded_status='skipped_no_content')
                        logger.info(f"✅ {file_record['sanitized_filename']}: No content to process")
                    else:
                        all_chunks.extend(result["chunks"])
                        logger.info(f"✅ {file_record['sanitized_filename']}: {len(result['chunks'])} chunks prepared")
                        
                        # Memory optimization: Embed intermediate batches when threshold reached
                        if len(all_chunks) >= CHUNK_BATCH_THRESHOLD:
                            logger.info(f"🔄 Intermediate embedding: {len(all_chunks)} chunks (threshold: {CHUNK_BATCH_THRESHOLD})")
                            try:
                                batch_file_ids = batch_embed_optimized(vector_store, all_chunks, BATCH_SIZE)
                                successfully_ingested_file_ids.update(batch_file_ids)
                                total_chunks += len(all_chunks)
                                all_chunks.clear()  # Free memory
                            except Exception as embed_err:
                                logger.error(f"Intermediate embedding failed: {embed_err}")
                else:
                    update_file_status(result["file_id"], parsing_status='error', error_message=result["error"])
                    logger.error(f"❌ {file_record['sanitized_filename']}: {result['error']}")
                    
            except Exception as e:
                logger.error(f"❌ Exception processing {file_record['sanitized_filename']}: {e}")
                update_file_status(file_record['id'], parsing_status='error', error_message=str(e))

    file_processing_time = time.time() - file_processing_start
    logger.info(f"📊 Parallel file processing completed in {file_processing_time:.1f}s for collection '{collection_name}'")

    # Process any remaining chunks that didn't reach the threshold
    if all_chunks:
        logger.info(f" Starting optimized embedding for remaining {len(all_chunks)} chunks...")
        embedding_start = time.time()
        
        try:
            batch_file_ids = batch_embed_optimized(vector_store, all_chunks, BATCH_SIZE)
            successfully_ingested_file_ids.update(batch_file_ids)
            embedding_time = time.time() - embedding_start
            
            total_chunks += len(all_chunks)
            
            throughput = len(all_chunks) / embedding_time if embedding_time > 0 else 0
            logger.info(f"🎯 Final batch completed: {len(all_chunks)} chunks in {embedding_time:.1f}s ({throughput:.1f} chunks/sec)")
            all_chunks.clear()  # Free memory
            
        except Exception as e:
            logger.error(f"Failed to embed remaining chunks for collection '{collection_name}': {e}")

            for file_record in pending_files:
                if file_record['id'] not in successfully_ingested_file_ids:
                    update_file_status(file_record['id'], parsing_status='completed', embedded_status='error', error_message="Batch embedding failed")
    elif not successfully_ingested_file_ids:
        logger.info(f" ⚠️ No chunks to ingest for collection '{collection_name}'.")
        return

    # Update successful files
    for file_id in successfully_ingested_file_ids:
        update_file_status(file_id, parsing_status='completed', embedded_status='completed')

    total_time = time.time() - start_time
    overall_throughput = total_chunks / total_time if total_time > 0 else 0

    logger.info("🎯 --- ENHANCED INGESTION COMPLETED ---")
    logger.info(f"   📁 Files Processed: {len(successfully_ingested_file_ids)}")
    logger.info(f"   📄 Total Chunks: {total_chunks}")  
    logger.info(f"   ⏱️  Total Time: {total_time:.1f}s")
    logger.info(f"   🚀 Overall Throughput: {overall_throughput:.1f} chunks/sec")
 
if __name__ == "__main__":
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    ingest_documents()