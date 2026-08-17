"""Roadmap-C missing-file repair review (resolve ambiguous reconnect matches)
plus the by-path preview its UI uses for not-yet-indexed candidate files.

Methods moved verbatim from services/image_service.py (decomposition
2026-07). get_thumbnail_async resolves through the facade at call time
(latent bare-import seam).
"""

import io
import os
from datetime import datetime
from email.utils import format_datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from PIL import UnidentifiedImageError

import database as db
from thumbnail_cache import generate_placeholder_thumbnail
from utils.path_validation import ALLOWED_IMAGE_EXTENSIONS, normalize_user_path, validate_file_path


def _svc():
    """Resolve facade-owned seams/constants through services.image_service at call time.

    Tests patch module attributes on the facade; a ``from`` import here would
    freeze an independent binding those patches silently miss. The lazy import avoids a facade<->mixin load cycle.
    """
    import services.image_service as image_service

    return image_service


def get_thumbnail_async(*args, **kwargs):
    """Facade-seam proxy (latent seam; returns the facade coroutine, awaited at the call site)."""
    return _svc().get_thumbnail_async(*args, **kwargs)


class RepairReviewMixin:
    """Roadmap-C repair-review slice of ImageService (assembled in services/image_service.py)."""

    # ------------------------------------------------------------------
    # Roadmap-C: missing-file repair review (resolve ambiguous matches)
    # ------------------------------------------------------------------
    def _reconnect_run_is_active(self) -> bool:
        with self._reconnect_lock:
            return self._reconnect_progress.get("status") in {"running", "cancelling"}

    def get_repair_candidates(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str = "pending",
    ) -> Dict[str, Any]:
        """List persisted ambiguous-match reviews, enriched with candidate rows.

        Each candidate is loaded fresh from the images table so callers see the
        current path/size and whether the candidate's own file is still missing.
        Candidate ids that no longer exist (deleted since the run) are skipped.
        ``status='all'`` lists every status; otherwise it scopes to one status.
        """
        normalized_status = str(status or "pending").strip().lower() or "pending"
        scope = None if normalized_status == "all" else normalized_status
        listing = db.list_reconnect_reviews(status=scope, limit=limit, offset=offset)

        items: List[Dict[str, Any]] = []
        for review in listing["items"]:
            candidate_ids = review.get("candidate_ids") or []
            rows_by_id = db.get_images_by_ids(candidate_ids) if candidate_ids else {}
            candidates: List[Dict[str, Any]] = []
            for image_id in candidate_ids:
                row = rows_by_id.get(image_id)
                if not row:
                    continue  # candidate deleted since the run; drop it
                candidate_path = row.get("path") or ""
                candidates.append({
                    "image_id": image_id,
                    "path": candidate_path,
                    "file_size": row.get("file_size"),
                    "source_mtime_ns": row.get("source_mtime_ns"),
                    "still_missing": not (bool(candidate_path) and os.path.isfile(candidate_path)),
                })
            found_path = review.get("found_path") or ""
            items.append({
                "review_id": review.get("id"),
                "filename": review.get("filename"),
                "found_path": found_path,
                "found_exists": bool(found_path) and os.path.isfile(found_path),
                "candidate_count": review.get("candidate_count"),
                "run_started_at": review.get("run_started_at"),
                "status": review.get("status"),
                "resolution": review.get("resolution"),
                "candidates": candidates,
            })
        return {"total": listing["total"], "items": items}

    def confirm_repair(
        self,
        *,
        review_id: int,
        action: str,
        chosen_image_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Resolve one ambiguous-match review (pick / merge / skip).

        - ``pick``  relinks ``chosen_image_id`` to the found file.
        - ``merge`` relinks ``chosen_image_id`` and deletes the other still-existing
          candidate rows.
        - ``skip``  records the decision without touching any image row.

        Refuses with 409 while a reconnect run is active. If the found path is
        already indexed as a different row, the review is marked ``conflict`` and
        a 409 is raised (never silently duplicating a row).
        """
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"pick", "merge", "skip"}:
            raise HTTPException(status_code=400, detail="action must be one of: pick, merge, skip")

        # Serialize against a running reconnect and against concurrent confirms
        # using the same lock the reconnect run holds.
        with self._reconnect_lock:
            if self._reconnect_progress.get("status") in {"running", "cancelling"}:
                raise HTTPException(
                    status_code=409,
                    detail="A missing-file reconnect run is in progress. Try again once it finishes.",
                )

            review = db.get_reconnect_review(review_id)
            if not review:
                raise HTTPException(status_code=404, detail="Repair review not found")
            if review.get("status") != db.REVIEW_STATUS_PENDING:
                raise HTTPException(status_code=409, detail="This repair review has already been resolved")

            candidate_ids = review.get("candidate_ids") or []

            if normalized_action == "skip":
                db.resolve_reconnect_review(
                    review_id, status=db.REVIEW_STATUS_RESOLVED, resolution="skip"
                )
                return {"status": "resolved", "review_id": int(review_id), "resolution": "skip"}

            # pick / merge both relink a chosen candidate.
            if chosen_image_id is None:
                raise HTTPException(status_code=400, detail="chosen_image_id is required for pick/merge")
            try:
                chosen_image_id = int(chosen_image_id)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="chosen_image_id must be an integer")
            if chosen_image_id not in candidate_ids:
                raise HTTPException(
                    status_code=400,
                    detail="chosen_image_id is not one of this review's candidates",
                )

            found_path = review.get("found_path") or ""
            if not found_path or not os.path.isfile(found_path):
                raise HTTPException(status_code=409, detail="The found file no longer exists on disk")
            resolved_found_path = os.path.abspath(found_path)

            # Relinking onto a path already owned by a different row would create
            # a duplicate index entry; mark the review conflicted and refuse.
            existing = db.get_image_by_path(resolved_found_path)
            if existing and int(existing.get("id") or 0) != chosen_image_id:
                db.resolve_reconnect_review(
                    review_id,
                    status=db.REVIEW_STATUS_CONFLICT,
                    resolution="conflict",
                    chosen_image_id=chosen_image_id,
                )
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"{resolved_found_path} is already indexed as image "
                        f"{existing.get('id')}; nothing was relinked."
                    ),
                )

            stat_result = os.stat(resolved_found_path)
            db.reconnect_image_source_path(
                chosen_image_id,
                resolved_found_path,
                source_mtime_ns=int(stat_result.st_mtime_ns),
                source_size=int(stat_result.st_size),
                source_file_mtime=datetime.fromtimestamp(stat_result.st_mtime),
            )

            deleted_ids: List[int] = []
            if normalized_action == "merge":
                others = [cid for cid in candidate_ids if cid != chosen_image_id]
                existing_others = [cid for cid in others if db.get_image_by_id(cid)]
                if existing_others:
                    db.delete_images_by_ids(existing_others)
                    deleted_ids = existing_others

            db.resolve_reconnect_review(
                review_id,
                status=db.REVIEW_STATUS_RESOLVED,
                resolution=normalized_action,
                chosen_image_id=chosen_image_id,
            )
            return {
                "status": "resolved",
                "review_id": int(review_id),
                "resolution": normalized_action,
                "image_id": chosen_image_id,
                "new_path": resolved_found_path,
                "deleted_ids": deleted_ids,
            }

    async def get_image_preview_by_path(self, path: str, size: int = 256) -> StreamingResponse:
        """Serve a thumbnail for a found-but-unlinked file by absolute path.

        Used by the repair-review UI to preview a candidate file that is not yet
        an indexed image (the id-based thumbnail endpoint can't reach it). The
        path is validated (traversal-safe, must exist, must be an allowed image
        type) before any read; size is clamped to 1..1024.
        """
        normalized_size = max(1, min(int(size or 256), 1024))
        is_valid, error = validate_file_path(path, ALLOWED_IMAGE_EXTENSIONS)
        if not is_valid:
            raise HTTPException(status_code=404, detail=error or "Image not found")

        source_path = normalize_user_path(path)
        try:
            thumbnail_bytes, last_modified, cache_hit = await get_thumbnail_async(source_path, normalized_size)
            return StreamingResponse(
                io.BytesIO(thumbnail_bytes),
                media_type="image/webp",
                headers={
                    "Cache-Control": f"public, max-age={86400 if cache_hit else 3600}",
                    "Last-Modified": format_datetime(last_modified, usegmt=True),
                    "X-Thumbnail-Cache": "HIT" if cache_hit else "MISS",
                },
            )
        except (UnidentifiedImageError, OSError):
            placeholder_bytes = generate_placeholder_thumbnail(normalized_size)
            return StreamingResponse(
                io.BytesIO(placeholder_bytes),
                media_type="image/webp",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Thumbnail-Cache": "MISS",
                    "X-Thumbnail-Placeholder": "UNREADABLE",
                },
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Failed to generate preview") from exc
