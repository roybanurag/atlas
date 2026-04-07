"""Local embedding generation for semantic memory search.

Uses sentence-transformers for privacy-preserving local embeddings.
No external API calls — all processing happens on-device.

This module provides embedding generation only. Storage and search
are handled by SQLiteMemoryStore.
"""

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingGenerator:
    """Generate text embeddings using sentence-transformers.
    
    Falls back gracefully if sentence-transformers is not installed.
    """
    
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """Initialize embedding generator.
        
        Args:
            model_name: Sentence transformer model name
        """
        self.model_name = model_name
        self._model = None
        self._available: Optional[bool] = None
    
    @property
    def available(self) -> bool:
        """Check if sentence-transformers is available."""
        if self._available is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._available = True
            except ImportError:
                self._available = False
                logger.info(
                    "sentence-transformers not available; "
                    "memory search will use keyword matching only"
                )
        return self._available
    
    @property
    def model(self):
        """Lazy-load the embedding model."""
        if self._model is None and self.available:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model
    
    def encode(self, text: str) -> Optional[np.ndarray]:
        """Generate an embedding vector for the given text.
        
        Args:
            text: Text to embed
            
        Returns:
            Numpy array of floats, or None if unavailable
        """
        if not self.available or self.model is None:
            return None
        
        try:
            return self.model.encode(text, convert_to_numpy=True)
        except Exception as e:
            logger.warning(f"Embedding generation failed: {e}")
            return None
    
    def encode_batch(self, texts: list[str]) -> list[Optional[np.ndarray]]:
        """Generate embeddings for multiple texts efficiently.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of numpy arrays (or None for failures)
        """
        if not self.available or self.model is None:
            return [None] * len(texts)
        
        try:
            embeddings = self.model.encode(texts, convert_to_numpy=True)
            return list(embeddings)
        except Exception as e:
            logger.warning(f"Batch embedding generation failed: {e}")
            return [None] * len(texts)


# Backward compatibility alias
EmbeddingIndex = EmbeddingGenerator
