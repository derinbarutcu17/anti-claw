import torch
from sentence_transformers import SentenceTransformer
from typing import List, Union
import numpy as np

class EmbeddingsManager:
    """Manages sentence embeddings using sentence-transformers, with MPS acceleration if available."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self.device = self._get_device()
        self.model = SentenceTransformer(model_name, device=self.device)
        self.dimension = self.model.get_sentence_embedding_dimension()

    def _get_device(self) -> str:
        """Determines the best available device: mps, cuda, or cpu."""
        if torch.backends.mps.is_available():
            return "mps"
        elif torch.cuda.is_available():
            return "cuda"
        else:
            return "cpu"

    def get_embeddings(self, texts: Union[str, List[str]]) -> np.ndarray:
        """Generates embeddings for one or more strings."""
        if isinstance(texts, str):
            texts = [texts]
        
        # Encode returns a numpy array by default
        embeddings = self.model.encode(texts, convert_to_numpy=True)
        return embeddings

    def get_dimension(self) -> int:
        """Returns the embedding dimension (384 for default all-MiniLM-L6-v2)."""
        return self.dimension

# Singleton instance to avoid reloading the model
embeddings_manager = EmbeddingsManager()
