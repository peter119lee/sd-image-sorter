"""
Image similarity search using FastEmbed CLIP embeddings.

Generates 512-dim CLIP embeddings per image and stores them in SQLite.
Supports finding similar images by ID, by upload, and finding duplicates.
"""
import gc
import io
import heapq
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any

import numpy as np
from PIL import Image, UnidentifiedImageError

from config import (
    CLIP_MODEL_NAME,
    CLIP_TEXT_MODEL_NAME,
    get_clip_model_dir,
    get_state_dir,
    SIMILARITY_DEFAULT_LIMIT,
    SIMILARITY_DEFAULT_THRESHOLD,
    DUPLICATE_THRESHOLD,
    EMBEDDING_BATCH_SIZE,
    DUPLICATE_CHUNK_SIZE,
    DUPLICATE_SYNC_MAX_EMBEDDINGS,
)
from image_fingerprint import compute_image_content_fingerprint
from metadata_parser import verify_image_readable
from model_health import get_clip_local_model_path, get_clip_text_local_model_path
from model_download_sources import (
    apply_hf_endpoint_monkeypatch,
    endpoint_label,
    get_hf_endpoint_order,
    log_model_artifact_status,
)
from model_health_paths import CLIP_TEXT_REQUIRED_FILES, CLIP_VISION_REQUIRED_FILES
from utils.source_paths import resolve_existing_indexed_image_path
from ai_runtime_guard import exclusive_ai_runtime
import similarity_ann


logger = logging.getLogger(__name__)


def _compute_embedding_content_fingerprint(image_path: str) -> Optional[str]:
    """Compute the source fingerprint used to publish an embedding."""
    try:
        fingerprint = compute_image_content_fingerprint(image_path)
    except Exception as exc:  # noqa: BLE001 - the job must report the source failure
        logger.warning("Could not fingerprint image for embedding path=%s: %s", image_path, exc)
        return None
    return fingerprint.strip() if fingerprint else None


# Decomposition (2026-07): the exception hierarchy moved byte-verbatim to
# similarity_errors. Re-imported so
# every ``similarity.<Error>`` reference -- services.similarity_service's
# HTTP-code mapping and the pin suite's identity checks -- keeps resolving to
# the SAME class objects (re-export, not copies).
from similarity_errors import (
    SimilarityError,
    SimilarityImageNotFoundError,
    SimilarityEmbeddingMissingError,
    SimilarityInvalidImageError,
    SimilarityInsufficientEmbeddingsError,
    SimilarityDuplicateSearchTooLargeError,
    SimilaritySearchWindowTooLargeError,
)


# Lazy-loaded FastEmbed model
_embed_model = None
_embed_lock = threading.Lock()

SIMILARITY_SEARCH_CHUNK_SIZE = max(
    64,
    min(8192, int(os.environ.get("SD_SIMILARITY_SEARCH_CHUNK_SIZE", "2048") or 2048)),
)
SIMILARITY_SEARCH_MAX_WINDOW = max(
    1000,
    min(200000, int(os.environ.get("SD_SIMILARITY_SEARCH_MAX_WINDOW", "50000") or 50000)),
)

# In-memory vectorized embedding cache (PERF-1).
#
# The streaming search re-reads and re-decodes every embedding BLOB from SQLite
# on each query (e.g. ~400MB of I/O for 200k images), which dominates latency.
# Caching a single L2-normalized float32 matrix in RAM turns each search into one
# vectorized matmul. This is a pure accelerator: the streaming scan below stays as
# the always-available fallback, so results are never capped or degraded — if the
# cache cannot be built (memory pressure, odd DB shape) we silently fall back.
#
# Opt-out via SD_SIMILARITY_DISABLE_VECTOR_CACHE=1 (ops escape hatch, not a feature
# cap — the feature works at full fidelity either way).
SIMILARITY_VECTOR_CACHE_ENABLED = (
    os.environ.get("SD_SIMILARITY_DISABLE_VECTOR_CACHE", "").strip().lower()
    not in ("1", "true", "yes", "on")
)

