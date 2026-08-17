"""
Similarity service for SD Image Sorter.

Handles business logic for image embedding, similarity search, and duplicate detection.
"""
from typing import Optional, List

from fastapi import HTTPException, UploadFile, BackgroundTasks
from starlette.concurrency import run_in_threadpool

import database as db
from ai_runtime_guard import AiRuntimeBusyError
from model_health import get_model_health
from similarity import (
    SimilarityEmbeddingMissingError,
    SimilarityImageNotFoundError,
    SimilarityInsufficientEmbeddingsError,
    SimilarityInvalidImageError,
    SimilarityDuplicateSearchTooLargeError,
    SimilaritySearchWindowTooLargeError,
    bytes_to_embedding,
    cosine_similarity,
    ensure_clip_model_ready,
    get_similarity_index,
)


class SimilarityService:
    """Service for image similarity search and duplicate detection."""

    def embed_images(
        self,
        background_tasks: BackgroundTasks,
        image_ids: Optional[List[int]] = None,
    ) -> dict:
        """
        Start embedding images in the background.

        Generates CLIP embeddings for images to enable similarity search
        and duplicate detection.
        """
        index = get_similarity_index(db)
        progress = index.get_progress()
        if progress["running"]:
            return {
                "status": "already_running",
                "progress": progress,
            }

        try:
            ensure_clip_model_ready()
        except AiRuntimeBusyError:
            # CLIP is not unavailable, it is queued behind another AI job.
            # main.py maps this to a 409 naming the blocker; 503 would read as
            # "the model is broken" and send the user to the Model Center.
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        background_tasks.add_task(index.embed_batch, image_ids)

        return {"status": "started", "message": "Embedding started in background"}

    def get_embed_progress(self) -> dict:
        """Get current embedding progress."""
        index = get_similarity_index(db)
        return index.get_progress()

    def cancel_embedding(self) -> bool:
        index = get_similarity_index(db)
        return index.request_cancel()

    def _resolve_scope_ids(self, collection_id: Optional[int], scope: Optional[str] = None) -> Optional[set]:
        """Resolve a collection scope into a set of allowed image ids.

        Returns ``None`` when no scope is requested (whole-library search, the
        default). Returns a (possibly empty) set of source image ids when a
        collection is requested. An empty set means "scope exists but has no
        members" — callers short-circuit to an empty result without touching the
        index.
        """
        if scope not in (None, "current_session", "library"):
            raise HTTPException(status_code=400, detail="scope must be current_session or library")
        session_ids = set(db.get_gallery_session_image_ids()) if scope == "current_session" else None
        collection_ids = (
            set(db.get_collection_image_ids(collection_id))
            if collection_id and collection_id > 0
            else None
        )
        if session_ids is None:
            return collection_ids
        if collection_ids is None:
            return session_ids
        return session_ids & collection_ids

    @staticmethod
    def _empty_search_result(image_id: Optional[int], limit: int, offset: int) -> dict:
        """Build a well-formed empty search envelope for an empty scope."""
        page_limit = max(1, int(limit))
        page_offset = max(0, int(offset))
        result = {
            "results": [],
            "count": 0,
            "total": 0,
            "has_more": False,
            "offset": page_offset,
            "limit": page_limit,
        }
        if image_id is not None:
            result["query_image_id"] = image_id
        return result

    def search_similar(
        self,
        image_id: int,
        limit: int = 100,
        threshold: float = 0.5,
        offset: int = 0,
        collection_id: Optional[int] = None,
        scope: Optional[str] = None,
    ) -> dict:
        """
        Find images similar to a given image ID.

        Uses pre-computed CLIP embeddings to find visually and semantically
        similar images. When ``collection_id`` is provided, results are scoped to
        that collection's members (e.g. Favorites); ``None`` searches the whole
        library.
        """
        allowed_ids = self._resolve_scope_ids(collection_id, scope)
        if allowed_ids is not None and not allowed_ids:
            return self._empty_search_result(image_id, limit, offset)

        index = get_similarity_index(db)
        try:
            result = index.search_by_id(
                image_id,
                limit=limit,
                threshold=threshold,
                offset=offset,
                allowed_ids=allowed_ids,
            )
        except SimilarityImageNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except SimilarityEmbeddingMissingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SimilaritySearchWindowTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        return {
            "query_image_id": image_id,
            "results": result["results"],
            "count": len(result["results"]),
            "total": result["total"],
            "has_more": result["has_more"],
            "offset": result["offset"],
            "limit": result["limit"],
        }

    def compare_pair(self, id_a: int, id_b: int) -> dict:
        """Compute the CLIP cosine similarity between two stored images.

        Read-only. Raises 404 if either image is missing and 409 if either has
        no embedding yet (run indexing first). Backs the two-image compare UI.
        """
        rows: dict = {}
        with db.get_db() as conn:
            cursor = conn.execute(
                "SELECT id, embedding, filename FROM images WHERE id IN (?, ?)",
                (id_a, id_b),
            )
            for row in cursor.fetchall():
                rows[int(row["id"])] = row
        for image_id in (id_a, id_b):
            if image_id not in rows:
                raise HTTPException(status_code=404, detail=f"Image {image_id} not found")
        for image_id in (id_a, id_b):
            if rows[image_id]["embedding"] is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"Image {image_id} has no embedding yet. Index it first (Similar tab).",
                )
        emb_a = bytes_to_embedding(rows[id_a]["embedding"])
        emb_b = bytes_to_embedding(rows[id_b]["embedding"])
        return {
            "id_a": id_a,
            "id_b": id_b,
            "similarity": round(float(cosine_similarity(emb_a, emb_b)), 4),
            "filename_a": rows[id_a]["filename"] or "",
            "filename_b": rows[id_b]["filename"] or "",
        }

    def find_near(
        self,
        image_id: int,
        limit: int = 24,
        collection_id: Optional[int] = None,
        scope: Optional[str] = None,
    ) -> dict:
        """Return the top-K nearest images to ``image_id`` (no threshold).

        Wraps the ANN-accelerated ``top_k_similar`` for a one-click
        "near-duplicates / most-similar" action. Scoped to a collection when
        ``collection_id`` is given.
        """
        allowed_ids = self._resolve_scope_ids(collection_id, scope)
        if allowed_ids is not None and not allowed_ids:
            return {"query_image_id": image_id, "results": [], "count": 0}
        with db.get_db() as conn:
            row = conn.execute(
                "SELECT embedding FROM images WHERE id = ?", (image_id,)
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Image {image_id} not found")
        if row["embedding"] is None:
            raise HTTPException(
                status_code=409,
                detail=f"Image {image_id} has no embedding yet. Index it first (Similar tab).",
            )
        index = get_similarity_index(db)
        query_emb = bytes_to_embedding(row["embedding"])
        results = index.top_k_similar(
            query_emb, max(1, int(limit)), exclude_id=image_id, allowed_ids=allowed_ids
        )
        return {
            "query_image_id": image_id,
            "results": results,
            "count": len(results),
        }

    async def search_by_upload(
        self,
        file: UploadFile,
        limit: int = 100,
        threshold: float = 0.5,
        offset: int = 0,
        collection_id: Optional[int] = None,
        scope: Optional[str] = None,
    ) -> dict:
        """
        Find images similar to an uploaded image.

        Generates an embedding for the uploaded image and searches
        the database for visually/semantically similar images. When
        ``collection_id`` is provided, results are scoped to that collection's
        members (e.g. Favorites); ``None`` searches the whole library.
        """
        MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
        image_data = bytearray()
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            image_data.extend(chunk)
            if len(image_data) > MAX_UPLOAD_SIZE:
                raise HTTPException(status_code=413, detail="File too large (max 50MB)")
        if not image_data:
            raise HTTPException(status_code=400, detail="Empty file uploaded")

        allowed_ids = self._resolve_scope_ids(collection_id, scope)
        if allowed_ids is not None and not allowed_ids:
            await file.close()
            return self._empty_search_result(None, limit, offset)

        index = get_similarity_index(db)
        try:
            result = await run_in_threadpool(
                index.search_by_upload,
                bytes(image_data),
                limit,
                threshold,
                offset,
                allowed_ids,
            )
        except SimilarityInvalidImageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SimilaritySearchWindowTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        finally:
            await file.close()
        return {
            "results": result["results"],
            "count": len(result["results"]),
            "total": result["total"],
            "has_more": result["has_more"],
            "offset": result["offset"],
            "limit": result["limit"],
        }

    async def search_by_text(
        self,
        query: str,
        limit: int = 100,
        threshold: float = 0.0,
        offset: int = 0,
        collection_id: Optional[int] = None,
        scope: Optional[str] = None,
    ) -> dict:
        """Semantic text search over the stored CLIP embeddings (same scope
        + pagination semantics as the upload search; RuntimeError from a
        not-yet-downloaded text tower maps to 503 so the UI can explain)."""
        allowed_ids = self._resolve_scope_ids(collection_id, scope)
        if allowed_ids is not None and not allowed_ids:
            return self._empty_search_result(None, limit, offset)

        index = get_similarity_index(db)
        try:
            result = await run_in_threadpool(
                index.search_by_text,
                query,
                limit,
                threshold,
                offset,
                allowed_ids,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "query": query,
            "results": result["results"],
            "count": len(result["results"]),
            "total": result["total"],
            "has_more": result["has_more"],
            "offset": result["offset"],
            "limit": result["limit"],
        }

    def find_duplicates(
        self,
        threshold: float = 0.95,
        limit: int = 500,
        offset: int = 0,
    ) -> dict:
        """
        Find near-duplicate image pairs above similarity threshold.

        Performs an all-to-all comparison of embedded images to find
        visually similar pairs.
        """
        index = get_similarity_index(db)
        try:
            result = index.find_duplicates(threshold=threshold, limit=limit, offset=offset)
        except SimilarityInsufficientEmbeddingsError as exc:
            return {
                "duplicates": [],
                "count": 0,
                "total": 0,
                "has_more": False,
                "offset": offset,
                "limit": limit,
                "threshold": threshold,
                "reason": "insufficient_embeddings",
                "embedded_count": exc.embedded_count,
                "minimum_required": exc.minimum_required,
            }
        except SimilarityDuplicateSearchTooLargeError as exc:
            return {
                "duplicates": [],
                "count": 0,
                "total": 0,
                "has_more": False,
                "offset": offset,
                "limit": limit,
                "threshold": threshold,
                "reason": "too_many_embeddings",
                "embedded_count": exc.embedded_count,
                "max_embeddings": exc.max_embeddings,
            }
        return {
            "duplicates": result["duplicates"],
            "count": len(result["duplicates"]),
            "total": result["total"],
            "has_more": result["has_more"],
            "offset": result["offset"],
            "limit": result["limit"],
            "threshold": result["threshold"],
        }

    def get_stats(self) -> dict:
        """Get statistics about embeddings."""
        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM images WHERE COALESCE(is_readable, 1) = 1")
            total = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM images WHERE embedding IS NOT NULL AND COALESCE(is_readable, 1) = 1")
            embedded = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM images WHERE COALESCE(is_readable, 1) = 0")
            unreadable = cursor.fetchone()[0]
        return {
            "total_images": total,
            "embedded_images": embedded,
            "embedded_count": embedded,
            "pending": total - embedded,
            "pending_count": total - embedded,
            "unreadable_count": unreadable,
            "coverage": round(embedded / total * 100, 1) if total > 0 else 0,
        }

    def get_model_status(self) -> dict:
        """Expose the local CLIP model readiness for the frontend."""
        clip = get_model_health()["clip"]
        runtime_loaded = clip.get("runtime_loaded", False)
        effective_available = clip["available"] or runtime_loaded
        # message_key mirrors model_service's CLIP card branches so the
        # Similar view can localize instead of echoing backend English.
        if runtime_loaded and not clip["available"]:
            message_key = "models.clip.loaded"
        elif clip["available"]:
            message_key = "models.clip.ready"
        elif clip["model_path"]:
            message_key = "models.clip.missingRuntime"
        else:
            message_key = "models.clip.missingModel"
        return {
            "status": "ok",
            **clip,
            "available": effective_available,
            "message": clip["message"] if not runtime_loaded or clip["available"] else "CLIP model is loaded and ready.",
            "message_key": message_key,
        }
