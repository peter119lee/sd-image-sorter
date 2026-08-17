"""Inference mixin for OppaiOracleTagger (split from oppai_oracle_tagger.py, 2026-07).

Methods moved from oppai_oracle_tagger.py: _process_probs / _build_empty_result / _run_inference /
_recreate_gpu_session / _run_batch_inference_adaptive /
_maybe_refresh_session / tag / _preprocess_paths / tag_batch. Manifested
lines (the ONLY non-verbatim edits): _preprocess_paths resolves
_get_preprocess_executor and preprocess_image through _svc() at call time
(the executor pair stays DEFINED on the facade and the pin suite patches
``oppai_oracle_tagger._get_preprocess_executor`` / ``.preprocess_image``
there -- the line-770 deep-read pin), ``tag`` resolves preprocess_image the
same way, and _recreate_gpu_session resolves ``ort`` through _svc().
TAG_SCORES_ENABLED / TAG_SCORES_FLOOR stay read through the ``config``
module object at call time (the origin the suites patch; report H4: NOT
facade-owned), and exclusive_ai_runtime / looks_like_cuda_oom are
origin-imported from ai_runtime_guard (the module the suites patch them
on). The logger keeps the original "oppai_oracle_tagger" channel.
"""

import gc
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

import config
from ai_runtime_guard import (
    PRIORITY_BATCH,
    exclusive_ai_runtime,
    looks_like_cuda_oom,
)

logger = logging.getLogger("oppai_oracle_tagger")


def _svc():
    """Resolve the facade-owned preprocess/executor family at call time.

    The pin suite snapshots/restores ``oppai_oracle_tagger._preprocess_executor``
    and patches ``_get_preprocess_executor`` / ``preprocess_image`` / ``ort`` on
    the facade module object; a from-import here would freeze independent
    bindings those patches silently miss. The lazy import avoids a
    facade<->mixin load cycle.
    """
    import oppai_oracle_tagger

    return oppai_oracle_tagger


