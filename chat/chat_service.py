import os
import sys
import logging
from typing import Optional, List
import gc
import json

from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough, RunnableWithMessageHistory

from config import settings
from core.redis_manager import RedisSessionManager
from core.vector_db_services import vector_db_service

logger = logging.getLogger(__name__)

class ChatService:
    def __init__(self):
        self.redis_manager = RedisSessionManager()
        self.llm = ChatOpenAI(model=settings.llm_model, temperature=0.6, streaming=True)
        self._setup_rag_chain()
    
    def _load_system_prompt(self) -> str:
        """Load system prompt from file"""
        try:
            with open(settings.system_prompt_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except FileNotFoundError:
            logger.warning(f"{settings.system_prompt_path} not found; using default prompt.")
            return (
                "You are Bella, Heckerbella's AI assistant. Use provided context to answer queries accurately and conversationally. "
                "If context doesn't contain the answer, say so. Always be friendly and include a relevant call-to-action."
            )
    
    def _setup_rag_chain(self):
        """Setup the RAG chain"""
        system_prompt = self._load_system_prompt()
        
        rag_prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder(variable_name="history"),
            ("human", "Context:\n{context}\n\nQuestion: {question}")
        ])
        
        rag_chain = (
            RunnablePassthrough.assign(
                documents=lambda x: self.get_retriever_for_request(
                    x.get("access_status", ["public"]),
                    department=x.get("department"),
                    company_id=x.get("company_id")
                ).invoke(x["question"])
            )
            |RunnablePassthrough.assign(context=lambda x: self.format_docs(x["documents"]))
            |RunnablePassthrough.assign(output=rag_prompt | self.llm | StrOutputParser())
        )
        
        self.chain_with_history = RunnableWithMessageHistory(
            rag_chain,
            self.redis_manager.get_session_history,
            input_messages_key="question",
            history_messages_key="history",
            output_messages_key="output",
        )
    
    def format_docs(self, docs):
        """Format documents for context"""
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
        return result
    
    def get_retriever_for_request(self, access_status: list[str], k: int = 10, 
                                  department: Optional[str] = None, 
                                  company_id: Optional[str] = None):
        """Get retriever with department filtering"""
        collections_to_query = set(access_status)
        collections_to_query.add("public")
        
        # For multi-tenant support (future)
        if company_id:
            primary_collection = f"{company_id}_{access_status[0]}" if access_status else f"{company_id}_public"
            logger.info(f"🏢 Using company-specific collection: {primary_collection}")
        else:
            logger.warning("⚠️ No company_id provided, kindly provide it to continue - using global collection (SECURITY RISK)")
            pass
            # primary_collection = access_status[0] if access_status else "public"
        
        logger.info(f"🔍 Getting retriever for collection: '{primary_collection}', department: '{department}'")
        try:
            vector_store = vector_db_service.get_vector_store(primary_collection)
        except Exception as e:
            logger.error(f"Failed to get vector store for collection '{primary_collection}': {e}", exc_info=True)
            fallback_collection = f"{company_id}_public" if company_id else "public"
            logger.info(f"🔄 Falling back to collection: {fallback_collection}")
            vector_store = vector_db_service.get_vector_store(fallback_collection)

        if not department or department.lower() in ["general", "all", ""]:
            logger.info(f"🔍 No department filter applied - returning all documents")
            return vector_store.as_retriever(search_kwargs={'k': k})
        
        return self.DepartmentFilteredRetriever(vector_store, department, k)
    
    async def stream_chat_response(self, session_id: str, message: str, access_status: list, department: str = None, company_id: str = None):
        """Stream chat response with sources and content"""
        try:
            normalized_department = None
            if department:
                cleaned_dept = department.strip()
                if cleaned_dept:
                    normalized_department = cleaned_dept.lower()
            
            logger.info(f"Chat request received. Session: {session_id}, Access: {access_status}, Department: {normalized_department}")
            
            config = {"configurable": {"session_id": session_id}}
            
            response_generator = self.chain_with_history.astream(
                {
                    "question": message,
                    "access_status": access_status,
                    "department": normalized_department,
                    "company_id": company_id
                },
                config=config
            )
            
            sources_sent = False
            async for chunk in response_generator:
                # Yield sources once if they exist in the chunk
                if not sources_sent and "documents" in chunk:
                    documents = chunk.get("documents", [])
                    if documents:
                        seen_sources = set()
                        for doc in documents:
                            source_file = os.path.basename(doc.metadata.get("source", "Unknown"))
                            department_meta = doc.metadata.get('department', 'N/A')
                            source_key = (source_file, department_meta)
                            if source_key not in seen_sources:
                                seen_sources.add(source_key)
                        
                        source_list = [{"name": name, "department": dept} for name, dept in seen_sources]
                        yield json.dumps({"type": "sources", "data": source_list}) + "\n"
                        sources_sent = True

                # Yield content if it exists in the chunk
                if "output" in chunk and chunk["output"]:
                    yield json.dumps({"type": "content", "data": chunk["output"]}) + "\n"
                    
        except Exception as e:
            logger.error(f"Chat streaming error: {e}", exc_info=True)
            yield json.dumps({"type": "error", "data": "An internal error occurred."}) + "\n"
    
    class DepartmentFilteredRetriever:
        def __init__(self, vector_store, target_department, k):
            self.vector_store = vector_store
            self.target_department = target_department.lower()
            self.k = k
        
        def invoke(self, query):
            import gc
            base_retriever = self.vector_store.as_retriever(search_kwargs={'k': self.k * 5})
            all_docs = base_retriever.invoke(query)
            
            logger.info(f"🔍 Retrieved {len(all_docs)} documents before department filtering")
            
            filtered_docs = []
            for i, doc in enumerate(all_docs):
                doc_department = doc.metadata.get('department', '').lower()
                logger.info(f"🔍 Checking doc department: '{doc_department}' vs target: '{self.target_department}'")
                
                if doc_department == self.target_department:
                    filtered_docs.append(doc)
                else:
                    all_docs[i] = None
            
            all_docs.clear()
            del all_docs
            gc.collect()
            
            result = filtered_docs[:self.k]
            if len(filtered_docs) > self.k:
                filtered_docs = None
                gc.collect()
            
            logger.info(f"🔍 Returning {len(result)} documents to chain")
            return result

# Global service instance
chat_service = ChatService()