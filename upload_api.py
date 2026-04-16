import os
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks, Form
from fastapi.responses import JSONResponse
import logging
import hashlib
import subprocess
import sys
from datetime import datetime
from config import settings
# Always set project_root to the directory containing this file (i.e., the project root)
project_root = os.path.abspath(os.path.dirname(__file__))
if project_root not in sys.path: 
    sys.path.insert(0, project_root)
from database.pg_database import DatabaseManager, get_db_manager
from upload_layer.schemas import UploadResult, UploadDetails
# Assuming database_utils.py is in the parent directory (project root)
from upload_layer.ingest import ingest_documents
from database.pg_learning_service import learning_service
from upload_layer.document_parser import DocumentChunker
from database.pg_database_manager import calculate_file_hash, add_file_record, get_file_by_hash, delete_file_record_by_hash, get_all_processed_files
import tempfile
from storage.storage_service import storage_service
router = APIRouter()

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".xls", ".csv", ".pptx", ".txt", ".html"}
# The UPLOAD_DIR should be the same as DOCS_DIR in ingest.py
UPLOAD_DIR = settings.company_docs_directory

# Ensure UPLOAD_DIR exists
os.makedirs(UPLOAD_DIR, exist_ok=True)

logger = logging.getLogger(__name__)
    
db_manager = get_db_manager(db_url=settings.DATABASE_URL)
if learning_service is None:
    logger.error("Learning service failed to initialize")
    raise RuntimeError("Learning service is not available")

