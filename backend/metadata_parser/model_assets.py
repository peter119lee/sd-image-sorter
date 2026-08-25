# =============================================================================
# metadata_parser.model_assets - metadata_parser decomposition stages 1+2 (2026-07-13).
# Extracted VERBATIM from backend/metadata_parser.py @ c06d374 (4,912 lines).
# Source line ranges (original file): 965-1268.
# Mixin: Model-asset identity, merge, and normalization helpers.
# self.* calls and class-constant lookups resolve via MRO exactly as before.
# Patched seams (Image / open / _MAX_* / _sidecar_directory_cache): the readers
# live in metadata_parser/_runtime.py behind the package get/set proxy in
# __init__.py (stage 3); see tests/test_metadata_parser_pins.py.
import re
from typing import Optional, Dict, Any, Tuple, List, Set

class ModelAssetsMixin:
    """Model-asset identity, merge, and normalization helpers."""

    def _extract_metadata_model_identifier(self, metadata: dict) -> Optional[str]:
        """Extract the best available model identifier from raw metadata."""
        software = str(metadata.get("Software", "") or "").strip().lower()

        for key in ("Source", "source", "Model", "model"):
            value = metadata.get(key)
            if not isinstance(value, str):
                continue

            text = value.strip().strip("\0 ")
            if not text:
                continue

            lower = text.lower()
            if self._looks_like_model_filename(text):
                return text
            if any(token in lower for token in ("novelai diffusion", "stable diffusion", "nai-diffusion")):
                return text
            if "novelai" in software and re.match(r"^(sdxl|nai|stable diffusion)\b", text, flags=re.IGNORECASE):
                return text

        return None

    def _parse_explicit_saved_metadata(self, metadata: dict) -> Optional[Dict[str, Any]]:
        """Parse simple text metadata fields written by the Reader metadata editor."""
        prompt = self._flatten_text_value(metadata.get("prompt"))
        negative_prompt = self._flatten_text_value(
            metadata.get("negative_prompt") if "negative_prompt" in metadata else metadata.get("negative prompt")
        )
        checkpoint = (
            self._flatten_text_value(metadata.get("model"))
            or self._flatten_text_value(metadata.get("checkpoint"))
            or self._extract_metadata_model_identifier(metadata)
        )

        loras: List[str] = []
        lora_value = metadata.get("loras") or metadata.get("LoRAs") or metadata.get("lora")
        if isinstance(lora_value, (list, tuple, set)):
            loras = [str(item).strip() for item in lora_value if str(item).strip()]
        elif lora_value is not None:
            loras = [
                part.strip() for part in re.split(r"[,\n]", str(lora_value))
                if str(part).strip()
            ]

        generation_params: Dict[str, Any] = {}
        if "seed" in metadata:
            try:
                generation_params["seed"] = int(str(metadata["seed"]).strip())
            except (TypeError, ValueError):
                generation_params["seed"] = metadata["seed"]
        if "steps" in metadata:
            try:
                generation_params["steps"] = int(str(metadata["steps"]).strip())
            except (TypeError, ValueError):
                generation_params["steps"] = metadata["steps"]
        if "sampler" in metadata:
            generation_params["sampler"] = metadata["sampler"]
        if "cfg_scale" in metadata or "cfg scale" in metadata:
            cfg_value = metadata.get("cfg_scale", metadata.get("cfg scale"))
            try:
                generation_params["cfg_scale"] = float(str(cfg_value).strip())
            except (TypeError, ValueError):
                generation_params["cfg_scale"] = cfg_value
        if "size" in metadata:
            generation_params["size"] = metadata["size"]
        if checkpoint:
            generation_params["model"] = checkpoint
        if loras:
            generation_params["loras"] = ", ".join(loras)

        if not any((prompt, negative_prompt, checkpoint, loras, generation_params)):
            return None

        return {
            "generator": "unknown",
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "checkpoint": checkpoint,
            "loras": self._normalize_lora_names(loras),
            "generation_params": generation_params or None,
        }

    @staticmethod
    def _dedupe_non_empty_strings(values: List[Any]) -> List[str]:
        """Deduplicate string values while preserving order."""
        result: List[str] = []
        seen: Set[str] = set()

        for value in values:
            text = str(value or "").strip()
            if not text or text.lower() in {"none", "null", "false"} or text in seen:
                continue
            seen.add(text)
            result.append(text)

        return result

    @staticmethod
    def _asset_alias_key(value: str) -> str:
        """Normalize a model path/tag into a comparison-friendly alias key."""
        text = re.sub(r"[\\/]+", "/", str(value or "").strip().strip('"').strip("'"))
        if not text:
            return ""

        leaf = text.split("/")[-1]
        stem = leaf.rsplit(".", 1)[0] if "." in leaf else leaf
        return stem.lower()

    def _normalize_lora_names(self, names: List[str]) -> List[str]:
        """Prefer explicit filenames over bare inline aliases when both exist."""
        unique_names = self._dedupe_non_empty_strings(names)
        file_backed = [name for name in unique_names if self._looks_like_model_filename(name)]
        alias_covered = {
            self._asset_alias_key(name)
            for name in file_backed
            if self._asset_alias_key(name)
        }

        result: List[str] = []
        seen: Set[str] = set()
        for name in [*file_backed, *[item for item in unique_names if item not in file_backed]]:
            if not self._looks_like_model_filename(name):
                alias_key = self._asset_alias_key(name)
                if alias_key and alias_key in alias_covered:
                    continue
            if name in seen:
                continue
            seen.add(name)
            result.append(name)

        return result

    @staticmethod
    def _model_candidate_identity(candidate: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
        """Stable dedupe key for model candidate records."""
        return (
            str(candidate.get("name", "")).strip(),
            str(candidate.get("node_id", "")).strip(),
            str(candidate.get("class_type", "")).strip(),
            str(candidate.get("input_key", "")).strip(),
            str(candidate.get("key_path", "")).strip(),
        )

    def _merge_candidate_records(self, *candidate_lists: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Merge candidate records while preserving first-seen ordering."""
        merged: List[Dict[str, Any]] = []
        seen: Set[Tuple[str, str, str, str, str]] = set()

        for candidate_list in candidate_lists:
            if not isinstance(candidate_list, list):
                continue
            for candidate in candidate_list:
                if not isinstance(candidate, dict):
                    continue
                identity = self._model_candidate_identity(candidate)
                if identity[0] == "" or identity in seen:
                    continue
                seen.add(identity)
                merged.append(dict(candidate))

        return merged

    def _build_explicit_model_assets(
        self,
        source: str,
        checkpoint: Optional[str] = None,
        loras: Optional[List[str]] = None,
        unets: Optional[List[str]] = None,
        diffusion_models: Optional[List[str]] = None,
        yolo_models: Optional[List[str]] = None,
        model_names: Optional[List[str]] = None,
        confidence: str = "high",
    ) -> Optional[Dict[str, Any]]:
        """Build a normalized model_assets payload from explicit metadata fields."""
        checkpoint_names = self._dedupe_non_empty_strings([checkpoint] if checkpoint else [])
        unet_names = self._dedupe_non_empty_strings(unets or [])
        diffusion_names = self._dedupe_non_empty_strings(diffusion_models or [])
        generic_model_names = self._dedupe_non_empty_strings(model_names or [])
        lora_names = self._normalize_lora_names(loras or [])
        yolo_names = self._dedupe_non_empty_strings(yolo_models or [])

        if not any((checkpoint_names, unet_names, diffusion_names, generic_model_names, lora_names, yolo_names)):
            return None

        def make_candidates(asset_type: str, names: List[str]) -> List[Dict[str, Any]]:
            return [
                {
                    "name": name,
                    "asset_type": asset_type,
                    "source_mode": source,
                    "confidence": confidence,
                    "match_type": "explicit_metadata",
                }
                for name in names
            ]

        primary_model_type = None
        primary_model_name = None
        for asset_type, names in (
            ("checkpoint", checkpoint_names),
            ("unet", unet_names),
            ("diffusion_model", diffusion_names),
            ("model", generic_model_names),
        ):
            if names:
                primary_model_type = asset_type
                primary_model_name = names[0]
                break

        return {
            "source": source,
            "primary_model_type": primary_model_type,
            "primary_model_name": primary_model_name,
            "checkpoint_candidates": make_candidates("checkpoint", checkpoint_names),
            "unet_candidates": make_candidates("unet", unet_names),
            "diffusion_model_candidates": make_candidates("diffusion_model", diffusion_names),
            "model_candidates": make_candidates("model", generic_model_names),
            "lora_candidates": make_candidates("lora", lora_names),
            "yolo_candidates": make_candidates("yolo", yolo_names),
            "loras": lora_names,
            "yolo_models": yolo_names,
        }

    def _merge_model_assets(self, primary: Optional[Dict[str, Any]], secondary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Merge two normalized model_assets payloads."""
        if not primary:
            return dict(secondary) if secondary else None
        if not secondary:
            return primary

        merged: Dict[str, Any] = dict(primary)

        if not merged.get("primary_model_name") and secondary.get("primary_model_name"):
            merged["primary_model_name"] = secondary.get("primary_model_name")
            merged["primary_model_type"] = secondary.get("primary_model_type")

        primary_source = str(primary.get("source", "")).strip()
        secondary_source = str(secondary.get("source", "")).strip()
        if primary_source and secondary_source and primary_source != secondary_source:
            merged["sources"] = self._dedupe_non_empty_strings([
                *(primary.get("sources") or [primary_source]),
                *(secondary.get("sources") or [secondary_source]),
            ])

        candidate_keys = {
            "checkpoint_candidates",
            "unet_candidates",
            "diffusion_model_candidates",
            "model_candidates",
            "lora_candidates",
            "yolo_candidates",
            "workflow_widget_lora_candidates",
            "global_lora_candidates",
            "global_yolo_candidates",
        }
        for key in candidate_keys:
            merged_list = self._merge_candidate_records(primary.get(key), secondary.get(key))
            if merged_list:
                merged[key] = merged_list

        merged["loras"] = self._normalize_lora_names([
            *(primary.get("loras") or []),
            *(secondary.get("loras") or []),
        ])
        merged["yolo_models"] = self._dedupe_non_empty_strings([
            *(primary.get("yolo_models") or []),
            *(secondary.get("yolo_models") or []),
        ])
        merged["activity_root_ids"] = self._dedupe_non_empty_strings([
            *(primary.get("activity_root_ids") or []),
            *(secondary.get("activity_root_ids") or []),
        ])

        primary_count = primary.get("activity_node_count")
        secondary_count = secondary.get("activity_node_count")
        if isinstance(primary_count, int) or isinstance(secondary_count, int):
            merged["activity_node_count"] = max(
                int(primary_count or 0),
                int(secondary_count or 0),
            )

        return merged

    def _looks_like_yolo_model_name(self, value: str, class_type: str = "", key_path: str = "") -> bool:
        """Detect Ultralytics/YOLO-style detector models without confusing them with checkpoints."""
        if not self._looks_like_model_filename(value):
            return False

        combined = " ".join([
            str(class_type or "").lower(),
            str(key_path or "").lower(),
            str(value or "").lower(),
        ])
        return any(token in combined for token in (
            "ultralytics",
            "yolo",
            "detector",
            "bbox/",
            "segm",
            "detailer",
            "adetailer",
        ))

