"""Inference-flow mixin for WD14Tagger (split from tagger.py, 2026-07).

Methods moved VERBATIM from tagger.py: _run_inference / _finalize_processed_images / _session_uses_gpu /
release_session / set_session_refresh_interval / tag / _runtime_chunk_size /
_empty_runtime_info / _merge_runtime_info / tag_batch. Zero manifested lines
-- these methods read no facade module globals (exclusive_ai_runtime is
origin-imported from ai_runtime_guard, the module the suites patch it on;
the GPU/OOM state machine itself stays byte-verbatim on the facade). The
logger keeps the original "tagger" channel.
"""

import gc
import logging
from typing import Any, Dict, List, Literal, Optional, Tuple, overload

import numpy as np
from PIL import Image

from ai_runtime_guard import (
    PRIORITY_BATCH,
    PRIORITY_NORMAL,
    exclusive_ai_runtime,
)

logger = logging.getLogger("tagger")


class _InferenceFlowMixin:
    """Single/batch tag entry points + session-refresh counters + runtime info."""

    def _run_inference(
        self, input_data: np.ndarray, *, allow_gpu_fallback: bool = True
    ) -> np.ndarray:
        """Run inference and optionally retry once on CPU if the GPU provider fails."""
        assert self.session is not None
        input_name = self._input_name or self.session.get_inputs()[0].name
        try:
            return self.session.run(None, {input_name: input_data})[self._output_index]
        except Exception as error:
            if not allow_gpu_fallback or not self._session_uses_gpu():
                raise
            self._fallback_to_cpu_session(error)
            assert self.session is not None
            self._refresh_session_metadata()
            retry_input_name = self._input_name or self.session.get_inputs()[0].name
            return self.session.run(None, {retry_input_name: input_data})[
                self._output_index
            ]

    def _finalize_processed_images(self, image_count: int) -> None:
        """Advance refresh counters after successfully processing one or more images."""
        if image_count <= 0:
            return

        self._images_since_session_create += image_count
        if (
            self._session_refresh_interval > 0
            and self._images_since_session_create >= self._session_refresh_interval
        ):
            try:
                self._recreate_session()
            except Exception as exc:
                logger.error("Session recreation failed after inference: %s", exc)

    def _session_uses_gpu(self) -> bool:
        """Return True when the active ONNX session is using CUDA or DirectML."""
        if self.session is None:
            return False
        current = self.session.get_providers()
        return "CUDAExecutionProvider" in current or "DmlExecutionProvider" in current

    def release_session(self) -> None:
        """Fully release the ONNX session (and its device memory) until next use.

        Used by the two-phase Smart Tag pipeline so the booru tagger does not
        stay resident in VRAM while a local VLM (ToriiGate) owns the GPU.
        ``load()`` / ``tag_batch`` lazily rebuild the session on the next call
        via the ``_loaded`` flag, so a released tagger self-heals transparently.
        """
        with self._load_lock:
            if self.session is not None:
                del self.session
                self.session = None
            self._loaded = False
            self._images_since_session_create = 0
            gc.collect()
        logger.info("ONNX session released for %s.", self.model_name)

    def set_session_refresh_interval(self, interval: int) -> None:
        """
        Set how many images to process before recreating the ONNX session.

        Args:
            interval: Number of images between session recreations.
                      0 disables automatic recreation.
        """
        self._session_refresh_interval = max(0, interval)
        logger.info(
            "Session refresh interval set to %d", self._session_refresh_interval
        )

    def tag(
        self,
        image_path: str,
        *,
        threshold: Optional[float] = None,
        character_threshold: Optional[float] = None,
        copyright_threshold: Optional[float] = None,
        priority: int = PRIORITY_NORMAL,
    ) -> Dict[str, Any]:
        """
        Tag a single image.

        ``priority`` is the AI-runtime admission lane and must be supplied by the
        caller, NOT pinned here: this method serves both ``POST /api/tag/single``
        (one file, user waiting -> ``PRIORITY_INTERACTIVE``) and Smart Tag's
        booru phase, which loops it over a whole job. Declaring the interactive
        lane here would hand that job interactive priority and invert the lane.

        Returns:
            {
                "general_tags": [{"tag": str, "confidence": float}, ...],
                "character_tags": [{"tag": str, "confidence": float}, ...],
                "rating": str,
                "rating_confidences": {"general": float, "sensitive": float, ...},
                "all_tags": [{"tag": str, "confidence": float}, ...]
            }
        """
        with exclusive_ai_runtime("wd14-tagger", priority=priority):
            if not self._loaded:
                self.load()

            # Load and preprocess image
            with Image.open(image_path) as image:
                input_data = np.expand_dims(self._preprocess(image), axis=0)

            output = self._run_inference(input_data)
            probs = output[0]
            result = self._process_probs(
                probs,
                threshold=threshold,
                character_threshold=character_threshold,
                copyright_threshold=copyright_threshold,
            )

            del input_data
            del output
            del probs

            self._finalize_processed_images(1)

            return result

    def _runtime_chunk_size(
        self, image_count: int, preferred_batch_size: Optional[int]
    ) -> int:
        """Return the maximum number of already-preprocessed inputs to hold at once."""
        if image_count <= 0:
            return 0
        if not self._supports_true_batch:
            return 1
        learned_chunk_size = (
            self._learned_stable_gpu_batch_size if self._session_uses_gpu() else None
        )
        candidates = [image_count]
        if preferred_batch_size:
            candidates.append(max(1, int(preferred_batch_size)))
        if learned_chunk_size:
            candidates.append(max(1, int(learned_chunk_size)))
        return max(1, min(candidates))

    @staticmethod
    def _empty_runtime_info() -> Dict[str, Any]:
        return {
            "initial_chunk_size": 0,
            "final_chunk_size": 0,
            "backoff_steps": [],
            "used_cpu_fallback": False,
            "attempted_gpu_backoff": False,
        }

    @staticmethod
    def _merge_runtime_info(
        total_info: Dict[str, Any], chunk_info: Dict[str, Any]
    ) -> None:
        chunk_initial = int(chunk_info.get("initial_chunk_size") or 0)
        chunk_final = int(chunk_info.get("final_chunk_size") or 0)
        if (
            total_info["initial_chunk_size"] == 0
            or chunk_initial > total_info["initial_chunk_size"]
        ):
            total_info["initial_chunk_size"] = chunk_initial
        if (
            total_info["final_chunk_size"] == 0
            or chunk_final < total_info["final_chunk_size"]
        ):
            total_info["final_chunk_size"] = chunk_final
        total_info["backoff_steps"].extend(chunk_info.get("backoff_steps") or [])
        total_info["used_cpu_fallback"] = bool(
            total_info["used_cpu_fallback"] or chunk_info.get("used_cpu_fallback")
        )
        total_info["attempted_gpu_backoff"] = bool(
            total_info["attempted_gpu_backoff"]
            or chunk_info.get("attempted_gpu_backoff")
        )

    @overload
    def tag_batch(
        self,
        image_paths: List[str],
        *,
        preferred_batch_size: Optional[int] = ...,
        min_batch_size: int = ...,
        threshold: Optional[float] = ...,
        character_threshold: Optional[float] = ...,
        copyright_threshold: Optional[float] = ...,
        return_runtime_info: Literal[False] = ...,
    ) -> List[Dict[str, Any]]: ...

    @overload
    def tag_batch(
        self,
        image_paths: List[str],
        *,
        preferred_batch_size: Optional[int] = ...,
        min_batch_size: int = ...,
        threshold: Optional[float] = ...,
        character_threshold: Optional[float] = ...,
        copyright_threshold: Optional[float] = ...,
        return_runtime_info: Literal[True],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]: ...

    def tag_batch(
        self,
        image_paths: List[str],
        *,
        preferred_batch_size: Optional[int] = None,
        min_batch_size: int = 1,
        threshold: Optional[float] = None,
        character_threshold: Optional[float] = None,
        copyright_threshold: Optional[float] = None,
        return_runtime_info: bool = False,
    ) -> Any:
        """Tag multiple images using adaptive true multi-image inference when supported."""
        if not image_paths:
            empty: List[Dict[str, Any]] = []
            if return_runtime_info:
                return empty, self._empty_runtime_info()
            return empty

        # Batch lane: this is the chunk every other AI feature waits behind.
        with exclusive_ai_runtime("wd14-tagger", priority=PRIORITY_BATCH):
            if not self._loaded:
                self.load()

            results: List[Optional[Dict[str, Any]]] = [None] * len(image_paths)
            runtime_info = self._empty_runtime_info()
            runtime_chunk_size = self._runtime_chunk_size(
                len(image_paths), preferred_batch_size
            )

            chunk_start = 0
            while chunk_start < len(image_paths):
                chunk_end = min(len(image_paths), chunk_start + runtime_chunk_size)
                chunk_paths = image_paths[chunk_start:chunk_end]
                prepared_inputs: List[np.ndarray] = []
                prepared_indices: List[int] = []

                prepared_chunk = self._preprocess_paths(chunk_paths)
                for offset, prepared in enumerate(prepared_chunk):
                    source_index = chunk_start + offset
                    if isinstance(prepared, Exception):
                        logger.error(
                            "Error preprocessing %s: %s", chunk_paths[offset], prepared
                        )
                        results[source_index] = self._build_empty_result(str(prepared))
                    else:
                        prepared_inputs.append(prepared)
                        prepared_indices.append(source_index)

                if prepared_inputs:
                    if self._supports_true_batch and len(prepared_inputs) > 1:
                        adaptive_results, chunk_info = (
                            self._run_true_batch_with_backoff(
                                prepared_inputs,
                                prepared_indices,
                                image_paths,
                                initial_chunk_size=len(prepared_inputs),
                                min_chunk_size=min_batch_size,
                                threshold=threshold,
                                character_threshold=character_threshold,
                                copyright_threshold=copyright_threshold,
                            )
                        )
                        self._merge_runtime_info(runtime_info, chunk_info)
                        for index, result in enumerate(adaptive_results):
                            if result is not None:
                                results[index] = result
                        runtime_chunk_size = (
                            self._runtime_chunk_size(
                                len(image_paths) - chunk_end,
                                preferred_batch_size,
                            )
                            or runtime_chunk_size
                        )
                    else:
                        chunk_info = {
                            "initial_chunk_size": 1,
                            "final_chunk_size": 1,
                            "backoff_steps": [],
                            "used_cpu_fallback": False,
                            "attempted_gpu_backoff": False,
                        }
                        for prepared_index, source_index in enumerate(prepared_indices):
                            try:
                                single_input = np.expand_dims(
                                    prepared_inputs[prepared_index], axis=0
                                )
                                output = self._run_inference(single_input)
                                results[source_index] = self._process_probs(
                                    output[0],
                                    threshold=threshold,
                                    character_threshold=character_threshold,
                                    copyright_threshold=copyright_threshold,
                                )
                                self._finalize_processed_images(1)
                                del single_input
                                del output
                            except Exception as error:
                                logger.error(
                                    "Error tagging %s: %s",
                                    image_paths[source_index],
                                    error,
                                )
                                results[source_index] = self._build_empty_result(
                                    str(error)
                                )
                        self._merge_runtime_info(runtime_info, chunk_info)

                del prepared_inputs
                gc.collect()
                chunk_start = chunk_end

            finalized_results = [
                result or self._build_empty_result() for result in results
            ]
            if return_runtime_info:
                return finalized_results, runtime_info
            return finalized_results