def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to remove problematic characters
    
    Args:
        filename: Original filename
        
    Returns:
        Sanitized filename
    """
    name, ext = os.path.splitext(filename)
    # Remove potentially problematic characters and convert to lowercase
    name = "".join(c if c.isalnum() or c in ['_', '-'] else '_' for c in name.strip())
    return f"{name.lower()}{ext.lower()}"


def check_duplicate_file(file_hash: str) -> Optional[dict]:
    """
    Check if a file with the same hash already exists in the processed_files table
    
    Args:
        file_hash: SHA256 hash of the file
        
    Returns:
        File record dictionary if found, None otherwise
    """
    try:
        return get_file_by_hash(file_hash)
    except Exception as e:
        logger.error(f"Error checking for duplicate file: {e}")
        return None
    
def trigger_ingestion_background():
    """
    Wrapper function to run the ingestion process.
    This will be run by FastAPI's BackgroundTasks.
    """
    logger.info("Background task started: Triggering document ingestion...")
    try:
        # Call the function directly
        ingest_documents()
        logger.info("Background task finished: Document ingestion completed successfully.")
    except Exception as e:
        # This will log any error from inside the ingest_documents function
        logger.error(f"Background ingestion task failed: {e}", exc_info=True)



@router.post("/upload", response_model=UploadResult)
async def upload_files(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    category: str = Form(..., description="Department associated with the files"),
    section_title: str = Form(..., description="Title of the section"),
    section_description: str = Form(..., description="Description of the section"),
):
    """
    Handles file uploads, saves them to Cloudflare R2, and triggers the ingestion process.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files were provided.")

    uploaded_files_details: List[UploadDetails] = []
    total_size = 0

    for file in files:
        sanitized_name = sanitize_filename(file.filename)
        file_extension = os.path.splitext(sanitized_name)[1]

        if file_extension not in ALLOWED_EXTENSIONS:
            logger.warning(f"Skipping file with disallowed extension: {file.filename}")
            continue

        try:
            # We'll use a temporary file to calculate hash and upload to R2
            # Add suffix so that DocumentChunker can identify the file format correctly
            with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as tmp:
                sha256_hash = hashlib.sha256()
                total_size_bytes = 0
                
                while True:
                    chunk = await file.read(1024 * 1024) # Read 1MB chunks
                    if not chunk:
                        break
                    tmp.write(chunk)
                    sha256_hash.update(chunk)
                    total_size_bytes += len(chunk)
                
                tmp_path = tmp.name
            
            file_hash = sha256_hash.hexdigest()
            file_size = total_size_bytes
            total_size += file_size

            # Check for duplicates in processed_files (source of truth for successful processing)
            existing_record = check_duplicate_file(file_hash)
            if existing_record:
                logger.info(f"Skipping duplicate file: {file.filename} (hash: {file_hash})")
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                uploaded_files_details.append(
                    UploadDetails(
                        id=str(existing_record.get('id')), # Use hash record ID as string
                        filename=file.filename,
                        sanitized_filename=sanitized_name,
                        file_type=file.content_type or "application/octet-stream",
                        file_size=file_size,
                        file_hash=file_hash,
                        status="skipped_duplicate",
                        message=f"File already exists (processed). Original: {existing_record.get('original_filename')}",
                        category=existing_record.get('department', category)
                    )
                )
                continue

            # Upload to Cloudflare R2
            logger.info(f"Uploading {file.filename} to Cloudflare R2...")
            with open(tmp_path, "rb") as f:
                storage_url = storage_service.upload_file(f, sanitized_name, file.content_type)
            
            if not storage_url:
                raise Exception("Failed to upload file to Cloudflare R2")

            # Create document in DB
            document_data = {
                "title": file.filename,
                "description": (
                    f"Uploaded document. "
                    f"File: {sanitized_name}, "
                    f"Size: {file_size} bytes, "
                    f"Type: {file_extension}, "
                    f"hash:{file_hash}"
                ),
                "category": category
            }
            
            document_id = learning_service.create_document(document_data)
            
            section_data = {
                "document_id": document_id,
                "title": section_title if section_title else f"Section for {file.filename}",
                "description": section_description if section_description else f"Initial section for uploaded document {file.filename}",
                "prerequisite_section_id": None
            }
            section_id = learning_service.create_section(section_data)

            # Process document chunking (extract lessons/levels)
            chunker = DocumentChunker(section_id=section_id, db_manager=db_manager)
            level_records = chunker.process_document(
                file_path=tmp_path,
                save_files=False,
                insert_to_db=True
            )
            
            if level_records:
                chunker._summary(level_records)

            # Add file record to processed_files table
            try:
                add_file_record(
                    original_filename=file.filename,
                    sanitized_filename=sanitized_name,
                    file_hash=file_hash,
                    file_extension=file_extension,
                    department=category,
                    access_status="public",
                    document_id=document_id
                )
                logger.info(f"Successfully added file record for {file.filename}")
            except Exception as e:
                logger.error(f"Failed to add {sanitized_name} to file metadata db: {e}")

            uploaded_files_details.append(
                UploadDetails(
                    id=document_id,
                    filename=file.filename,
                    sanitized_filename=sanitized_name,
                    file_type=file.content_type or "application/octet-stream",
                    file_size=file_size,
                    file_hash=file_hash,
                    status="uploaded",
                    message=f"File uploaded successfully to R2. Document ID: {document_id}.",
                    category=category
                )
            )

            # Cleanup temporary file
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
                logger.info(f"Cleaned up temporary file: {tmp_path}")

        except Exception as e:
            logger.error(f"Failed to process or save file {file.filename}: {e}", exc_info=True)
            
            # Cleanup DB records if they were partially created
            # This is critical for atomicity - if processing fails, we don't want a "zombie" document
            if 'document_id' in locals() and document_id:
                try:
                    logger.info(f"Attempting cleanup of partial document: {document_id}")
                    learning_service.delete_document(document_id)
                    logger.info(f"Successfully cleaned up partial document {document_id}")
                except Exception as cleanup_err:
                    logger.error(f"Failed to cleanup partial document {document_id}: {cleanup_err}")

            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                os.remove(tmp_path)
            
            uploaded_files_details.append(
                UploadDetails(
                    filename=file.filename,
                    sanitized_filename=sanitized_name,
                    file_type=file.content_type or "application/octet-stream",
                    file_size=0,
                    status="error",
                    message=f"An error occurred: {str(e)}",
                    category=category
                )
            )

    if not uploaded_files_details:
        raise HTTPException(status_code=400, detail="No valid files were processed.")

    successful_uploads = sum(1 for detail in uploaded_files_details if detail.status == "uploaded")
    if successful_uploads > 0:
        background_tasks.add_task(trigger_ingestion_background)
        logger.info("Added ingestion task to background.")

    return UploadResult(
        message="File upload process finished. Ingestion started in the background.",
        total_files=len(files),
        total_size=total_size,
        uploads=uploaded_files_details
    )

@router.get("/documents", summary="List all uploaded documents", tags=["File Operations"])
async def list_uploaded_documents():
    """
    List all documents from the database
    
    Returns documents with fields from schema:
    - document_id, title, description, category, created_at, updated_at
    """
    try:
        documents = learning_service.list_documents()
        
        if not documents:
            return JSONResponse(
                content={
                    "documents": [], 
                    "message": "No documents found in the database."
                }, 
                status_code=200
            )
        
        logger.info(f"Found {len(documents)} documents in database.")
        formatted_docs = []
        for doc in documents:
            # created_at_str = doc.get('created_at') if isinstance(doc.get('created_at'), str) else (doc.get('created_at').isoformat() if doc.get('created_at') else None)
            # updated_at_str = doc.get('updated_at') if isinstance(doc.get('updated_at'), str) else (doc.get('updated_at').isoformat() if doc.get('updated_at') else None)
            
            formatted_docs.append({
                "document_id": doc.get('document_id'),
                "title": doc.get('title'),
                "description": doc.get('description'),
                "category": doc.get('category'),
                "created_at": doc.get('created_at'),
                "updated_at": doc.get('updated_at')
            })
        
        return {
            "documents": formatted_docs,
            "total": len(formatted_docs)
        }
        
    except Exception as e:
        logger.error(f"Error listing documents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while retrieving the document list.")



