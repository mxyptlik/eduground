import os
import logging
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from qdrant_client import QdrantClient
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from chat.schemas import ChatRequest, ChatResponse
from chat.chat_service import chat_service
from core.vector_db_services import vector_db_service
from core.redis_manager import RedisSessionManager
from core.rate_limiter import chat_rate_limit
from config import settings

from auth.auth_service import get_current_user
from auth.auth_schemas import AuthenticatedUser
from auth.auth_service import require_admin
from core.audit_logger import audit, AuditEventType

logger = logging.getLogger(__name__)
security = HTTPBearer()
router = APIRouter(prefix="/chat", tags=["Chat"])

@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest, req: Request, current_user: AuthenticatedUser = Depends(get_current_user),_: bool = Depends(chat_rate_limit)):
    """Main chat endpoint with streaming response"""
    try:
        audit.log_event(
            AuditEventType.CHAT_REQUEST,
            user_id=current_user.user.id,
            company_id=current_user.company.id,
            ip_address=req.client.host,
            details={"session_id": request.session_id, "message_length": len(request.message), "department": request.department}
        )
   
        try:
            user_access_status = current_user.access_levels or ["public"]
            user_department = current_user.user.department
            company_id = current_user.company.id

            logger.info(f"Authenticated chat request - User: {current_user.user.email}, Company: {current_user.company.name}")

            response_stream = chat_service.stream_chat_response(
                session_id=request.session_id,
                message=request.message,
                access_status=user_access_status,
                department=user_department,
                company_id=company_id
            )
            
            return StreamingResponse(response_stream, media_type="application/x-ndjson")

        except Exception as e:
            logger.error(f"Chat endpoint error for user {current_user.user.email}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="An internal error occurred.")
        
    except Exception as e:
       audit.log_event(
            AuditEventType.CHAT_REQUEST,
            user_id=current_user.user.id,
            company_id=current_user.company.id,
            ip_address=req.client.host,
            details={"session_id": request.session_id, "error": str(e)}
        )
       raise

@router.delete("/session/{session_id}")
async def delete_session(session_id: str, current_user: AuthenticatedUser = Depends(get_current_user)):
    """Delete a user's session history from Redis"""
    
    try:
        redis_manager = RedisSessionManager()
        await redis_manager.delete_history(session_id)
        logger.info(f"Successfully deleted session: {session_id}")
        return {"status": "success", "message": f"Session {session_id} deleted by user {current_user.user.email}."}
    except Exception as e:
        logger.error(f"Error deleting session {session_id} for user {current_user.user.email}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete session.")

# Debug endpoints
@router.get("/debug/collections")
async def debug_collections(current_user: AuthenticatedUser = Depends(require_admin)):
    """Check what collections exist in vector store"""
    try:
        logger.info(f"Admin {current_user.user.email} accessing debug collections")
        qdrant_path = settings.qdrant_database_path
        client = QdrantClient(path=qdrant_path)
        collections = client.get_collections()
        
        result = []
        for collection in collections.collections:
            info = client.get_collection(collection.name)
            result.append({
                "name": collection.name,
                "vectors_count": info.vectors_count,
                "status": info.status
            })
        
        logger.info(f"Found {len(result)} collections: {[c['name'] for c in result]}")
        return {"collections": result}
        
    except Exception as e:
        logger.error(f"Failed to list collections: {e}")
        raise HTTPException(status_code=500, detail="Debug request failed.")

@router.get("/debug/test-retrieval/{collection_name}")
async def debug_test_retrieval(collection_name: str, query: str = "test employee",current_user: AuthenticatedUser = Depends(require_admin) ):
    """Test retrieval from specific collection"""
    try:
        logger.info(f"Admin {current_user.user.email} testing retrieval from '{collection_name}' with query {query}")
        
        vector_store = vector_db_service.get_vector_store(collection_name)
        logger.info(f"Got vector store: {type(vector_store)}")
        
        retriever = vector_store.as_retriever(search_kwargs={"k": 3})
        docs = retriever.invoke(query)
        
        result = {
            "collection": collection_name,
            "query": query,
            "retrieved_count": len(docs),
            "documents": []
        }
        
        for i, doc in enumerate(docs):
            result["documents"].append({
                "index": i,
                "content_preview": doc.page_content[:200],
                "metadata": doc.metadata
            })
        
        logger.info(f"Retrieved {len(docs)} documents")
        return result
        
    except Exception as e:
        logger.error(f"Retrieval test failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Debug request failed.")

@router.get("/debug/departments/{collection_name}")
async def debug_departments(collection_name: str, current_user: AuthenticatedUser = Depends(require_admin)):
    """Check what department values exist in the collection"""
    try:
        vector_store = vector_db_service.get_vector_store(collection_name)
        retriever = vector_store.as_retriever(search_kwargs={"k": 50})
        docs = retriever.invoke("employee")
        
        departments = set()
        for doc in docs:
            dept = doc.metadata.get('department')
            departments.add(dept)
        
        return {
            "collection": collection_name,
            "total_docs": len(docs),
            "unique_departments": list(departments),
            "sample_metadata": [doc.metadata for doc in docs[:3]]
        }
        
    except Exception as e:
        logger.error(f"Department debug failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Debug request failed.")

@router.post("/debug/request-format")
async def debug_request_format(request: ChatRequest, current_user: AuthenticatedUser = Depends(require_admin)):
    """Debug what request format is being received"""
    return {
        "message": request.message,
        "session_id": request.session_id,
        "user": current_user.user.email,
        "company": current_user.company.name,
        "access_levels": current_user.access_levels
    }

@router.get("/debug/metadata/{collection_name}")
async def debug_metadata(collection_name: str, current_user: AuthenticatedUser = Depends(require_admin)):
    """Check exact metadata structure using existing service"""
    try:
        vector_store = vector_db_service.get_vector_store(collection_name)
        retriever = vector_store.as_retriever(search_kwargs={"k": 10})
        docs = retriever.invoke("test")
        
        metadata_samples = []
        departments = set()
        
        for i, doc in enumerate(docs[:5]):
            metadata = doc.metadata if doc.metadata else {}
            dept = metadata.get("department")
            
            departments.add(dept if dept is not None else "None")
            
            metadata_samples.append({
                "doc_index": i,
                "payload_keys": list(metadata.keys()),
                "department_value": dept,
                "department_type": type(dept).__name__,
                "full_metadata": metadata,
                "content_preview": doc.page_content[:100] if doc.page_content else ""
            })
        
        return {
            "collection": collection_name,
            "total_docs_checked": len(docs),
            "unique_departments": [d for d in departments if d],
            "metadata_samples": metadata_samples
        }
        
    except Exception as e:
        logger.error(f"Debug metadata failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Debug request failed.")
