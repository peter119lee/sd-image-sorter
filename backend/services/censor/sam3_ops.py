"""SAM3 mask refinement, text segmentation, background removal, batch refine.

Methods moved verbatim from services/censor_service.py (decomposition 2026-07) except the manifest lines: all four
methods resolve get_model_health through _svc() at call time. The seam is
patched on the facade module object across the reader suites, so a bare
re-import here would make those patches silently miss. The lazy
from-sam3_refiner imports are patched via sys.modules on the origin module and
move freely; no real weights are ever loaded at import time.
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO
from typing import TYPE_CHECKING, Any, Dict, List

from fastapi import HTTPException
from PIL import Image

import database as db

if TYPE_CHECKING:  # annotation-only; never imported at runtime (no facade cycle)
    from services.censor_service import BatchMaskRefineRequest, MaskRefineRequest, RemoveBackgroundRequest, TextSegmentRequest

logger = logging.getLogger("services.censor_service")


def _svc():
    """Resolve facade-owned seams/constants through services.censor_service at call time.

    Tests patch module attributes on the facade; a from-import here would
    freeze an independent binding those patches silently miss. The lazy import avoids a facade<->mixin load cycle.
    """
    import services.censor_service as censor_service

    return censor_service


class _Sam3Mixin:
    """SAM3 slice of CensorService (assembled in services/censor_service.py)."""

    def refine_mask(self, request: MaskRefineRequest) -> Dict[str, Any]:
        """Refine a bounding box into a pixel-precise segmentation mask using SAM3."""
        try:
            from sam3_refiner import get_sam3_refiner
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="SAM3 module unavailable"
            )

        sam3_status = _svc().get_model_health()["censor"]["sam3"]
        if not sam3_status["available"]:
            raise HTTPException(
                status_code=503,
                detail=sam3_status["message"]
            )

        image_data = db.get_image_by_id(request.image_id)
        if not image_data:
            raise HTTPException(status_code=404, detail="Image not found")

        image_path = self._resolve_source_image_path(
            image_data["path"],
            image_id=request.image_id,
            action_label="SAM3 mask refinement",
        )

        try:
            with Image.open(image_path) as src:
                image = src.convert("RGB")
            refiner = get_sam3_refiner()
            mask = refiner.refine_box(
                image,
                request.box,
                text_prompt=request.text_prompt,
                confidence_threshold=request.sam3_confidence,
            )

            if mask is None:
                return {
                    "status": "fallback",
                    "message": "SAM3 could not refine this box. Using bounding box.",
                    "mask": None,
                    "box": request.box,
                }

            return {
                "status": "ok",
                **self._build_mask_payload(Image.fromarray(mask * 255, mode="L")),
                "box": request.box,
            }
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception:
            raise HTTPException(status_code=500, detail="Mask refinement failed")

    def segment_text(self, request: TextSegmentRequest) -> Dict[str, Any]:
        """Segment objects by text description using SAM3."""
        try:
            from sam3_refiner import get_sam3_refiner
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="SAM3 module unavailable"
            )

        sam3_status = _svc().get_model_health()["censor"]["sam3"]
        if not sam3_status["available"]:
            raise HTTPException(
                status_code=503,
                detail=sam3_status["message"]
            )

        image_data = db.get_image_by_id(request.image_id)
        if not image_data:
            raise HTTPException(status_code=404, detail="Image not found")

        image_path = self._resolve_source_image_path(
            image_data["path"],
            image_id=request.image_id,
            action_label="Text segmentation",
        )

        try:
            with Image.open(image_path) as src:
                image = src.convert("RGB")
            refiner = get_sam3_refiner()
            mask = refiner.segment_by_text(
                image,
                request.text_prompt,
                presence_threshold=request.presence_threshold,
            )

            if mask is None:
                return {
                    "status": "no_match",
                    "message": f"No regions matched text prompt: '{request.text_prompt}'",
                    "mask": None,
                }

            return {
                "status": "ok",
                **self._build_mask_payload(Image.fromarray(mask * 255, mode="L")),
                "text_prompt": request.text_prompt,
            }
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception:
            raise HTTPException(status_code=500, detail="Text segmentation failed")

    def remove_background(self, request: RemoveBackgroundRequest) -> Dict[str, Any]:
        """Remove background using SAM3 foreground detection."""
        try:
            from sam3_refiner import get_sam3_refiner
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="SAM3 module unavailable"
            )

        sam3_status = _svc().get_model_health()["censor"]["sam3"]
        if not sam3_status["available"]:
            raise HTTPException(
                status_code=503,
                detail=sam3_status["message"]
            )

        image_data = db.get_image_by_id(request.image_id)
        if not image_data:
            raise HTTPException(status_code=404, detail="Image not found")

        image_path = self._resolve_source_image_path(
            image_data["path"],
            image_id=request.image_id,
            action_label="Background removal",
        )

        try:
            with Image.open(image_path) as src:
                image = src.convert("RGB")

            # Use SAM3 to detect foreground objects
            # We use a generic "main subject" prompt to detect the foreground
            refiner = get_sam3_refiner()
            mask = refiner.segment_by_text(
                image,
                "foreground subject",
                presence_threshold=request.edge_threshold,
            )

            if mask is None:
                return {
                    "status": "no_match",
                    "message": "No foreground object detected",
                    "preview": None,
                }

            # Create output image based on fill_mode
            img_rgba = image.convert('RGBA')

            # Convert mask to PIL Image and ensure it's the right size
            mask_image = Image.fromarray((mask * 255).astype('uint8'), mode='L')
            if mask_image.size != img_rgba.size:
                mask_image = mask_image.resize(img_rgba.size, Image.LANCZOS)

            if request.fill_mode == 'transparent':
                # Apply alpha channel for transparency
                img_rgba.putalpha(mask_image)
                output = img_rgba
            else:
                # Create background color
                bg_color = (255, 255, 255, 255) if request.fill_mode == 'white' else (0, 0, 0, 255)
                background = Image.new('RGBA', img_rgba.size, bg_color)

                # Composite: paste foreground onto colored background using mask
                background.paste(img_rgba, (0, 0), mask_image)
                output = background

            # Encode as base64 preview
            buffer = BytesIO()
            output.save(buffer, format='PNG')
            buffer.seek(0)
            preview_base64 = f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('utf-8')}"

            return {
                "status": "ok",
                "preview": preview_base64,
                "fill_mode": request.fill_mode,
                "edge_threshold": request.edge_threshold,
            }
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception as exc:
            logger.error("Background removal failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail="Background removal failed")

    def batch_refine_mask(self, request: "BatchMaskRefineRequest") -> Dict[str, Any]:
        """Run SAM3 mask refinement on multiple images/boxes sequentially."""
        try:
            from sam3_refiner import get_sam3_refiner
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="SAM3 module unavailable"
            )

        sam3_status = _svc().get_model_health()["censor"]["sam3"]
        if not sam3_status["available"]:
            raise HTTPException(
                status_code=503,
                detail=sam3_status["message"]
            )

        results: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        refiner = None

        for idx, item in enumerate(request.items):
            try:
                image_data = db.get_image_by_id(item.image_id)
                if not image_data:
                    errors.append({"index": idx, "image_id": item.image_id, "error": "Image not found"})
                    continue

                image_path = self._resolve_source_image_path(
                    image_data["path"],
                    image_id=item.image_id,
                    action_label="SAM3 batch refinement",
                )
                with Image.open(image_path) as src:
                    image = src.convert("RGB")

                if refiner is None:
                    refiner = get_sam3_refiner()

                # Per-item confidence wins; otherwise the batch-level slider
                # value gates how confident SAM3 must be before a box is
                # accepted as a refined mask (low-confidence -> "fallback").
                item_confidence = (
                    item.sam3_confidence
                    if item.sam3_confidence is not None
                    else request.sam3_confidence
                )
                mask = refiner.refine_box(
                    image,
                    item.box,
                    text_prompt=item.text_prompt,
                    confidence_threshold=item_confidence,
                )

                if mask is None:
                    results.append({
                        "index": idx,
                        "image_id": item.image_id,
                        "status": "fallback",
                        "message": "SAM3 could not refine this box. Using bounding box.",
                        "mask": None,
                        "box": item.box,
                    })
                else:
                    results.append({
                        "index": idx,
                        "image_id": item.image_id,
                        "status": "ok",
                        **self._build_mask_payload(Image.fromarray(mask * 255, mode="L")),
                        "box": item.box,
                    })
            except Exception as exc:
                logger.warning("Batch SAM3 refinement failed for item %d (image %d): %s", idx, item.image_id, exc)
                errors.append({"index": idx, "image_id": item.image_id, "error": str(exc)})
            finally:
                if (idx + 1) % 4 == 0:
                    import gc as _gc
                    _gc.collect()
                    try:
                        import torch as _torch
                        if _torch.cuda.is_available():
                            _torch.cuda.empty_cache()
                    except Exception:
                        pass

        refined = sum(1 for r in results if r.get("status") == "ok")
        return {
            "status": "ok",
            "total": len(request.items),
            "completed": len(results),
            # `completed` = boxes that ran (ok + fallback). Split it out so the UI
            # doesn't report SAM3-could-not-refine fallbacks as real refinements.
            "refined": refined,
            "fallback": len(results) - refined,
            "failed": len(errors),
            "results": results,
            "errors": errors,
        }