class _InferenceMixin:
    """Probs post-processing, single/batch tag entry points, GPU-OOM backoff."""



    # ----- inference -----------------------------------------------------

    def _process_probs(
        self,
        probs: np.ndarray,
        *,
        threshold: Optional[float] = None,
        character_threshold: Optional[float] = None,
    ) -> Dict[str, Any]:
        thresh = float(threshold) if threshold is not None else self.threshold
        # character_threshold accepted for API compat; OppaiOracle has no
        # character tags so the parameter is unused beyond bookkeeping.
        del character_threshold

        values = np.asarray(probs, dtype=np.float32).reshape(-1)
        invalid = ~np.isfinite(values)
        if np.any(invalid):
            values = np.where(invalid, 0.0, values)
        out_of_range = (values < -1e-6) | (values > 1.0 + 1e-6)
        if np.any(out_of_range):
            values = np.where(out_of_range, 0.0, values)
        values = np.clip(values, 0.0, 1.0)

        result: Dict[str, Any] = {
            "general_tags": [],
            "character_tags": [],
            "rating": "unknown",
            "rating_confidences": {},
            "all_tags": [],
        }

        # BE-1: collect every score >= the configured floor for the
        # tag_scores table (virtual re-threshold) — same seam as
        # WD14Tagger._process_probs, kept in sync.
        collect_scores = bool(config.TAG_SCORES_ENABLED)
        score_floor = float(config.TAG_SCORES_FLOOR)
        raw_scores: List[Dict[str, Any]] = []

        for tag_id, tag_name in self.general_tags:
            if tag_id < values.shape[0]:
                conf = float(values[tag_id])
                if collect_scores and conf >= score_floor:
                    raw_scores.append(
                        {"tag": tag_name, "score": conf, "category": "general"}
                    )
                if conf >= thresh:
                    entry = {"tag": tag_name, "confidence": conf}
                    result["general_tags"].append(entry)
                    result["all_tags"].append(entry)

        rating_probs: List[Tuple[str, float]] = []
        for tag_id, rating_name in self.rating_tags:
            if tag_id < values.shape[0]:
                conf = float(values[tag_id])
                rating_probs.append((rating_name, conf))
                result["rating_confidences"][rating_name] = conf
                if collect_scores and conf >= score_floor:
                    raw_scores.append(
                        {"tag": rating_name, "score": conf, "category": "rating"}
                    )

        if rating_probs:
            best = max(rating_probs, key=lambda x: x[1])
            result["rating"] = best[0]
            result["all_tags"].append({"tag": best[0], "confidence": best[1]})

        if collect_scores:
            result["tag_scores"] = raw_scores

        result["general_tags"].sort(key=lambda x: x["confidence"], reverse=True)
        result["all_tags"].sort(key=lambda x: x["confidence"], reverse=True)
        return result

    def _build_empty_result(self, error: Optional[str] = None) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "general_tags": [],
            "character_tags": [],
            "rating": "unknown",
            "rating_confidences": {},
            "all_tags": [],
        }
        if error:
            result["error"] = error
        return result

    def _run_inference(
        self,
        pixel_values: np.ndarray,
        padding_mask: np.ndarray,
        *,
        allow_cpu_fallback: bool = True,
    ) -> np.ndarray:
        assert self.session is not None
        try:
            outputs = self.session.run(
                ["probabilities"],
                {"pixel_values": pixel_values, "padding_mask": padding_mask},
            )
        except Exception as exc:
            # When the caller is doing GPU batch-size backoff it passes
            # ``allow_cpu_fallback=False`` so the OOM propagates and it can retry
            # a SMALLER batch on the GPU first, instead of this method silently
            # dropping the whole run to CPU (permanently) on the first failure.
            if self._session_uses_gpu() and allow_cpu_fallback:
                logger.warning("OppaiOracle GPU inference failed (%s); rebuilding on CPU.", exc)
                self.session = self._create_session(
                    self._resolved_model_path or "",
                    self._build_session_options(gpu_enabled=False),
                    ["CPUExecutionProvider"],
                )
                self.use_gpu = False
                outputs = self.session.run(
                    ["probabilities"],
                    {"pixel_values": pixel_values, "padding_mask": padding_mask},
                )
            else:
                raise
        return outputs[0]

    def _recreate_gpu_session(self) -> None:
        """Rebuild the ONNX session on the GPU to clear CUDA state between
        batch-size backoff steps (mirrors the WD14 tagger). Best-effort: if the
        rebuild fails or no GPU provider is available, the existing session is
        kept and the next attempt simply runs on it."""
        if not self._resolved_model_path or not self._session_uses_gpu():
            return
        try:
            available = _svc().ort.get_available_providers()
            providers = [
                p
                for p in ("CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider")
                if p in available
            ]
            if not any(p in providers for p in ("CUDAExecutionProvider", "DmlExecutionProvider")):
                return
            self.session = self._create_session(
                self._resolved_model_path,
                self._build_session_options(gpu_enabled=True),
                providers,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("OppaiOracle GPU session rebuild failed (%s); keeping current session.", exc)

    def _run_batch_inference_adaptive(
        self,
        pixel_values: np.ndarray,
        padding_mask: np.ndarray,
        *,
        min_chunk_size: int = 1,
        backoff_steps: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[np.ndarray, int]:
        """Run inference on a preprocessed batch, halving the GPU sub-chunk on
        failure (e.g. CUDA OOM) and rebuilding the GPU session before giving up
        the GPU. Falls back to CPU only once the sub-chunk is at the floor.

        Mirrors the WD14 tagger's adaptive backoff so a too-large batch degrades
        to a smaller GPU batch instead of crashing or permanently dropping to
        CPU. Returns ``(stacked_probabilities, final_chunk_size)`` where the
        second value lets the caller remember the largest size that worked.
        """
        total = int(pixel_values.shape[0])
        if total == 0:
            return pixel_values[:0], max(1, int(min_chunk_size))

        floor = max(1, int(min_chunk_size))
        collected: List[np.ndarray] = []
        chunk = total
        cursor = 0
        while cursor < total:
            current = min(chunk, total - cursor)
            sub_pv = pixel_values[cursor:cursor + current]
            sub_pm = padding_mask[cursor:cursor + current]
            try:
                probs = self._run_inference(sub_pv, sub_pm, allow_cpu_fallback=False)
            except Exception as exc:
                if self._session_uses_gpu() and current > floor and looks_like_cuda_oom(exc):
                    next_chunk = max(floor, current // 2)
                    logger.warning(
                        "OppaiOracle GPU OOM at batch %d (%s); retrying at %d.",
                        current, exc, next_chunk,
                    )
                    if backoff_steps is not None:
                        backoff_steps.append(
                            {"from": current, "to": next_chunk, "mode": "gpu_backoff", "error": str(exc)}
                        )
                    gc.collect()
                    self._recreate_gpu_session()
                    chunk = next_chunk
                    continue
                # Non-OOM error, or already at the floor: last-resort CPU fallback
                # for this sub-batch. If even CPU fails, let it propagate so the
                # caller can mark just this chunk's images empty (graceful degrade).
                probs = self._run_inference(sub_pv, sub_pm, allow_cpu_fallback=True)
            for row in probs:
                collected.append(row)
            cursor += current
        return np.stack(collected, axis=0), chunk

    def _maybe_refresh_session(self, image_count: int) -> None:
        if image_count <= 0:
            return
        self._images_since_session_create += image_count
        if (
            self._session_refresh_interval > 0
            and self._images_since_session_create >= self._session_refresh_interval
        ):
            self._images_since_session_create = 0
            try:
                self._loaded = False
                self.load()
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("OppaiOracle session recreate failed: %s", exc)

    def tag(
        self,
        image_path: str,
        *,
        threshold: Optional[float] = None,
        character_threshold: Optional[float] = None,
    ) -> Dict[str, Any]:
        # Batch lane: both tag() and tag_batch() are reached only from bulk
        # tagging (the gallery tag worker and Smart Tag's booru phase), never
        # from a synchronous single-image request.
        with exclusive_ai_runtime("oppai-oracle-tagger", priority=PRIORITY_BATCH):
            if not self._loaded:
                self.load()
            try:
                with Image.open(image_path) as image:
                    pixel_values, padding_mask = _svc().preprocess_image(
                        image, target=self._target, pad_color=self._pad_color
                    )
            except Exception as exc:
                return self._build_empty_result(str(exc))

            pv_batch = np.expand_dims(pixel_values, axis=0)
            pm_batch = np.expand_dims(padding_mask, axis=0)
            probs = self._run_inference(pv_batch, pm_batch)
            self._maybe_refresh_session(1)
            return self._process_probs(
                probs[0],
                threshold=threshold,
                character_threshold=character_threshold,
            )

    def _preprocess_paths(self, paths: List[str]) -> List[Any]:
        """Decode + letterbox-preprocess a chunk in parallel; returns a list
        aligned with ``paths`` where each entry is a ``(pixel_values,
        padding_mask)`` tuple or the Exception that failed it (per-image
        isolation, order preserved). Threads overlap CPU decode so the GPU is
        not starved; serial for a single image."""
        def _one(path: str):
            with Image.open(path) as image:
                return _svc().preprocess_image(image, target=self._target, pad_color=self._pad_color)

        if len(paths) <= 1:
            serial: List[Any] = []
            for path in paths:
                try:
                    serial.append(_one(path))
                except Exception as exc:
                    serial.append(exc)
            return serial

        futures = [_svc()._get_preprocess_executor().submit(_one, path) for path in paths]
        prepared: List[Any] = []
        for future in futures:
            try:
                prepared.append(future.result())
            except Exception as exc:
                prepared.append(exc)
        return prepared

    def tag_batch(
        self,
        image_paths: List[str],
        *,
        preferred_batch_size: Optional[int] = None,
        min_batch_size: int = 1,
        threshold: Optional[float] = None,
        character_threshold: Optional[float] = None,
        return_runtime_info: bool = False,
    ) -> Any:
        if not image_paths:
            empty: List[Dict[str, Any]] = []
            if return_runtime_info:
                return empty, {
                    "initial_chunk_size": 0,
                    "final_chunk_size": 0,
                    "backoff_steps": [],
                    "used_cpu_fallback": False,
                    "attempted_gpu_backoff": False,
                }
            return empty

        with exclusive_ai_runtime("oppai-oracle-tagger", priority=PRIORITY_BATCH):
            if not self._loaded:
                self.load()

            initial_chunk = max(1, int(preferred_batch_size or 1))
            chunk = initial_chunk
            min_chunk = max(1, int(min_batch_size or 1))
            results: List[Dict[str, Any]] = [self._build_empty_result() for _ in image_paths]
            backoff_steps: List[Dict[str, Any]] = []

            cursor = 0
            while cursor < len(image_paths):
                end = min(len(image_paths), cursor + chunk)
                batch_paths = image_paths[cursor:end]
                pv_list: List[np.ndarray] = []
                pm_list: List[np.ndarray] = []
                indices: List[int] = []
                prepared_chunk = self._preprocess_paths(batch_paths)
                for offset, prepared in enumerate(prepared_chunk):
                    src_idx = cursor + offset
                    if isinstance(prepared, Exception):
                        logger.error("OppaiOracle preprocess failed for %s: %s", batch_paths[offset], prepared)
                        results[src_idx] = self._build_empty_result(str(prepared))
                    else:
                        pv, pm = prepared
                        pv_list.append(pv)
                        pm_list.append(pm)
                        indices.append(src_idx)

                if pv_list:
                    pv_batch = np.stack(pv_list, axis=0).astype(np.float32, copy=False)
                    pm_batch = np.stack(pm_list, axis=0).astype(bool, copy=False)
                    try:
                        probs_batch, used_chunk = self._run_batch_inference_adaptive(
                            pv_batch,
                            pm_batch,
                            min_chunk_size=min_chunk,
                            backoff_steps=backoff_steps,
                        )
                    except Exception as exc:
                        # Unrecoverable (even the CPU fallback failed): degrade
                        # gracefully by marking this chunk's images empty instead
                        # of sinking the whole call.
                        logger.error(
                            "OppaiOracle inference failed for a %d-image batch (%s); marking empty.",
                            len(indices), exc,
                        )
                        for src_idx in indices:
                            results[src_idx] = self._build_empty_result(str(exc))
                    else:
                        for i, src_idx in enumerate(indices):
                            results[src_idx] = self._process_probs(
                                probs_batch[i],
                                threshold=threshold,
                                character_threshold=character_threshold,
                            )
                        # Remember the largest sub-chunk that actually fit so later
                        # windows don't re-attempt (and re-OOM) the original size.
                        if used_chunk < chunk:
                            chunk = max(min_chunk, used_chunk)
                    self._maybe_refresh_session(len(indices))
                cursor = end

            if return_runtime_info:
                return results, {
                    "initial_chunk_size": initial_chunk,
                    "final_chunk_size": chunk,
                    "backoff_steps": backoff_steps,
                    "used_cpu_fallback": not self.use_gpu,
                    "attempted_gpu_backoff": bool(backoff_steps),
                }
            return results
