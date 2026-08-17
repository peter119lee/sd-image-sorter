"""Scoring / rating mixin for WD14Tagger (split from tagger.py, 2026-07).

Methods moved VERBATIM from tagger.py: _build_empty_result / _normalize_output_probs / _process_probs /
_derive_fallback_rating -- the pure probs-to-payload half of WD14Tagger.
Zero manifested lines. TAG_SCORES_ENABLED / TAG_SCORES_FLOOR stay read
through the ``config`` module object at call time (the origin the suites
patch), exactly as before the split. The logger keeps the original "tagger"
channel.
"""

import logging
from typing import Any, Dict, List, Optional

import numpy as np

import config

logger = logging.getLogger("tagger")


class _ScoringMixin:
    """Probs -> public result payload (thresholds, tag_scores floor, rating)."""

    def _build_empty_result(self, error: Optional[str] = None) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "general_tags": [],
            "copyright_tags": [],
            "character_tags": [],
            "rating": "unknown",
            "rating_confidences": {},
            "all_tags": [],
        }
        if error:
            result["error"] = error
        return result

    def _normalize_output_probs(self, probs: np.ndarray) -> np.ndarray:
        """Convert model output to bounded confidence probabilities before thresholding."""
        values = np.asarray(probs, dtype=np.float32)
        invalid_values = ~np.isfinite(values)
        if np.any(invalid_values):
            logger.warning(
                "Tagger output for %s contained NaN/Inf values; ignoring those scores.",
                self.model_name,
            )
            values = np.where(invalid_values, 0.0, values)

        if self._output_activation == "sigmoid":
            clipped_logits = np.clip(values, -80.0, 80.0)
            values = 1.0 / (1.0 + np.exp(-clipped_logits))
        elif self._output_activation not in {"identity", "probability", "none", ""}:
            logger.warning(
                "Unknown output_activation %r for %s; treating output as probabilities.",
                self._output_activation,
                self.model_name,
            )

        if np.any(invalid_values):
            values = np.where(invalid_values, 0.0, values)

        out_of_range = (values < -1e-6) | (values > 1.0 + 1e-6)
        if np.any(out_of_range):
            logger.warning(
                "Tagger output for %s contained %d score(s) outside [0, 1]; "
                "ignoring them so thresholds do not accept invalid logits.",
                self.model_name,
                int(np.count_nonzero(out_of_range)),
            )
            values = np.where(out_of_range, 0.0, values)

        return np.clip(values, 0.0, 1.0)

    def _process_probs(
        self,
        probs: np.ndarray,
        threshold: Optional[float] = None,
        character_threshold: Optional[float] = None,
        copyright_threshold: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Convert raw model scores into the public result payload."""
        general_thresh = threshold if threshold is not None else self.threshold
        char_thresh = (
            character_threshold
            if character_threshold is not None
            else self.character_threshold
        )
        copyright_thresh = (
            copyright_threshold if copyright_threshold is not None else general_thresh
        )
        probs = self._normalize_output_probs(probs)
        result = self._build_empty_result()

        # BE-1: alongside the thresholded verdicts, collect EVERY score >= the
        # configured floor so the tag_scores table can serve virtual
        # re-threshold and coverage-gap queries with zero re-inference.
        # Read at call time (not import time) so tests and settings changes
        # take effect without a process restart.
        collect_scores = bool(config.TAG_SCORES_ENABLED)
        score_floor = float(config.TAG_SCORES_FLOOR)
        raw_scores: List[Dict[str, Any]] = []

        for tag_id, tag_name in self.general_tags:
            if tag_id < len(probs):
                conf = float(probs[tag_id])
                if collect_scores and conf >= score_floor:
                    raw_scores.append(
                        {
                            "tag": tag_name,
                            "score": conf,
                            "category": self._general_category_overrides.get(
                                tag_name, "general"
                            ),
                        }
                    )
                if conf >= general_thresh:
                    category = self._general_category_overrides.get(tag_name, "general")
                    result["general_tags"].append(
                        {"tag": tag_name, "confidence": conf, "category": category}
                    )
                    result["all_tags"].append(
                        {"tag": tag_name, "confidence": conf, "category": category}
                    )

        for tag_id, tag_name in self.copyright_tags:
            if tag_id < len(probs):
                conf = float(probs[tag_id])
                if collect_scores and conf >= score_floor:
                    raw_scores.append(
                        {"tag": tag_name, "score": conf, "category": "copyright"}
                    )
                if conf >= copyright_thresh:
                    result["copyright_tags"].append(
                        {"tag": tag_name, "confidence": conf, "category": "copyright"}
                    )
                    result["all_tags"].append(
                        {"tag": tag_name, "confidence": conf, "category": "copyright"}
                    )

        for tag_id, tag_name in self.character_tags:
            if tag_id < len(probs):
                conf = float(probs[tag_id])
                if collect_scores and conf >= score_floor:
                    raw_scores.append(
                        {"tag": tag_name, "score": conf, "category": "character"}
                    )
                if conf >= char_thresh:
                    result["character_tags"].append(
                        {"tag": tag_name, "confidence": conf, "category": "character"}
                    )
                    result["all_tags"].append(
                        {"tag": tag_name, "confidence": conf, "category": "character"}
                    )

        rating_probs = []
        for tag_id, tag_name in self.rating_tags:
            if tag_id < len(probs):
                conf = float(probs[tag_id])
                rating_probs.append((tag_name, conf))
                result["rating_confidences"][tag_name] = conf
                if collect_scores and conf >= score_floor:
                    raw_scores.append(
                        {"tag": tag_name, "score": conf, "category": "rating"}
                    )

        if rating_probs:
            best_rating = max(rating_probs, key=lambda x: x[1])
            result["rating"] = best_rating[0]
            result["all_tags"].append(
                {
                    "tag": best_rating[0],
                    "confidence": best_rating[1],
                    "category": "rating",
                }
            )
        elif self._rating_fallback_mode == "derive_from_tags":
            result["rating"] = self._derive_fallback_rating(result)
            if result["rating"] != "unknown":
                result["rating_confidences"][result["rating"]] = 1.0
                result["all_tags"].append(
                    {"tag": result["rating"], "confidence": 1.0, "category": "rating"}
                )
                if collect_scores:
                    raw_scores.append(
                        {"tag": result["rating"], "score": 1.0, "category": "rating"}
                    )

        if collect_scores:
            result["tag_scores"] = raw_scores

        result["general_tags"].sort(key=lambda x: x["confidence"], reverse=True)
        result["copyright_tags"].sort(key=lambda x: x["confidence"], reverse=True)
        result["character_tags"].sort(key=lambda x: x["confidence"], reverse=True)
        result["all_tags"].sort(key=lambda x: x["confidence"], reverse=True)
        return result

    def _derive_fallback_rating(self, result: Dict[str, Any]) -> str:
        """Infer a usable rating when the model package does not provide a rating head."""
        general_tag_names = {
            str(item.get("tag", "")).strip().lower()
            for item in result.get("general_tags", [])
            if item.get("tag")
        }

        explicit_markers = {
            "sex",
            "vaginal",
            "penis",
            "pussy",
            "anus",
            "nipples",
            "nude",
            "completely_nude",
            "uncensored",
            "cum",
            "fellatio",
            "masturbation",
            "breasts_out",
            "topless",
            "no_panties",
            "pantyshot",
            "pubic_hair",
        }
        questionable_markers = {
            "lingerie",
            "underwear",
            "panties",
            "bra",
            "cameltoe",
            "cleavage",
            "see-through",
            "wet",
            "swimsuit",
            "bikini",
            "navel",
            "thighhighs",
            "garter_straps",
            "bondage",
            "bdsm",
        }
        sensitive_markers = {
            "midriff",
            "bare_shoulders",
            "stomach",
            "armpits",
            "short_shorts",
            "miniskirt",
            "crop_top",
            "tube_top",
        }

        if general_tag_names & explicit_markers:
            return "explicit"
        if general_tag_names & questionable_markers:
            return "questionable"
        if general_tag_names & sensitive_markers:
            return "sensitive"
        if general_tag_names:
            return "general"
        return "unknown"
