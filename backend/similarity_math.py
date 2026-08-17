"""Pure embedding byte/cosine helpers (split from similarity.py, 2026-07).

Moved byte-verbatim from similarity.py lines 260-276. The ``similarity`` facade
re-imports all three, so ``similarity.embedding_to_bytes`` /
``bytes_to_embedding`` / ``cosine_similarity`` keep resolving there for
services.similarity_service, services.duplicate_group_service, embed_batch's
module-global read, and every test that from-imports them.
"""

import numpy as np


def embedding_to_bytes(embedding: np.ndarray) -> bytes:
    """Convert a numpy embedding to bytes for SQLite storage."""
    return embedding.astype(np.float32).tobytes()


def bytes_to_embedding(data: bytes) -> np.ndarray:
    """Convert bytes from SQLite back to numpy embedding."""
    return np.frombuffer(data, dtype=np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))
