import logging
import os
from typing import Optional
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from langchain_qdrant import QdrantVectorStore
from langchain_openai import OpenAIEmbeddings
from qdrant_client.models import Distance, VectorParams

# Load environment variables
load_dotenv()

# Configure logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
class QdrantService:
    """
    A singleton service for interacting with Qdrant vector database.
    """
    _instance = None
    _client: QdrantClient = None
    _embeddings = None
    _retriever = None
    _vector_store = None
    _initialized = False
    _force_recreate_done = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(QdrantService, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        """
        Initializes the QdrantService, setting up a singleton client and embedding function.
        """
        if not self._initialized:
            self.initialize()

    def initialize(self):
        """Initialize Qdrant client and embeddings."""
        if self._initialized:
            logger.info("QdrantService already initialized.")
            return

        try:
            # Initialize client
            if QdrantService._client is None:
                self._initialize_client()

            # Initialize embeddings using OpenAI text-embedding-3-large
            if QdrantService._embeddings is None:
                embedding_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")
                QdrantService._embeddings = OpenAIEmbeddings(model=embedding_model)
                logger.info(f"OpenAI embedding model '{embedding_model}' successfully initialized")
            # Initialize vector store and retriever
            self.collection_name = os.getenv("COLLECTION_NAME", "MoneyQuest")
            self._vector_store = self.get_vector_store(self.collection_name)
            self._retriever = self._vector_store.as_retriever(
                search_kwargs={"k": int(os.getenv("RETRIEVER_K", "10"))}
            )
            self._initialized = True
            logger.info("✅ QdrantService initialized successfully.")

        except Exception as e:
            logger.error(f"❌ Failed to initialize QdrantService: {e}", exc_info=True)
            raise

    def _initialize_client(self):
        """
        Initializes the Qdrant client.
        """
        host = os.getenv("QDRANT_HOST")
        port = os.getenv("QDRANT_PORT")
        path = os.getenv("QDRANT_PATH", "./qdrant_db")

        try:
            if host and port and host != "localhost":
                # Connect to remote Qdrant server
                url = f"http://{host}:{port}"
                logger.info(f"Connecting to Qdrant server at {host}:{port}...")
                QdrantService._client = QdrantClient(url=url, timeout=30)
            else:
                # Use local persistent Qdrant
                logger.info(f"Using persistent Qdrant at path: {path}")
                QdrantService._client = QdrantClient(path=path)

            logger.info("✅ Qdrant client initialized successfully.")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Qdrant client: {e}", exc_info=True)
            raise

    def get_client(self) -> QdrantClient:
        """Returns the singleton Qdrant client instance."""
        if QdrantService._client is None:
            self._initialize_client()
        return QdrantService._client

    def get_vector_store(self, collection_name: str) -> QdrantVectorStore:
        """
        Creates and returns a LangChain Qdrant vector store for a specific collection.
        """
        if not QdrantService._client or not QdrantService._embeddings:
            logger.error("QdrantService is not properly initialized.")
            raise ConnectionError("QdrantService is not properly initialized.")

        try:
            # Check if collection exists
            collections = QdrantService._client.get_collections().collections
            collection_names = [collection.name for collection in collections]
            
            if collection_name not in collection_names:
                # OpenAI text-embedding-3-large uses 3072-dimensional vectors
                vector_size = 3072
                logger.info(f"Creating collection '{collection_name}' with vector size {vector_size}")
                QdrantService._client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(
                        size=vector_size,
                        distance=Distance.COSINE
                    )
                )
                logger.info(f"✅ Collection '{collection_name}' created successfully")
            else:
                # Check collection size
                collection_info = QdrantService._client.get_collection(collection_name)
                count = collection_info.points_count
                if count == 0:
                    logger.warning(f"Qdrant collection '{collection_name}' is empty. Please run ingest.py to add documents.")
                else:
                    logger.info(f"Qdrant collection '{collection_name}' contains {count} documents.")

            force_recreate = (os.getenv("QDRANT_FORCE_RECREATE", "false").lower() == "true" and not QdrantService._force_recreate_done)
        
            if force_recreate and collection_name in collection_names:
                logger.warning("⚠️ QDRANT_FORCE_RECREATE=true - Collection will be recreated (one-time)!")
                QdrantService._client.delete_collection(collection_name=collection_name)
                collection_names.remove(collection_name)  # Update local list
                QdrantService._force_recreate_done = True  # Mark as done
                logger.info(f"✅ Collection '{collection_name}' deleted for recreation")
            
            if collection_name not in collection_names:
                # OpenAI text-embedding-3-large uses 3072-dimensional vectors
                vector_size = 3072
                logger.info(f"Creating collection '{collection_name}' with vector size {vector_size}")
                QdrantService._client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(
                        size=vector_size,
                        distance=Distance.COSINE
                    )
                )
                logger.info(f"✅ Collection '{collection_name}' created successfully")
                QdrantService._force_recreate_done = True  # Also mark done after creation
            else:
                # Check collection size
                collection_info = QdrantService._client.get_collection(collection_name)
                count = collection_info.points_count
                if count == 0:
                    logger.warning(f"Qdrant collection '{collection_name}' is empty. Please run ingest.py to add documents.")
                else:
                    logger.info(f"Qdrant collection '{collection_name}' contains {count} documents.")

        except Exception as e:
            logger.error(f"❌ Failed to create/check collection '{collection_name}': {e}")
            raise

        return QdrantVectorStore(
            client=QdrantService._client,
            collection_name=collection_name,
            embedding=QdrantService._embeddings
        )

    def get_retriever(self, collection_name: str = None, search_kwargs: Optional[dict] = None):
        """Gets a retriever for a specific collection."""
        if not self._retriever:
            collection_name = collection_name or os.getenv("COLLECTION_NAME", "MoneyQuest")
            vector_store = self.get_vector_store(collection_name)
            if not search_kwargs:
                search_kwargs = {"k": int(os.getenv("RETRIEVER_K", "5"))}
            self._retriever = vector_store.as_retriever(search_kwargs=search_kwargs)
        return self._retriever

    @property
    def vector_store(self):
        """Property to access the vector store."""
        if not self._vector_store:
            collection_name = os.getenv("COLLECTION_NAME", "MoneyQuest")
            self._vector_store = self.get_vector_store(collection_name)
        return self._vector_store

    async def retrieve_documents(self, query: str):
        """Retrieves relevant documents from Qdrant based on a query."""
        if not self._retriever:
            logger.error("Retriever is not initialized.")
            return []
        
        return await self._retriever.ainvoke(query)

# Create a singleton instance of the service
qdrant_service = QdrantService()