"""
Embedding generation service using Ollama nomic-embed-text
"""
from langchain_ollama import OllamaEmbeddings
from typing import List
from backend.config import settings
from backend.utils.logger import get_logger

logger = get_logger(__name__)


# Initialize Ollama embeddings
embeddings_model = OllamaEmbeddings(
    model=settings.EMBEDDING_MODEL,
    base_url=settings.OLLAMA_BASE_URL
)


def generate_embedding(text: str) -> List[float]:
    """
    Generate embedding for a single text string
    
    Args:
        text: Input text to embed
        
    Returns:
        List of float values representing the embedding vector
    """
    try:
        embedding = embeddings_model.embed_query(text)
        logger.info(f"Generated embedding for text of length {len(text)}")
        return embedding
    except Exception as e:
        logger.error(f"Error generating embedding: {e}")
        raise


def batch_embed(texts: List[str]) -> List[List[float]]:
    """
    Generate embeddings for multiple texts in batch
    
    Args:
        texts: List of text strings to embed
        
    Returns:
        List of embedding vectors
    """
    try:
        embeddings = embeddings_model.embed_documents(texts)
        logger.info(f"Generated {len(embeddings)} embeddings in batch")
        return embeddings
    except Exception as e:
        logger.error(f"Error in batch embedding: {e}")
        raise
