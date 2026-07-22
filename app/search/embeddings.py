"""
EmbeddingEngine — Semantic search via sentence-transformers.

Uses the `all-MiniLM-L6-v2` model (22 MB, 384 dimensions) to encode
text into normalized vectors. Cosine similarity between normalized vectors
is equivalent to a dot product, enabling fast bulk comparison with numpy.

Graceful fallback: if sentence-transformers is not installed, all methods
return None/False and the ranker silently falls back to TF-IDF + PageRank.

Model is lazy-loaded on first call and cached as a class variable.
"""

import json
import logging
from typing import Optional, List

logger = logging.getLogger(__name__)

MODEL_NAME = "all-MiniLM-L6-v2"
MAX_INPUT_CHARS = 2000  # truncate to keep inference fast on CPU


class EmbeddingEngine:
    """
    Lightweight wrapper around sentence-transformers for semantic embedding.
    All methods are synchronous — call from a thread pool if needed in async code.
    """

    _model = None          # cached SentenceTransformer instance
    _available: Optional[bool] = None   # tri-state: None = not yet checked

    @classmethod
    def is_available(cls) -> bool:
        """Return True if sentence-transformers is installed and the model loaded."""
        if cls._available is None:
            cls._load_model()
        return cls._available is True

    @classmethod
    def _load_model(cls) -> None:
        """Attempt to load the sentence-transformers model. Sets _available flag."""
        try:
            import torch
            torch.set_num_threads(1)
            torch.set_num_interop_threads(1)

            from sentence_transformers import SentenceTransformer
            try:
                cls._model = SentenceTransformer(MODEL_NAME, device="cpu")
            except Exception:
                # Fallback to locally cached model if HuggingFace network check fails
                cls._model = SentenceTransformer(MODEL_NAME, device="cpu", local_files_only=True)

            cls._available = True
            logger.info(f"EmbeddingEngine: Loaded model '{MODEL_NAME}' (PyTorch CPU mode) successfully.")
        except ImportError:
            cls._available = False
            logger.warning(
                "EmbeddingEngine: sentence-transformers is not installed. "
                "Semantic search is DISABLED. Install with: pip install sentence-transformers"
            )
        except Exception as e:
            cls._available = False
            logger.error(f"EmbeddingEngine: Failed to load model — {e}")

    @classmethod
    def encode(cls, text: str) -> Optional[List[float]]:
        """
        Encode a text string into a normalized 384-dim embedding vector.

        Args:
            text: input text (will be truncated to MAX_INPUT_CHARS)

        Returns:
            List of floats (normalized) or None if unavailable.
        """
        if not cls.is_available():
            return None

        try:
            text = text[:MAX_INPUT_CHARS].strip()
            if not text:
                return None

            vector = cls._model.encode(text, normalize_embeddings=True)
            return vector.tolist()
        except Exception as e:
            logger.error(f"EmbeddingEngine.encode error: {e}")
            return None

    @staticmethod
    def cosine_similarity(a: List[float], b: List[float]) -> float:
        """
        Compute cosine similarity between two pre-normalized embedding vectors.
        Since both vectors are L2-normalized, cos_sim = dot product.
        Uses a simple Python loop; for larger scale use numpy batch operations.
        """
        if len(a) != len(b):
            return 0.0
        return sum(x * y for x, y in zip(a, b))

    @staticmethod
    def batch_cosine_similarity(query_vec: List[float], embeddings: List[List[float]]) -> List[float]:
        """
        Compute cosine similarity between one query vector and a list of document vectors.
        Uses numpy for fast vectorized computation when available.

        Args:
            query_vec: normalized query embedding (384 dims)
            embeddings: list of normalized document embeddings

        Returns:
            List of similarity scores (one per document)
        """
        if not embeddings:
            return []

        try:
            import numpy as np
            q = np.array(query_vec, dtype=np.float32)          # (384,)
            D = np.array(embeddings, dtype=np.float32)          # (N, 384)
            scores = D @ q                                      # (N,) — dot product per row
            return scores.tolist()
        except ImportError:
            # numpy not available — fall back to pure Python
            return [
                sum(q_i * d_i for q_i, d_i in zip(query_vec, doc_vec))
                for doc_vec in embeddings
            ]

    @staticmethod
    def embedding_to_json(embedding: List[float]) -> str:
        """Serialize embedding to compact JSON string for database storage."""
        return json.dumps(embedding, separators=(',', ':'))

    @staticmethod
    def json_to_embedding(json_str: str) -> Optional[List[float]]:
        """Deserialize embedding from JSON string."""
        try:
            return json.loads(json_str)
        except Exception:
            return None
