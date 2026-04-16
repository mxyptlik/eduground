import logging
import json
import os
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from auth.auth_service import get_current_user
from auth.auth_schemas import AuthenticatedUser
from chat.schemas import ChatRequest, ChatResponse
from chat.service import chain_with_history

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Chat"])

@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    current_user: AuthenticatedUser = Depends(get_current_user)
):
    try:
        session_id = request.session_id
        # Security: Derive access and department from the authenticated user token
        user_access_status = current_user.access_levels or ["public"]
        user_department = current_user.user.department

        normalized_department = None
        if user_department:
            cleaned_dept = user_department.strip()
            if cleaned_dept:
                normalized_department = cleaned_dept.lower()
        
        logger.info(f"Chat request received. Session: {session_id}, Access: {user_access_status}, Department: {normalized_department}")
        
        config = {"configurable": {"session_id": session_id}}
        
        response_generator = chain_with_history.astream(
            {
                "question": request.message,
                "access_status": user_access_status,
                "department": normalized_department
            },
            config=config
        )
        
        async def stream_response():
            """Streams RAG chain output, sending sources first, then content."""
            sources_sent = False
            async for chunk in response_generator:
                # Yield sources once if they exist in the chunk
                if not sources_sent and "documents" in chunk:
                    documents = chunk.get("documents", [])
                    if documents: # Ensure there are documents before processing
                        seen_sources = set()
                        for doc in documents:
                            source_file = os.path.basename(doc.metadata.get("source", "Unknown"))
                            department = doc.metadata.get('department', 'N/A')
                            # Create a unique key for the source to avoid duplicates
                            source_key = (source_file, department)
                            if source_key not in seen_sources:
                                seen_sources.add(source_key)
                        
                        # Format for client-side display
                        source_list = [{"name": name, "department": dept} for name, dept in seen_sources]
                        yield json.dumps({"type": "sources", "data": source_list}) + "\n"
                        sources_sent = True

                # Yield content if it exists in the chunk
                if "output" in chunk and chunk["output"]:
                    yield json.dumps({"type": "content", "data": chunk["output"]}) + "\n"

        return StreamingResponse(stream_response(), media_type="application/x-ndjson")

    except Exception as e:
        logger.error(f"Chat endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred.")
