"""Exception hierarchy for the similarity workflow (split from similarity.py, 2026-07).

The 7 classes moved byte-verbatim from similarity.py lines 46-103. The ``similarity`` facade
re-imports every class, so ``similarity.SimilaritySearchWindowTooLargeError``
(and siblings) resolve to THESE SAME objects -- services.similarity_service
imports all of them from ``similarity`` and maps them to HTTP codes, and
tests/test_similarity_pins.py pins the hierarchy and attributes.
"""


class SimilarityError(RuntimeError):
    """Base class for similarity workflow errors."""


class SimilarityImageNotFoundError(SimilarityError):
    """Raised when the requested image id does not exist."""

    def __init__(self, image_id: int):
        super().__init__(f"Image {image_id} was not found.")
        self.image_id = image_id


class SimilarityEmbeddingMissingError(SimilarityError):
    """Raised when the requested image does not have an embedding yet."""

    def __init__(self, image_id: int):
        super().__init__(f"Image {image_id} has no embedding yet. Generate embeddings first.")
        self.image_id = image_id


class SimilarityInvalidImageError(SimilarityError):
    """Raised when an uploaded image cannot be decoded."""

    def __init__(self):
        super().__init__("Invalid image file. Upload a readable PNG, JPG, or WebP image.")


class SimilarityInsufficientEmbeddingsError(SimilarityError):
    """Raised when duplicate detection does not have enough embedded images."""

    def __init__(self, embedded_count: int, minimum_required: int = 2):
        super().__init__(
            f"Need at least {minimum_required} embedded images before duplicate search is meaningful."
        )
        self.embedded_count = embedded_count
        self.minimum_required = minimum_required


class SimilarityDuplicateSearchTooLargeError(SimilarityError):
    """Raised when synchronous duplicate search would compare too many embeddings."""

    def __init__(self, embedded_count: int, max_embeddings: int):
        super().__init__(
            f"Duplicate search is limited to {max_embeddings} embedded images for synchronous checks."
        )
        self.embedded_count = embedded_count
        self.max_embeddings = max_embeddings


class SimilaritySearchWindowTooLargeError(SimilarityError):
    """Raised when offset + limit would require an unsafe ranking window."""

    def __init__(self, requested_window: int, max_window: int):
        super().__init__(
            f"Similarity pagination window is limited to {max_window} ranked results per request."
        )
        self.requested_window = requested_window
        self.max_window = max_window
