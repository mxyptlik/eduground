import os
import logging
import asyncio
from dotenv import load_dotenv
import json
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi import APIRouter,Request
from typing import Optional, Union
from qdrant_client.models import Filter, FieldCondition, MatchValue

# Langchain Imports
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough, RunnableLambda, RunnableWithMessageHistory

# Utility Imports
from rapidfuzz import process, fuzz 
from chat.schemas import ChatRequest, ChatResponse
from core.redis_manager import RedisSessionManager  
import time
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from auth.auth_api import router as auth_router
from auth.auth_service import get_current_user
from auth.auth_schemas import AuthenticatedUser
from fastapi import Depends
# Local Imports
from core.vector_db_services import vector_db_service, QdrantService
from core.database_utils import initialize_db
from files.upload_api import router as upload_router
from core.ingestion_scheduler import run_scheduler_in_background


load_dotenv()
                                                                                                                                                                                                                                                                    
# Initialize logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize OpenTelemetry tracing
tracer_provider = TracerProvider()
tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
trace.set_tracer_provider(tracer_provider)

# Disable anonymous telemetry
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_TELEMETRY_ENABLED"] = "false"

# --- Environment Variable Configuration ---
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
SYSTEM_PROMPT_PATH = os.getenv("SYSTEM_PROMPT_PATH", "system_prompt.txt")
CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:5500,http://localhost:5500").split(',')
TRUSTED_HOSTS = [
    host.strip()
    for host in os.getenv("TRUSTED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

# Load system prompt
try:
    with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT = f.read().strip()
        logger.info(f"Loaded system prompt from {SYSTEM_PROMPT_PATH}")
except FileNotFoundError:
    logger.warning(f"{SYSTEM_PROMPT_PATH} not found; using default prompt.")
    SYSTEM_PROMPT = (
        "You are Bella, Heckerbella's AI assistant. Use provided context to answer queries accurately and conversationally. "
        "If context doesn't contain the answer, say so. Always be friendly and include a relevant call-to-action."
    )
except Exception as e:
    logger.error(f"Error loading system prompt: {e}", exc_info=True)
    SYSTEM_PROMPT = "Error: Could not load system prompt."

# Redis session manager
redis_manager = RedisSessionManager()

# --- RAG Chain Components ---
def format_docs(docs):
    if not docs:
        logger.info("🔍 format_docs: No documents provided")
        return "No context available."

    logger.info(f"🔍 format_docs: Formatting {len(docs)} documents")

    result = "\n\n".join(
        f"Source: {os.path.basename(doc.metadata.get('source', 'Unknown'))}\n"
        f"Department: {doc.metadata.get('department', 'N/A')}\n" # Include department
        f"Content: {doc.page_content}"
        for doc in docs
    )
    logger.info(f"🔍 format_docs: Generated context length: {len(result)} chars") 
    logger.info(f"🔍 format_docs: Context preview: {result[:200]}...")
    return result


def get_retriever_for_request(access_status: list[str], k: int = 10, department: Optional[str] = None):
    """
    Dynamically gets a retriever for the specified access levels and department.   
    """
    # All users get access to the public collection
    collections_to_query = set(access_status)
    collections_to_query.add("public")  # Ensure public is always included
    
    primary_collection = access_status[0] if access_status else "public"
    logger.info(f"🔍 Getting retriever for collection: '{primary_collection}', department: '{department}'")
    
    # Get the base vector store
    vector_store = vector_db_service.get_vector_store(primary_collection)
    
    # If no department filtering needed, return simple retriever
    if not department or department.lower() in ["general", "all", ""]:
        logger.info(f"🔍 No department filter applied - returning all documents")
        return vector_store.as_retriever(search_kwargs={'k': k})
    
    # Create custom retriever with manual department filtering
    class DepartmentFilteredRetriever:
        def __init__(self, vector_store, target_department, k):
            self.vector_store = vector_store
            self.target_department = target_department.lower()
            self.k = k
        
        def invoke(self, query):
            # Get more documents than needed to allow for filtering
            import  gc
            base_retriever = self.vector_store.as_retriever(search_kwargs={'k': self.k * 5})
            all_docs = base_retriever.invoke(query)
            
            logger.info(f"🔍 Retrieved {len(all_docs)} documents before department filtering")
            
            # Manual filtering by department
            filtered_docs = []
            processed_count = 0

            for i, doc in enumerate(all_docs):
                doc_department = doc.metadata.get('department', '').lower()
                logger.info(f"🔍 Checking doc department: '{doc_department}' vs target: '{self.target_department}'")
                
                if doc_department == self.target_department:
                    filtered_docs.append(doc)

                else:
                    # Clear processed document immediately if not needed
                    all_docs[i] = None
                processed_count += 1    
            
            all_docs.clear()
            del all_docs
            gc.collect()
                
            # Return the requested number of filtered documents
            result = filtered_docs[:self.k]
            if len(filtered_docs) > self.k:
                excess_count = len(filtered_docs) - self.k
                filtered_docs = None
                gc.collect()
            logger.info(f"🔍 Returning {len(result)} documents to chain")
            return result
    
    logger.info(f"🔍 Using manual department filtering for: '{department}'")
    return DepartmentFilteredRetriever(vector_store, department, k)

# --- Application Setup ---
app = FastAPI(
    title="Heckermind AI",
    description="API for interacting with Heckermind.",
    version="1.0.0"
)

# Add Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=TRUSTED_HOSTS)
app.include_router(upload_router, prefix="/api/files")
app.include_router(auth_router)
# Instrument FastAPI for OpenTelemetry
FastAPIInstrumentor.instrument_app(app)

# --- Langchain RAG Chain ---
llm = ChatOpenAI(model=LLM_MODEL, temperature=0.6, streaming=True)

# Main RAG prompt
rag_prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    MessagesPlaceholder(variable_name="history"),
    ("human", "Context:\n{context}\n\nQuestion: {question}")
])

