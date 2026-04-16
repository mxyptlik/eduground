
import psycopg2
from psycopg2.extras import RealDictCursor
import hashlib
import json
import os
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from config import settings
from contextlib import contextmanager

# Configure logger
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

DATABASE_URL = settings.DATABASE_URL
CHUNKS_DIR = settings.UPLOAD_DIR / "chunks"
CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

@contextmanager
def get_db_connection():
    """
    Yields a PostgreSQL database connection.
    This replaces the SQLite get_db_connection.
    """
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        yield conn
    except psycopg2.Error as e:
        logger.error(f"Failed to connect to PostgreSQL: {e}", exc_info=True)
        raise
    finally:
        if conn:
            conn.close()

def initialize_db():
    """
    Initializes the database schema.
    In PG migration, this is largely handled by pg_database.py, 
    but we can ensure the table exists here too.
    """
    # This is redundant if pg_database.py is initialized, but safe to keep.
    # We'll rely on pg_database.py for schema creation to avoid duplication.
    pass

def calculate_file_hash(file_path: str) -> Optional[str]:
    """Calculates SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception as e:
        logger.error(f"Error hashing file {file_path}: {e}", exc_info=True)
        return None

def add_file_record(original_filename: str, sanitized_filename: str, file_hash: str, file_extension: str, department: Optional[str] = None, access_status: Optional[str] = None, document_id: Optional[str] = None) -> Optional[int]:
    """Adds a file record to the database. Returns the record ID or None on failure."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Use UTC for all timestamps
                upload_timestamp = datetime.now(timezone.utc)

                cursor.execute("""
                    INSERT INTO processed_files (original_filename, sanitized_filename, file_hash, file_extension, upload_timestamp, department, access_status, document_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (original_filename, sanitized_filename, file_hash, file_extension, upload_timestamp, department, access_status, document_id))
                
                record_id = cursor.fetchone()['id']
                conn.commit()
                logger.info(f"Successfully added file record for '{original_filename}' with ID {record_id}, document_id: {document_id}.")
                return record_id
    except psycopg2.IntegrityError:
        logger.warning(f"A file with the same sanitized name or hash already exists: '{sanitized_filename}'")
        return None
    except Exception as e:
        logger.error(f"Database error while adding file record: {e}", exc_info=True)
        return None

def get_file_by_hash(file_hash: str) -> Optional[dict]:
    """Retrieves a file record from the database by its hash."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM processed_files WHERE file_hash = %s", (file_hash,))
                return cursor.fetchone()
    except Exception as e:
        logger.error(f"Database error fetching file by hash: {e}", exc_info=True)
        return None

def get_files_for_embedding(limit: Optional[int] = None) -> list[dict]:
    """Retrieves records of files that have been successfully parsed but not yet embedded."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                query = "SELECT * FROM processed_files WHERE parsing_status = 'completed' AND embedded_status = 'pending'"
                if limit:
                    query += f" LIMIT {limit}"
                cursor.execute(query)
                return cursor.fetchall()
    except Exception as e:
        logger.error(f"Database error fetching files for embedding: {e}", exc_info=True)
        return []

def get_file_by_sanitized_path(file_path: str) -> Optional[dict]:
    """Retrieves a file record from the database by its sanitized file path."""
    sanitized_filename = os.path.basename(file_path)
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM processed_files WHERE sanitized_filename = %s", (sanitized_filename,))
                return cursor.fetchone()
    except Exception as e:
        logger.error(f"Database error getting file by path: {e}", exc_info=True)
        return None

def update_file_status(
    file_id: int, 
    parsing_status: Optional[str] = None, 
    embedded_status: Optional[str] = None, 
    chunk_file_path: Optional[str] = None, 
    error_message: Optional[str] = None
) -> bool:
    """Updates the status of a file record in the database identified by its ID."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                last_processed_time = datetime.now(timezone.utc)
                
                update_fields = {}
                values = []
                
                if parsing_status is not None:
                    update_fields["parsing_status"] = parsing_status
                if chunk_file_path is not None:
                    update_fields["chunk_file_path"] = chunk_file_path
                if error_message is not None:
                    update_fields["error_message"] = error_message
                if embedded_status is not None:
                    update_fields["embedded_status"] = embedded_status
                
                # Timestamp update
                update_fields["last_processed_timestamp"] = last_processed_time

                if parsing_status == 'pending' and error_message is None:
                    update_fields["error_message"] = None # Will set to NULL
                
                set_clauses = []
                for field, value in update_fields.items():
                    set_clauses.append(f"{field} = %s")
                    values.append(value)
                
                values.append(file_id)
                query = f"UPDATE processed_files SET {', '.join(set_clauses)} WHERE id = %s"
                
                cursor.execute(query, tuple(values))
                conn.commit()
                
                if cursor.rowcount == 0:
                    logger.warning(f"No record found with ID {file_id} to update.")
                    return False
                    
                logger.info(f"Updated record {file_id}")
                return True
    except Exception as e:
        logger.error(f"Database error updating file status for ID {file_id}: {e}", exc_info=True)
        return False

