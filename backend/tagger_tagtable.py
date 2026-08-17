"""Model/tag-table path-resolution mixin for WD14Tagger (split 2026-07).

Methods moved VERBATIM from tagger.py: _validate_model_file / _get_model_paths / _load_tags. Zero manifested
lines. MODELS is origin-imported from config (the same dict object the
facade re-exports; no suite patches ``tagger.MODELS``), and
self._download_model resolves through the class MRO to the download mixin.
The logger keeps the original "tagger" channel.
"""

import csv
import json
import logging
import os
from typing import Tuple

from config import TAGGER_MODELS as MODELS

logger = logging.getLogger("tagger")


class _TagTableMixin:
    """Custom-path hard contract, ONNX file validation, WD14/camie tag tables."""

    def _validate_model_file(self, model_path: str) -> bool:
        """
        Validate that an ONNX model file is not corrupted.
        Returns True if valid, False if corrupted or invalid.
        """
        if not os.path.exists(model_path):
            return False

        # Check file size - ONNX models should be at least 1MB
        try:
            file_size = os.path.getsize(model_path)
            if file_size < 1024 * 1024:  # Less than 1MB is suspicious
                logger.warning(
                    f"Model file {model_path} is suspiciously small ({file_size} bytes)"
                )
                return False
        except OSError:
            return False

        # Try to read the file header to verify it's a valid ONNX file
        try:
            with open(model_path, "rb") as f:
                header = f.read(4)
                # ONNX files start with specific protobuf bytes
                if len(header) < 4:
                    return False
        except Exception as e:
            logger.error(f"Error reading model file header: {e}")
            return False

        return True

    def _get_model_paths(self) -> Tuple[str, str]:
        """Get model and tags file paths."""
        # If direct paths are provided, use them. Explicit local paths are hard contracts:
        # a typo must fail loudly, not fall back to downloading or auto-discovery.
        if self.model_path:
            if not os.path.exists(self.model_path):
                raise FileNotFoundError(
                    f"Custom ONNX model file not found: {self.model_path}"
                )

            model_config = MODELS.get(self.model_name, {})
            metadata_format = str(
                model_config.get("metadata_format", "wd14_csv")
            ).lower()
            allowed_tag_exts = {".json"} if metadata_format == "camie_v2" else {".csv"}
            if self.tags_path:
                if not os.path.exists(self.tags_path):
                    raise FileNotFoundError(
                        f"Custom tags/metadata file not found: {self.tags_path}"
                    )
                tags_ext = os.path.splitext(self.tags_path)[1].lower()
                if tags_ext not in allowed_tag_exts:
                    allowed_text = " or ".join(sorted(allowed_tag_exts))
                    raise ValueError(
                        f"Tags/metadata file for {self.model_name} must be {allowed_text}."
                    )
                return self.model_path, self.tags_path
            # Try to find the profile-specific tags/metadata file next to the model.
            model_dir = os.path.dirname(self.model_path)
            configured_tags_file = str(model_config.get("tags_file") or "").strip()
            candidate_names = []
            if configured_tags_file:
                candidate_names.append(configured_tags_file)
            if metadata_format == "camie_v2":
                candidate_names.extend(
                    ["camie-tagger-v2-metadata.json", "metadata.json"]
                )
            else:
                candidate_names.append("selected_tags.csv")

            possible_tags = []
            seen_tags = set()
            for candidate_name in candidate_names:
                if not candidate_name:
                    continue
                if os.path.splitext(candidate_name)[1].lower() not in allowed_tag_exts:
                    continue
                for candidate_path in [
                    os.path.join(model_dir, candidate_name),
                    os.path.join(model_dir, "..", candidate_name),
                ]:
                    normalized_candidate = os.path.normpath(candidate_path)
                    if normalized_candidate in seen_tags:
                        continue
                    seen_tags.add(normalized_candidate)
                    possible_tags.append(normalized_candidate)
            for tags_path in possible_tags:
                if os.path.exists(tags_path):
                    return self.model_path, tags_path
            expected = " or ".join(candidate_names) or "a supported tags/metadata file"
            raise ValueError(
                f"Tags/metadata file not found for {self.model_name}. Expected {expected}. "
                "Please provide tags_path for custom model."
            )

        # Otherwise, download from HuggingFace
        return self._download_model()

    def _load_tags(self, tags_path: str):
        """Load tag metadata for classic WD CSV files, PixAI CSV exports, or Camie JSON metadata."""
        self.tags = []
        self.general_tags = []
        self.copyright_tags = []
        self.character_tags = []
        self.rating_tags = []
        self.rating_indices = {}
        # tag name -> true category for tags that live in the general bucket
        # but aren't 'general' (camie's artist/meta/year entries).
        self._general_category_overrides = {}

        if self._metadata_format == "camie_v2" or tags_path.lower().endswith(".json"):
            with open(tags_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
            dataset_info = metadata.get("dataset_info", {})
            tag_mapping = dataset_info.get("tag_mapping", {})
            idx_to_tag = tag_mapping.get("idx_to_tag", {})
            tag_to_category = tag_mapping.get("tag_to_category", {})

            def normalize_rating_name(name: str) -> str:
                lowered = str(name or "").strip().lower()
                if lowered.startswith("rating_"):
                    lowered = lowered.split("rating_", 1)[1]
                return lowered

            for index_key, tag_name in idx_to_tag.items():
                try:
                    tag_idx = int(index_key)
                except (TypeError, ValueError):
                    continue
                category = str(tag_to_category.get(tag_name, "general")).strip().lower()
                self.tags.append(tag_name)
                if category == "copyright":
                    self.copyright_tags.append((tag_idx, tag_name))
                elif category in {"general", "meta", "year", "artist"}:
                    # Bucket membership stays 'general' (downstream pipelines
                    # read the three classic buckets), but remember the true
                    # category so persisted rows can carry it (P3-11: the
                    # export engine's {artists} section reads tags.category).
                    self.general_tags.append((tag_idx, tag_name))
                    if category != "general":
                        self._general_category_overrides[tag_name] = category
                elif category == "character":
                    self.character_tags.append((tag_idx, tag_name))
                elif category == "rating":
                    normalized = normalize_rating_name(tag_name)
                    self.rating_tags.append((tag_idx, normalized))
                    self.rating_indices[normalized] = tag_idx
            return

        with open(tags_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        if not rows:
            return

        header = [str(part or "").strip().lower() for part in rows[0]]
        has_named_header = "name" in header and "category" in header
        name_index = header.index("name") if "name" in header else 1
        category_index = header.index("category") if "category" in header else 2
        data_rows = rows[1:] if has_named_header else rows

        for row_idx, parts in enumerate(data_rows):
            if not parts or len(parts) <= max(name_index, category_index):
                continue
            tag_name = parts[name_index]
            try:
                category = int(parts[category_index])
            except ValueError:
                continue
            self.tags.append(tag_name)
            if category == 0:
                self.general_tags.append((row_idx, tag_name))
            elif category == 3:
                self.copyright_tags.append((row_idx, tag_name))
            elif category == 4:
                self.character_tags.append((row_idx, tag_name))
            elif category == 9:
                self.rating_tags.append((row_idx, tag_name))
                self.rating_indices[tag_name] = row_idx