# RAG chain that passes documents through for source citation.
# It's structured to preserve the input 'question' and 'history' at each step.
rag_chain = (
    RunnablePassthrough.assign(
        documents=lambda x: get_retriever_for_request(x.get("access_status", ["public"]),
                                                       department=x.get("department")
                                                       ).invoke(x["question"])
    )
    |RunnablePassthrough.assign(context=lambda x: format_docs(x["documents"]))
    |RunnablePassthrough.assign(output=rag_prompt | llm | StrOutputParser())
)

# Chain with message history management
chain_with_history = RunnableWithMessageHistory(
    rag_chain,
    redis_manager.get_session_history_sync,
    input_messages_key="question",
    history_messages_key="history",
    output_messages_key="output",
)

# --- API Endpoints ---
@app.post("/chat", response_model=ChatResponse)
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
        # 2. Execute RAG chain for streaming response
        config = {"configurable": {"session_id": session_id}}
        
        response_generator = chain_with_history.astream(
            {
                "question": request.message,
                "access_status": user_access_status, # Pass access status to the chain
                "department": normalized_department  # Pass department to the chain
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

@app.get("/debug/collections")
async def debug_collections(current_user: AuthenticatedUser = Depends(get_current_user)):
    """Check what collections exist in vector store"""
    try:
        from qdrant_client import QdrantClient
        
        qdrant_path = "./qdrant_db"
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
        
        logger.info(f"User {current_user.user.email} inspected {len(result)} collections")
        return {"collections": result}
        
    except Exception as e:
        logger.error(f"Failed to list collections: {e}")
        raise HTTPException(status_code=500, detail="Debug request failed.")

@app.get("/debug/test-retrieval/{collection_name}")
async def debug_test_retrieval(
    collection_name: str,
    query: str = "test employee",
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Test retrieval from specific collection"""
    try:
        logger.info(f"User {current_user.user.email} testing retrieval from '{collection_name}'")
        
        # Get vector store
        vector_store = vector_db_service.get_vector_store(collection_name)
        logger.info(f"Got vector store: {type(vector_store)}")
        
        # Test retrieval
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

@app.get("/debug/departments/{collection_name}")
async def debug_departments(collection_name: str, current_user: AuthenticatedUser = Depends(get_current_user)):
    """Check what department values exist in the collection"""
    try:
        vector_store = vector_db_service.get_vector_store(collection_name)
        retriever = vector_store.as_retriever(search_kwargs={"k": 50})  # Get many docs
        docs = retriever.invoke("employee")  # Use a broad search
        
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
        logger.error(f"Department debug failed for user {current_user.user.email}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Debug request failed.")

@app.post("/debug/request-format")
async def debug_request_format(request: ChatRequest, current_user: AuthenticatedUser = Depends(get_current_user)):
    """Debug what request format is being received"""
    return {
        "message": request.message,
        "session_id": request.session_id,
        "user": current_user.user.email,
        "access_status": request.access_status,
        "department": request.department,
        "department_type": type(request.department).__name__,
        "department_value": repr(request.department),
    }

@app.get("/debug/metadata/{collection_name}")
async def debug_metadata(collection_name: str, current_user: AuthenticatedUser = Depends(get_current_user)):
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
            
            # Handle None department values
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
            "unique_departments": [d for d in departments if d],  # Filter out None values
            "metadata_samples": metadata_samples
        }
        
    except Exception as e:
        logger.error(f"Debug metadata failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Debug request failed.")

@app.get("/")
def read_root():
    return {"message": "Welcome to Heckermind."}

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("frontend/favicon.ico")

@app.delete("/session/{session_id}")
async def delete_session(session_id: str):
    """
    Deletes a user's session history from Redis.
    """
    try:
        await redis_manager.delete_history(session_id)
        logger.info(f"Successfully deleted session: {session_id}")
        return {"status": "success", "message": f"Session {session_id} deleted."}
    except Exception as e:
        logger.error(f"Error deleting session {session_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete session.")

# --- Lifecycle Events ---
@app.on_event("startup")
async def startup_event():
    """Initializes the database and ChromaDB service on application startup."""
    logger.info("--- Application starting up ---")
    try:
        initialize_db()
        logger.info("Database initialized.")
        await redis_manager.get_client()  # Initialize Redis connection pool
        logger.info("Redis connection pool initialized.")
        from auth.auth_service import auth_service 
        logger.info("AuthService system is initialized.")
        QdrantService()
        logger.info("QdrantService singleton is ready.")
        scheduler_thread = run_scheduler_in_background()
        if scheduler_thread:
            logger.info("Background ingestion scheduler started successfully.")
    except Exception as e:
        logger.critical(f"FATAL: Failed to initialize critical services on startup: {e}", exc_info=True)

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Application shutdown...")
    await redis_manager.close()  # Correctly await the async close method
    logger.info("Redis connection closed.")
    
    # Shut down the OpenTelemetry tracer provider to prevent event loop errors
    tracer_provider.shutdown()
    logger.info("OpenTelemetry tracer provider shut down.")

if __name__ == "__main__":
    import uvicorn
    # Note: The host is set to 0.0.0.0 to be accessible within a Docker container
    uvicorn.run(app, host="0.0.0.0", port=8000)
