import os
import logging
import json
from typing import Optional
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough, RunnableWithMessageHistory
from core.vector_db_services import vector_db_service
from core.redis_manager import RedisSessionManager
from config import settings

logger = logging.getLogger(__name__)

# Initialize Redis Manager
redis_manager = RedisSessionManager()

# Initialize LLM
llm = ChatOpenAI(model=settings.llm_model, temperature=0.6, streaming=True)

# Load System Prompt
try:
    with open(settings.system_prompt_path, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT = f.read().strip()
        logger.info(f"Loaded system prompt from {settings.system_prompt_path}")
except FileNotFoundError:
    logger.warning(f"{settings.system_prompt_path} not found; using default prompt.")
    SYSTEM_PROMPT = (
        "You are Bella, Heckerbella's AI assistant. Use provided context to answer queries accurately and conversationally. "
        "If context doesn't contain the answer, say so. Always be friendly and include a relevant call-to-action."
    )
except Exception as e:
    logger.error(f"Error loading system prompt: {e}", exc_info=True)
    SYSTEM_PROMPT = "Error: Could not load system prompt."

# Main RAG prompt
rag_prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    MessagesPlaceholder(variable_name="history"),
    ("human", "Context:\n{context}\n\nQuestion: {question}")
])

def format_docs(docs):
    if not docs:
        logger.info("🔍 format_docs: No documents provided")
        return "No context available."

    logger.info(f"🔍 format_docs: Formatting {len(docs)} documents")

    result = "\n\n".join(
        f"Source: {os.path.basename(doc.metadata.get('source', 'Unknown'))}\n"
        f"Department: {doc.metadata.get('department', 'N/A')}\n"
        f"Content: {doc.page_content}"
        for doc in docs
    )
    logger.info(f"🔍 format_docs: Generated context length: {len(result)} chars") 
    # logger.info(f"🔍 format_docs: Context preview: {result[:200]}...")
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
            import gc
            base_retriever = self.vector_store.as_retriever(search_kwargs={'k': self.k * 5})
            all_docs = base_retriever.invoke(query)
            
            logger.info(f"🔍 Retrieved {len(all_docs)} documents before department filtering")
            
            # Manual filtering by department
            filtered_docs = []
            
            for doc in all_docs:
                doc_department = doc.metadata.get('department', '').lower()
                # logger.info(f"🔍 Checking doc department: '{doc_department}' vs target: '{self.target_department}'")
                
                if doc_department == self.target_department:
                    filtered_docs.append(doc)
            
            # Return the requested number of filtered documents
            result = filtered_docs[:self.k]
            logger.info(f"🔍 Returning {len(result)} documents to chain")
            return result
    
    logger.info(f"🔍 Using manual department filtering for: '{department}'")
    return DepartmentFilteredRetriever(vector_store, department, k)

# RAG chain definition
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