def update_file_status_by_path(file_path: str, **kwargs) -> bool:
    """Wrapper to update file status using the file path instead of hash."""
    file_hash = calculate_file_hash(file_path)
    if not file_hash:
        record = get_file_by_sanitized_path(file_path)
        if record:
            file_hash = record['file_hash']
        else:
            return False
    
    # Needs file_id, not hash to call update_file_status? 
    # Wait, update_file_status takes file_id. 
    # Original database_manager.py called update_file_status(file_hash, ...) ? 
    # No, original database_manager.py: `update_file_status(file_hash, ...)`??
    # Let me check original code. 
    # Original: `def update_file_status_by_path(file_path, **kwargs): ... return update_file_status(file_hash, **kwargs)`
    # BUT `update_file_status` signature was `(file_id: int...)`. 
    # This implies original code might have had a bug or loose typing, OR `update_file_status` handled hash too?
    # Let me re-read original `database_manager.py`.
    
    # Checking Step 137:
    # def update_file_status(file_id: int, ...) -> bool:
    # Query: "UPDATE processed_files ... WHERE id = ?"
    # So it expects ID.
    # BUT `update_file_status_by_path` calls `update_file_status(file_hash, ...)`!
    # This looks like a BUG in the original code! Passing file_hash string to a function expecting file_id int, and using `WHERE id = ?`.
    # SQLite is loosely typed, maybe it worked if id column matched hash? No, id is int autoinc.
    # THIS IS A BUG found in migration.
    # I should fix it. I need to get ID from hash.
    
    record = get_file_by_hash(file_hash)
    if not record:
        return False
    return update_file_status(record['id'], **kwargs)


def save_chunks_to_json(file_hash: str, chunks: list[dict]) -> Optional[str]:
    """Saves document chunks to a JSON file."""
    if not chunks:
        return None

    try:
        sorted_chunks = sorted(chunks, key=lambda x: x.get('chunk_index', 0))
    except:
        sorted_chunks = chunks

    file_dir = os.path.join(CHUNKS_DIR, file_hash[:2])
    os.makedirs(file_dir, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    safe_hash = file_hash.replace('/', '_')
    json_file_path = os.path.join(file_dir, f"{safe_hash}_{timestamp}_chunks.json")

    try:
        with open(json_file_path, 'w', encoding='utf-8') as json_file:
            json.dump(sorted_chunks, json_file, ensure_ascii=False, indent=4)
        logger.info(f"Saved {len(sorted_chunks)} chunks to {json_file_path}")
        return json_file_path
    except Exception as e:
        logger.error(f"Error saving chunks: {e}", exc_info=True)
        return None
def delete_file_record_by_hash(file_hash: str) -> bool:
    """Deletes a file record from the database by its hash."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM processed_files WHERE file_hash = %s", (file_hash,))
                count = cursor.rowcount
                conn.commit()
                if count > 0:
                    logger.info(f"Successfully deleted file record for hash {file_hash}")
                    return True
                else:
                    logger.warning(f"No file record found with hash {file_hash}")
                    return False
    except Exception as e:
        logger.error(f"Database error deleting file record by hash: {e}", exc_info=True)
        return False

def get_all_processed_files(limit: int = 500, offset: int = 0) -> list[dict]:
    """Retrieves all processed file records from the database."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT * FROM processed_files 
                    ORDER BY upload_timestamp DESC 
                    LIMIT %s OFFSET %s
                """, (limit, offset))
                return cursor.fetchall()
    except Exception as e:
        logger.error(f"Database error fetching processed files: {e}", exc_info=True)
        return []