# hnswlib ANN top-k bypass (v3.3.2 Phase 1, slice 4b). Optional accelerator for
# the workbench / dedup "top-k nearest" path only — never the exact paginated
# search. Opt-out via SD_SIMILARITY_DISABLE_ANN=1; also a silent no-op (exact
# fallback) when hnswlib is not installed.
SIMILARITY_ANN_ENABLED = (
    os.environ.get("SD_SIMILARITY_DISABLE_ANN", "").strip().lower()
    not in ("1", "true", "yes", "on")
)


def _get_embed_model():
    """Get or create the FastEmbed CLIP model (singleton)."""
    global _embed_model
    if _embed_model is None:
        with _embed_lock:
            if _embed_model is None:
                try:
                    from fastembed import ImageEmbedding  # type: ignore
                    local_model_path = get_clip_local_model_path()
                    model_kwargs = {
                        "model_name": CLIP_MODEL_NAME,
                        "cache_dir": get_clip_model_dir(),
                    }
                    if local_model_path:
                        model_kwargs.update(
                            {
                                "local_files_only": True,
                                "specific_model_path": local_model_path,
                            }
                        )
                    if not local_model_path:
                        endpoint = get_hf_endpoint_order(model_name="CLIP Similarity")[0]
                        apply_hf_endpoint_monkeypatch(endpoint, purpose="CLIP Similarity / FastEmbed")
                        logger.info("FastEmbed CLIP will use %s unless a local cache is already valid.", endpoint_label(endpoint))
                    with exclusive_ai_runtime("clip-similarity-load"):
                        _embed_model = ImageEmbedding(
                            **model_kwargs,
                        )
                except ImportError:
                    raise RuntimeError(
                        "fastembed not installed. Run: pip install fastembed"
                    )
                except Exception as exc:
                    if get_clip_local_model_path():
                        raise RuntimeError(
                            "Local CLIP model exists but FastEmbed could not open it. "
                            f"Checked: {get_clip_local_model_path()}. Error: {exc}"
                        ) from exc
                    raise RuntimeError(
                        "CLIP embedding model is not ready yet. "
                        "Download the local model first or allow the first-run model download."
                    ) from exc
    return _embed_model


def _loaded_fastembed_model_dir(model: object) -> Optional[str]:
    """Return the backing directory exposed by a loaded FastEmbed wrapper."""
    try:
        wrapped = getattr(model, "model", None)
        model_dir = getattr(model, "model_dir", None) or getattr(wrapped, "_model_dir", None)
        if model_dir:
            return str(model_dir)
    except Exception:
        logger.debug("Could not introspect FastEmbed model directory", exc_info=True)
    return None


def ensure_clip_model_ready() -> str:
    """Prepare and validate both CLIP towers used by similarity search."""
    vision_model = _get_embed_model()
    text_model = _get_text_embed_model()
    vision_path = get_clip_local_model_path() or _loaded_fastembed_model_dir(vision_model)
    text_path = get_clip_text_local_model_path() or _loaded_fastembed_model_dir(text_model)
    if not vision_path or not text_path:
        raise RuntimeError(
            "CLIP Prepare completed without both local model directories: "
            f"vision={vision_path!r}, text={text_path!r}. Retry Prepare / Download."
        )
    endpoint = get_hf_endpoint_order(model_name="CLIP Similarity")[0]
    vision_missing = log_model_artifact_status(
        logger,
        model_id=CLIP_MODEL_NAME,
        revision=None,
        endpoint=endpoint,
        model_dir=Path(vision_path),
        required_files=CLIP_VISION_REQUIRED_FILES,
    )
    text_missing = log_model_artifact_status(
        logger,
        model_id=CLIP_TEXT_MODEL_NAME,
        revision=None,
        endpoint=endpoint,
        model_dir=Path(text_path),
        required_files=CLIP_TEXT_REQUIRED_FILES,
    )
    if vision_missing or text_missing:
        raise RuntimeError(
            "CLIP Prepare completed with missing runtime artifacts: "
            f"vision={list(vision_missing)}, text={list(text_missing)}. "
            "Retry Prepare / Download."
        )
    return vision_path


_text_embed_model = None