@router.get("/documents/{document_id}", summary="Get document by ID", tags=["File Operations"])
async def get_document_by_id(document_id: str):
    """
    Get a specific document by its ID
    
    Returns document with schema fields:
    - document_id, title, description, category, created_at, updated_at
    """
    try:
        document = learning_service.get_document(document_id)
        
        if not document:
            raise HTTPException(status_code=404, detail=f"Document with ID '{document_id}' not found.")
        
        return {
            "document": document,
            "message": "Document retrieved successfully."
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting document {document_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while retrieving the document.")


@router.put("/documents/{document_id}", summary="Update document", tags=["File Operations"])
async def update_document(
    document_id: str,
    confirm: bool = Form(..., description="Set to true to confirm update"),
    title: str = Form(None),
    description: str = Form(None),
    category: str = Form(None)
):
    """
    Update a document
    
    Only updates fields from schema:
    - title (VARCHAR 255)
    - description (TEXT)
    - category (VARCHAR 100)
    
    updated_at will be automatically updated
    """
    try:
        # Verify document exists
        document = learning_service.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found")
        update_data = {}
        if title is not None:
            update_data["title"] = title
        if description is not None:
            update_data["description"] = description
        if category is not None:
            update_data["category"] = category
        
        if not update_data:
            raise HTTPException(status_code=400, detail="No update data provided")

        success = learning_service.update_document(document_id, update_data)

        if success:
            return {
                "success": True,
                "message": "Document updated successfully",
                "document": learning_service.get_document(document_id)
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to update document")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating document {document_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while updating the document.")


@router.delete("/documents/{document_id}", summary="Delete document", tags=["File Operations"])
async def delete_document(document_id: str):
    """
    Delete a document from the database
    
    Args:
        document_id: Document identifier
        
    Returns:
        Success message
    """
    try:
        # Get document before deletion to check if it exists
        document = learning_service.get_document(document_id)
        
        if not document:
            raise HTTPException(status_code=404, detail=f"Document with ID '{document_id}' not found.")
        
        # Delete the document
        success = learning_service.delete_document(document_id)
        
        if success:
            logger.info(f"Document {document_id} deleted successfully.")
            return {
                "message": f"Document '{document.get('title')}' deleted successfully.",
                "document_id": document_id
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to delete document.")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting document {document_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while deleting the document.")


@router.get("/stats", summary="Get upload statistics", tags=["File Operations"])
async def get_upload_stats():
    """
    Get statistics about uploaded documents
    
    Returns:
        Statistics about documents and files
    """
    try:
        # Get database stats
        db_stats =learning_service.get_database_stats()
        
        # Get file directory stats
        file_count = 0
        total_file_size = 0
        
        if os.path.exists(UPLOAD_DIR) and os.path.isdir(UPLOAD_DIR):
            files_in_directory = [
                f for f in os.listdir(UPLOAD_DIR) 
                if os.path.isfile(os.path.join(UPLOAD_DIR, f))
            ]
            file_count = len(files_in_directory)
            total_file_size = sum(
                os.path.getsize(os.path.join(UPLOAD_DIR, f)) 
                for f in files_in_directory
            )
        
        return {
            "database_stats": db_stats,
            "file_stats": {
                "total_files": file_count,
                "total_size_bytes": total_file_size,
                "total_size_mb": round(total_file_size / (1024 * 1024), 2),
                "upload_directory": UPLOAD_DIR
            }
        }
    except Exception as e:
        logger.error(f"Error getting upload stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while retrieving statistics.")

@router.delete("/files/{file_hash}", summary="Delete file record by hash", tags=["File Operations"])
async def delete_file_by_hash(file_hash: str):
    """
    Delete a file record from the processed_files table by its hash.
    This allows re-uploading a file if it previously failed during processing.
    """
    success = delete_file_record_by_hash(file_hash)
    if success:
        return {"success": True, "message": f"File record with hash {file_hash} deleted successfully."}
    else:
        raise HTTPException(status_code=404, detail=f"No file record found with hash {file_hash}")


@router.get("/processed-files", summary="List all processed files", tags=["File Operations"])
async def list_processed_files(limit: int = 500, offset: int = 0):
    """
    Get all records from the processed_files table.
    
    Returns all file processing records including:
    - id, original_filename, sanitized_filename, file_hash
    - file_extension, upload_timestamp, parsing_status
    - embedded_status, department, access_status, document_id
    
    Args:
        limit: Maximum number of records to return (default 500)
        offset: Number of records to skip for pagination (default 0)
    """
    try:
        records = get_all_processed_files(limit=limit, offset=offset)
        
        if not records:
            return JSONResponse(
                content={
                    "processed_files": [],
                    "message": "No processed files found.",
                    "total": 0
                },
                status_code=200
            )
        
        logger.info(f"Retrieved {len(records)} processed file records.")
        return {
            "processed_files": records,
            "total": len(records),
            "limit": limit,
            "offset": offset
        }
        
    except Exception as e:
        logger.error(f"Error listing processed files: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while retrieving processed files.")
