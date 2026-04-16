from pydantic import BaseModel, Field, validator
from typing import List, Optional
import re
import html

class ChatRequest(BaseModel):
    session_id: str = Field(
        ..., 
        min_length=1,
        max_length=100, 
        description="Session ID",
        pattern="^[a-zA-Z0-9_-]+$")


        sanitized = html.escape(v.strip())

        suspicious_patterns = [
            r'<script',
            r'javascript:',
            r'data:text/html',
            r'vbscript:',
            r'onload=',
            r'onerror='
        ]

        for pattern in suspicious_patterns:
            if re.search(pattern, sanitized, re.IGNORECASE):
                raise ValueError("Message contains invalid content")
        
        return sanitized
    
    @validator('session_id')
    def validate_session_id(cls, v):
        if not v or not v.strip():
            raise ValueError('Session ID cannot be empty')
    sources: Optional[List[str]] = None
    type: str
    company_id: Optional[str] = None  # Add company_id for multi-tenancy

class StreamEvent(BaseModel):
    content: Optional[str] = None
    complete: Optional[bool] = None
    error: Optional[str] = None


class FileMetadata(BaseModel):
    filename: str
    path: str
    size: int
    file_type: str
    timestamp: str

class Chunk(BaseModel):
    page_content: str
    metadata: dict

class UploadDetails(BaseModel):
    """Details for a single file in an upload operation."""
    id: Optional[int] = None
    filename: str
    sanitized_filename: Optional[str] = None
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    file_hash: Optional[str] = None
    status: str
    message: str
    department: Optional[str] = None
    access_status: Optional[str] = None

class UploadResult(BaseModel):
    """Overall result of a file upload operation."""
    message: str
    total_files: int
    total_size: int
    uploads: List[UploadDetails]