def _get_text_embed_model():
    """FastEmbed CLIP TEXT tower (singleton) — the same OpenAI CLIP ViT-B/32
    checkpoint as the vision model, split by fastembed, so text queries are
    cosine-comparable with the stored image embeddings. Downloads on first
    use (~65 MB) into the CLIP model dir."""
    global _text_embed_model
    if _text_embed_model is None:
        with _embed_lock:
            if _text_embed_model is None:
                try:
                    from fastembed import TextEmbedding  # type: ignore

                    local_model_path = get_clip_text_local_model_path()
                    model_kwargs = {
                        "model_name": CLIP_TEXT_MODEL_NAME,
                        "cache_dir": get_clip_model_dir(),
                    }
                    if local_model_path:
                        model_kwargs.update(
                            {
                                "local_files_only": True,
                                "specific_model_path": local_model_path,
                            }
                        )
                    if not local_model_path:
                        endpoint = get_hf_endpoint_order(model_name="CLIP Similarity")[0]
                        apply_hf_endpoint_monkeypatch(endpoint, purpose="CLIP text tower / FastEmbed")
                    with exclusive_ai_runtime("clip-text-load"):
                        _text_embed_model = TextEmbedding(**model_kwargs)
                except ImportError:
                    raise RuntimeError(
                        "fastembed not installed. Run: pip install fastembed"
                    )
                except Exception as exc:
                    raise RuntimeError(
                        "CLIP text model is not ready. Run Prepare / Download in Model Manager. "
                        f"Error: {exc}"
                    ) from exc
    return _text_embed_model


def embed_text(query: str) -> Optional[np.ndarray]:
    """Embed a natural-language query into the CLIP image-embedding space."""
    value = str(query or "").strip()
    if not value:
        return None
    model = _get_text_embed_model()
    try:
        with exclusive_ai_runtime("clip-text-inference"):
            embeddings = list(model.embed([value]))
        if embeddings:
            return np.array(embeddings[0], dtype=np.float32)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Similarity] Error embedding text query: %s", exc)
    return None


# Decomposition (2026-07): the pure byte/cosine helpers moved byte-verbatim to
# similarity_math. Re-imported so
# ``similarity.embedding_to_bytes`` / ``bytes_to_embedding`` /
# ``cosine_similarity`` keep resolving here for services.similarity_service,
# services.duplicate_group_service and the suites that from-import them --
# and because embed_batch below reads embedding_to_bytes as a module global.
from similarity_math import bytes_to_embedding, cosine_similarity, embedding_to_bytes


def embed_image_file(image_path: str, model=None) -> Optional[np.ndarray]:
    """Generate CLIP embedding for a single image file."""
    try:
        model = model or _get_embed_model()
        # FastEmbed accepts file paths
        with exclusive_ai_runtime("clip-similarity-inference"):
            embeddings = list(model.embed([image_path]))
        if embeddings:
            return np.array(embeddings[0], dtype=np.float32)
    except Exception as e:
        logger.warning("[Similarity] Error embedding %s: %s", image_path, e)
    return None


def embed_image_pil(pil_image: Image.Image) -> Optional[np.ndarray]:
    """Generate CLIP embedding for a PIL Image."""
    tmp_path = None
    try:
        model = _get_embed_model()
        # Save to temp bytes, then embed
        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        buf.seek(0)

        # FastEmbed needs a file path or bytes — use temp file
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(buf.getvalue())
            tmp_path = tmp.name

        with exclusive_ai_runtime("clip-similarity-upload"):
            embeddings = list(model.embed([tmp_path]))
        if embeddings:
            return np.array(embeddings[0], dtype=np.float32)
    except Exception as e:
        logger.error("[Similarity] Error embedding PIL image: %s", e)
    finally:
        # Clean up temp file with existence check
        if tmp_path is not None:
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError as e:
                logger.debug("[Similarity] Failed to delete temp file %s: %s", tmp_path, e)
    return None


