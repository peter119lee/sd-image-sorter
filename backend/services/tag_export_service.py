"""
Shared export helpers for prompt/tag/caption sidecar files.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

from fastapi import HTTPException

import database as db
from services.export_validation import ExportValidator
from utils.path_validation import normalize_user_path, sanitize_filename, validate_folder_path



# ---------------------------------------------------------------------------
# Decomposition (2026-07): every constant and helper below lives in the
# services/tag_export/ package (selection / captions / sidecars / preview),
# re-imported here BY REFERENCE. THIS module remains a real FILE and the
# single import/monkeypatch surface (tests/test_tag_export_pins.py):
#   * The header import block above is kept verbatim (per-file F401 ignore in
#     pyproject.toml) so every historical module attribute keeps resolving
#     here — ``db`` in particular: tests patch tag_export_service.db.<fn>
#     (tests/test_resource_safety.py) and the patch lands on the SHARED
#     database module object every submodule also binds via
#     ``import database as db``.
#   * The identity-shared objects (NL_COMPOSE_MODES / VALID_CONTENT_MODES /
#     VALID_OUTPUT_MODES, and the callables build_sidecar_content /
#     compose_caption_with_nl / apply_caption_transforms) are defined ONCE in
#     their origin submodule and re-bound here so
#     services.dataset_export_service and the services/dataset_export/
#     submodules keep resolving the SAME objects (`is`-identity pins).
#   * ``count_selection_token_ids`` stays a plain, settable module attribute:
#     tests/test_routers/test_vlm.py monkeypatches it on THIS module and
#     routers/vlm lazy-imports it from here at call time.
# ---------------------------------------------------------------------------
from services.tag_export.selection import (
    EXPORT_DB_CHUNK_SIZE,
    PROMPT_MATCH_MODE_CONTAINS,
    PROMPT_MATCH_MODE_EXACT,
    _decode_selection_token,
    _iter_decoded_filter_id_chunks,
    _iter_id_list_chunks,
    _normalize_export_image_ids,
    count_selection_token_ids,
    iter_selection_token_id_chunks,
)
from services.tag_export.captions import (
    DANBOORU_TAG_CONTENT_MODES,
    LORA_PRESERVE_UNDERSCORE_PREFIXES,
    NL_COMPOSE_MODES,
    PARAMETER_EXPORT_ORDER,
    VALID_CONTENT_MODES,
    _build_nl_sidecar_content,
    _coerce_int_str_map,
    _coerce_transform_token_list,
    _compose_nl_for_image,
    _filter_tags,
    _filter_text_caption_tokens,
    _image_nl_source_text,
    _join_caption_parts,
    _maybe_normalize_underscores,
    _merge_template_blacklist_options,
    _normalize_caption_transform_token,
    _resolve_underscore_normalization,
    _split_caption_transform_tokens,
    apply_caption_transforms,
    build_a1111_parameters_text,
    build_sidecar_content,
    compose_caption_with_nl,
    extract_generation_params,
)
from services.tag_export.sidecars import (
    COMBINED_EXPORT_RECENT_ERROR_LIMIT,
    VALID_OUTPUT_MODES,
    VALID_OVERWRITE_POLICIES,
    _SidecarAllocation,
    _allocate_output_path,
    _get_combined_export_dir,
    _sanitized_fallback_stem,
    _sidecar_extension,
    _unique_collision_message,
    combined_export_path,
    export_tags_batch_request,
    export_tags_combined_request,
)
from services.tag_export.preview import render_export_preview