# ---------------------------------------------------------------------------
# Decomposition (2026-07): the method families of SimilarityIndex live in the
# similarity_* sibling modules as mixins. THIS module remains a real FILE named ``similarity`` at
# backend/ depth and the single monkeypatch surface:
#   * The MODEL/SINGLETON family stays DEFINED here in one namespace -- the
#     ``_embed_model`` / ``_text_embed_model`` / ``_index`` lazy globals
#     (model_health._clip_model_loaded() reads ``_embed_model`` cross-module),
#     the ONE shared ``_embed_lock`` that serializes both model singletons AND
#     the embed_batch "already running" gate, the env-derived constants, and
#     the model entry points (_get_embed_model, _get_text_embed_model,
#     ensure_clip_model_ready, embed_text, embed_image_file, embed_image_pil).
#     conftest.py:66 autouse-patches ``get_state_dir`` on THIS module object.
#   * ``__init__`` and ``embed_batch`` stay on the class body below (report
#     section 5 pattern 1): embed_batch takes the shared _embed_lock, passes
#     ``backend_file=__file__`` -- pinned by value to basename similarity.py
#     with parent backend/ -- and reads six patched seams as THIS module's
#     globals (_get_embed_model, embed_image_file, verify_image_readable,
#     compute_image_content_fingerprint, resolve_existing_indexed_image_path,
#     EMBEDDING_BATCH_SIZE).
#   * Every moved read of a patched name resolves back through this facade
#     via _svc() at call time (see each sibling module's docstring). Lazy
#     heavy imports (fastembed) stay inside the model entry points here; no
#     sibling import can trigger model loading.
# The header import block above is kept verbatim (per-file F401 ignore in
# pyproject.toml) so every historical attribute keeps resolving here.
# ---------------------------------------------------------------------------
from similarity_index_core import _IndexCoreMixin
from similarity_topk import _TopKMixin
from similarity_vector_cache import _VectorCacheMixin


class SimilarityIndex(_IndexCoreMixin, _VectorCacheMixin, _TopKMixin):
    """Manages image embeddings and similarity search."""

    def __init__(self, db_module=None):
        self.db = db_module
        self._cancel_requested = False
        self._progress_lock = threading.Lock()
        self._progress = {
            "running": False,
            "total": 0,
            "processed": 0,
            "embedded": 0,
            "errors": 0,
            "skipped": 0,
            "unreadable": 0,
            "failed": 0,
            "message": "",
            "step": "idle",
            "current_item": None,
            "recent_issues": [],
            "started_at": None,
            "updated_at": None,
            "current_batch": 0,
            "total_batches": 0,
        }
        # Vectorized embedding cache (PERF-1). Guarded by _vector_cache_lock.
        # _vector_cache holds {"matrix", "ids", "paths", "filenames", "dim",
        # "signature"} once built; None means "not built / invalidated".
        self._vector_cache_lock = threading.Lock()
        self._vector_cache: Optional[Dict[str, Any]] = None
        # Optional hnswlib ANN index for the top-k bypass (slice 4b). Guarded by
        # _ann_lock; None means "not built / invalidated".
        self._ann_lock = threading.Lock()
        self._ann = None

    def embed_batch(self, image_ids: Optional[List[int]] = None):
        """
        Embed images in batch.

        If image_ids is None, embeds all images without embeddings.
        """
        with _embed_lock:
            if self._progress["running"]:
                return {"error": "Embedding already in progress"}
            self._progress["running"] = True
            self._cancel_requested = False

        with self._progress_lock:
            self._progress = {
                "running": True,
                "total": 0,
                "processed": 0,
                "embedded": 0,
                "errors": 0,
                "skipped": 0,
                "unreadable": 0,
                "failed": 0,
                "message": "Preparing embedding job...",
                "step": "preparing",
                "current_item": None,
                "recent_issues": [],
                "started_at": time.time(),
                "updated_at": time.time(),
                "current_batch": 0,
                "total_batches": 0,
            }

        try:
            with self.db.get_db() as conn:
                cursor = conn.cursor()

                if image_ids:
                    placeholders = ",".join("?" * len(image_ids))
                    cursor.execute(
                        f"SELECT id, path, content_fingerprint FROM images WHERE id IN ({placeholders}) AND embedding IS NULL AND COALESCE(is_readable, 1) = 1",
                        image_ids,
                    )
                else:
                    cursor.execute(
                        "SELECT id, path, content_fingerprint FROM images WHERE embedding IS NULL AND COALESCE(is_readable, 1) = 1"
                    )

                rows = cursor.fetchall()
                self._progress["total"] = len(rows)
                self._progress["updated_at"] = time.time()

            if not rows:
                self._progress["message"] = "No images pending embeddings."
                self._progress["step"] = "idle"
                return {
                    "processed": 0,
                    "errors": 0,
                    "total": 0,
                    "message": self._progress["message"],
                }

            self._progress["message"] = "Loading embedding model..."
            self._progress["step"] = "loading_model"
            self._progress["updated_at"] = time.time()
            try:
                model = _get_embed_model()
            except Exception as exc:
                self._progress["errors"] = len(rows)
                self._progress["failed"] = len(rows)
                self._progress["message"] = f"Embedding unavailable: {exc}"
                self._progress["step"] = "error"
                self._progress["updated_at"] = time.time()
                logger.error("Similarity embedding unavailable: %s", exc)
                return {
                    "processed": 0,
                    "errors": self._progress["errors"],
                    "total": self._progress["total"],
                    "message": self._progress["message"],
                }

            # Process in small batches to allow progress tracking
            batch_size = EMBEDDING_BATCH_SIZE
            total_batches = max(1, int(np.ceil(len(rows) / batch_size)))
            self._progress["total_batches"] = total_batches
            for i in range(0, len(rows), batch_size):
                if self._cancel_requested:
                    logger.info("Similarity embedding cancelled at %d/%d", self._progress["processed"], len(rows))
                    break
                batch = rows[i:i + batch_size]
                batch_index = (i // batch_size) + 1
                self._progress["message"] = f"Embedding batch {batch_index}/{total_batches}..."
                self._progress["step"] = "embedding"
                self._progress["current_batch"] = batch_index
                self._progress["updated_at"] = time.time()

                # Batch update embeddings - collect all updates first
                updates: List[Tuple[bytes, str, int, str]] = []
                for img_id, img_path, stored_content_fingerprint in batch:
                    resolved_path = resolve_existing_indexed_image_path(img_path, backend_file=__file__)
                    self._progress["current_item"] = os.path.basename(resolved_path or img_path)
                    self._progress["updated_at"] = time.time()

                    if not resolved_path:
                        self._progress["processed"] += 1
                        self._progress["errors"] += 1
                        self._progress["skipped"] += 1
                        self._record_issue("skipped", img_id, img_path, "File not found")
                        if hasattr(self.db, "mark_image_unreadable"):
                            self.db.mark_image_unreadable(img_id, "File not found")
                        continue

                    readable, read_error = verify_image_readable(resolved_path)
                    if not readable:
                        self._progress["processed"] += 1
                        self._progress["errors"] += 1
                        self._progress["unreadable"] += 1
                        self._record_issue("unreadable", img_id, img_path, read_error or "Unreadable image")
                        if hasattr(self.db, "mark_image_unreadable"):
                            self.db.mark_image_unreadable(img_id, read_error or "Unreadable image")
                        continue

                    before_fingerprint = _compute_embedding_content_fingerprint(resolved_path)
                    if before_fingerprint is None:
                        self._progress["processed"] += 1
                        self._progress["errors"] += 1
                        self._progress["skipped"] += 1
                        self._record_issue(
                            "skipped",
                            img_id,
                            img_path,
                            "Image pixels could not be fingerprinted before embedding; rescan and retry",
                        )
                        continue

                    if stored_content_fingerprint is not None:
                        stored_fingerprint = str(stored_content_fingerprint).strip()
                        if stored_fingerprint != before_fingerprint:
                            self._progress["processed"] += 1
                            self._progress["errors"] += 1
                            self._progress["skipped"] += 1
                            self._record_issue(
                                "skipped",
                                img_id,
                                img_path,
                                "Image pixels changed since the library index was updated; rescan and retry",
                            )
                            continue
                    else:
                        from services.derived_state_service import initialize_image_content_fingerprint

                        with self.db.get_db() as conn:
                            initialized = initialize_image_content_fingerprint(
                                conn.cursor(),
                                image_id=img_id,
                                content_fingerprint=before_fingerprint,
                            )
                        if not initialized:
                            self._progress["processed"] += 1
                            self._progress["errors"] += 1
                            self._progress["skipped"] += 1
                            self._record_issue(
                                "skipped",
                                img_id,
                                img_path,
                                "Image fingerprint changed before embedding started; rescan and retry",
                            )
                            continue

                    embedding = embed_image_file(resolved_path, model=model)
                    self._progress["processed"] += 1
                    if embedding is not None:
                        after_fingerprint = _compute_embedding_content_fingerprint(resolved_path)
                        if after_fingerprint != before_fingerprint:
                            self._progress["errors"] += 1
                            self._progress["skipped"] += 1
                            self._record_issue(
                                "skipped",
                                img_id,
                                img_path,
                                "Image pixels changed while its embedding was being computed; rescan and retry",
                            )
                            continue
                        updates.append((embedding_to_bytes(embedding), before_fingerprint, img_id, img_path))
                    else:
                        self._progress["errors"] += 1
                        self._progress["failed"] += 1
                        self._record_issue("failed", img_id, img_path, "Embedding backend returned no vector")

                # Single batch UPDATE for all embeddings in this chunk
                if updates:
                    from services.derived_state_service import write_image_embeddings

                    with self.db.get_db() as conn:
                        cursor = conn.cursor()
                        written_image_ids = set(
                            write_image_embeddings(
                                cursor,
                                [(embedding, fingerprint, image_id) for embedding, fingerprint, image_id, _path in updates],
                            )
                        )

                    for _embedding, _fingerprint, image_id, image_path in updates:
                        if image_id in written_image_ids:
                            self._progress["embedded"] += 1
                            continue
                        self._progress["errors"] += 1
                        self._progress["skipped"] += 1
                        self._record_issue(
                            "skipped",
                            image_id,
                            image_path,
                            "Image changed before its embedding could be saved; rescan and retry",
                        )

                    # Embeddings changed → the vectorized search cache is stale.
                    # Drop it so the next search rebuilds from fresh vectors.
                    if written_image_ids:
                        self.invalidate_vector_cache()

                gc.collect()

            if self._cancel_requested:
                # Cancel happy-path: distinguish "user cancelled mid-run" from
                # "completed normally" so the UI can show the right toast and
                # the progress endpoint isn't stuck on the success message.
                self._progress["message"] = (
                    f"Cancelled at {self._progress['processed']}/{len(rows)}."
                )
                self._progress["step"] = "cancelled"
            else:
                self._progress["message"] = (
                    f"Completed embeddings: {self._progress['embedded']} embedded"
                    + (
                        f", {self._progress['skipped']} skipped, "
                        f"{self._progress['unreadable']} unreadable, "
                        f"{self._progress['failed']} failed."
                        if self._progress["errors"]
                        else "."
                    )
                )
                self._progress["step"] = "done"
            self._progress["current_item"] = None
            self._progress["updated_at"] = time.time()

        except Exception as exc:
            # Without this branch a crash in the embed loop just propagates
            # into FastAPI's BackgroundTasks logger, leaving the progress
            # endpoint pinned at running=False + step="embedding" — visually
            # indistinguishable from "still in progress". Surface it instead.
            logger.exception("Similarity embedding failed: %s", exc)
            self._progress["step"] = "error"
            self._progress["message"] = f"Embedding failed: {exc}"
            self._progress["current_item"] = None
            self._progress["updated_at"] = time.time()

        finally:
            self._progress["running"] = False

        return {
            "processed": self._progress["processed"],
            "embedded": self._progress["embedded"],
            "errors": self._progress["errors"],
            "skipped": self._progress["skipped"],
            "unreadable": self._progress["unreadable"],
            "failed": self._progress["failed"],
            "total": self._progress["total"],
            "message": self._progress["message"],
            "recent_issues": list(self._progress["recent_issues"]),
        }


# Singleton
_index: Optional[SimilarityIndex] = None
_index_lock = threading.Lock()


def get_similarity_index(db_module=None) -> SimilarityIndex:
    """Get the singleton similarity index."""
    global _index
    current_index = _index
    if current_index is not None:
        return current_index

    with _index_lock:
        current_index = _index
        if current_index is None:
            current_index = SimilarityIndex(db_module=db_module)
            _index = current_index
        return current_index
