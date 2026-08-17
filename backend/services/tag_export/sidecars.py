"""Sidecar allocation + batch/combined export writers (split from services/tag_export_service.py).

Moved verbatim except the ONE
documented anchor recompute in ``_get_combined_export_dir`` (see the
function body). Import through services.tag_export_service — the facade
is the monkeypatch surface.

Seams kept verbatim:
  * ``import database as db`` + ``db.<fn>()`` call style — callers patch
    tag_export_service.db.<fn> (tests/test_resource_safety.py), which
    mutates the SHARED database module object, so the patch is visible
    here too. Never switch to ``from database import <fn>``.
  * ``_allocate_output_path`` MUTATES its ``image`` argument (pops the
    ``_sidecar_stem_override`` that export_tags_batch_request seeds per
    row) — do not "fix" this to an immutable copy.
  * The identity-shared objects are origin-imported from
    services.tag_export.captions by reference (never re-declared).
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from fastapi import HTTPException

import database as db
from services.export_validation import ExportValidator
from services.tag_export.captions import (
    NL_COMPOSE_MODES,
    VALID_CONTENT_MODES,
    _build_nl_sidecar_content,
    _coerce_int_str_map,
    _compose_nl_for_image,
    _image_nl_source_text,
    apply_caption_transforms,
    build_sidecar_content,
)
from services.tag_export.selection import (
    EXPORT_DB_CHUNK_SIZE,
    _iter_id_list_chunks,
    _normalize_export_image_ids,
)
from utils.path_validation import normalize_user_path, sanitize_filename, validate_folder_path


VALID_OVERWRITE_POLICIES = {"unique", "overwrite", "skip"}
# ``folder``       — write all sidecars into the user-supplied ``output_folder``
#                    (legacy default; flat output regardless of source layout).
# ``beside_image`` — write each sidecar to the directory of its source image,
#                    so a library spread across many subfolders keeps its
#                    structure intact and per-image training tools that look
#                    for ``foo.png`` + ``foo.txt`` in the same directory keep
#                    working without extra plumbing.
VALID_OUTPUT_MODES = {"folder", "beside_image"}
COMBINED_EXPORT_RECENT_ERROR_LIMIT = 20


def _sidecar_extension(content_mode: str) -> str:
    return ".json" if str(content_mode or "").lower() == "json" else ".txt"


def _sanitized_fallback_stem(image: Dict[str, Any]) -> str:
    """Last-resort sidecar stem when the image has no on-disk path.

    Used only for orphaned DB rows (missing-file records, broken paths).
    The normal export path uses the actual on-disk filename so the
    sidecar can pair with the image by exact basename match.
    """
    raw = str(image.get("filename") or f"image_{image.get('id') or 'unknown'}")
    sanitized = sanitize_filename(raw)
    return os.path.splitext(sanitized)[0] or "unnamed"


@dataclass(frozen=True)
class _SidecarAllocation:
    """Outcome of resolving one image's caption sidecar destination.

    ``outcome`` is one of:
      - ``"write"``  — write the caption to ``path``.
      - ``"skip"``   — write nothing; count toward ``skipped`` (an existing
                       sidecar is intentionally left in place).
      - ``"error"``  — write nothing; count toward ``error_count`` and surface
                       ``message`` (a name clash that renaming would only paper
                       over by breaking image/caption pairing).
    """

    outcome: str
    path: Optional[str] = None      # set iff outcome == "write"
    message: Optional[str] = None   # set iff outcome == "error"


def _unique_collision_message(sidecar_name: str, taken_by: str) -> str:
    """Per-image error text for a ``unique``-policy sidecar name clash."""
    return (
        f"Sidecar name '{sidecar_name}' already taken (by {taken_by}); "
        "rename the image, or use overwrite/skip."
    )


def _in_run_collision_message(sidecar_name: str, taken_by: str) -> str:
    """Per-image error text when two batch rows resolve to one sidecar."""
    return (
        f"Sidecar name '{sidecar_name}' already taken (by {taken_by}); "
        "two source images use the same stem, so rename one source image."
    )


def _output_path_key(path: str) -> str:
    """Return the canonical path key for one sidecar destination."""
    return os.path.realpath(os.path.abspath(path))


def _output_path_keys(path: str) -> tuple[str, ...]:
    """Return path and existing-file identity keys for one destination."""
    path_key = _output_path_key(path)
    try:
        stat_result = os.stat(path)
    except FileNotFoundError:
        return (path_key,)
    file_key = f"file:{stat_result.st_dev}:{stat_result.st_ino}"
    return (path_key, file_key)


def _find_output_owner(
    path: str, used_output_paths: Dict[str, str]
) -> tuple[bool, str]:
    """Find the first batch owner of any equivalent output destination."""
    for key in _output_path_keys(path):
        if key in used_output_paths:
            return True, used_output_paths[key]
    return False, ""


def _output_path_claims(path: str, owner: str) -> Dict[str, str]:
    """Build the occupancy entries for one written sidecar."""
    return {key: owner for key in _output_path_keys(path)}


def _output_paths_overlap(first_path: str, second_path: str) -> bool:
    """Return whether two destinations resolve to one path or existing file."""
    return bool(set(_output_path_keys(first_path)) & set(_output_path_keys(second_path)))


def _in_run_nl_collision_message(sidecar_name: str, taken_by: str) -> str:
    """Per-image error text when an NL twin targets an earlier output."""
    return (
        f"NL sidecar name '{sidecar_name}' is already used by another image's "
        f"sidecar (first owner: {taken_by}); rename one source image."
    )


def _allocate_output_path(
    output_folder: str,
    image: Dict[str, Any],
    content_mode: str,
    overwrite_policy: str,
    used_output_paths: Dict[str, str],
    output_mode: str = "folder",
) -> _SidecarAllocation:
    """Resolve where (or whether) one image's caption sidecar is written.

    The sidecar stem is pinned to the image's on-disk stem so the caption pairs
    with the image by exact basename — the invariant LoRA trainers rely on.
    Returns a :class:`_SidecarAllocation`:

    - ``write`` + ``path``: write the caption there.
    - ``skip``: write nothing, keeping an existing sidecar — ``skip`` policy
      with the name already present, or ``unique`` policy in ``beside_image``
      mode where a caption already sits next to the image ("already exported").
    - ``error`` + ``message``: a name clash that must not be worked around.
      Renaming to ``{stem}_1{ext}`` would produce a caption that pairs with no
      image, so the clash is reported instead. This applies whenever an earlier
      image in the same run already claimed the name, and to pre-existing files
      under ``unique`` policy in ``folder`` mode.

    ``overwrite`` replaces files that existed before this export, but it does
    not let a later row overwrite or rename an earlier row's sidecar.
    ``used_output_paths`` maps canonical path and existing-file identity keys
    to the source image path that claimed them. This catches case-equivalent
    paths on case-insensitive volumes plus symlink, junction, and hard-link
    aliases without collapsing distinct names on case-sensitive volumes.
    """
    extension = _sidecar_extension(content_mode)
    # v3.2.2: derive the sidecar stem from the actual on-disk image
    # filename rather than ``sanitize_filename(image["filename"])``.
    #
    # The DB-stored ``filename`` field gets routed through
    # ``sanitize_filename`` here, which replaces apostrophes, parentheses,
    # commas, brackets, and other "non-word" characters with underscores
    # ("my (test).png" -> "my _test_.png"). For LoRA training that pairs
    # captions with images by exact basename match, this is fatal: the
    # caption file ends up named ``my _test_.txt`` while the image keeps
    # its original "my (test).png", and the trainer skips both.
    #
    # The image already exists on disk, so its filename is by definition
    # OS-legal; we don't need to sanitize. The ``beside_image`` branch
    # already does this via ``_sidecar_stem_override``; this aligns the
    # ``folder`` branch with that pattern. ``sanitize_filename`` remains
    # the fallback when the DB has no on-disk path (orphaned records,
    # missing-file rows, etc).
    stem_override = image.pop("_sidecar_stem_override", None)
    if stem_override:
        basename = stem_override
    else:
        on_disk_path = str(image.get("path") or "").strip()
        if on_disk_path:
            on_disk_basename = os.path.basename(on_disk_path)
            on_disk_stem = os.path.splitext(on_disk_basename)[0]
            basename = on_disk_stem if on_disk_stem else _sanitized_fallback_stem(image)
        else:
            basename = _sanitized_fallback_stem(image)
    if not basename:
        basename = f"image_{image.get('id') or 'unknown'}"

    sidecar_name = f"{basename}{extension}"
    primary_path = os.path.join(output_folder, sidecar_name)
    in_run_taken, taken_by = _find_output_owner(primary_path, used_output_paths)

    if overwrite_policy == "overwrite":
        # A file that predates this batch may be replaced in place. An earlier
        # row in the same batch owns its sidecar name, though: adding a numeric
        # suffix would leave that caption without a matching image.
        if not in_run_taken:
            return _SidecarAllocation("write", path=primary_path)
        taken_by = taken_by or "an earlier image in this export"
        return _SidecarAllocation(
            "error", message=_in_run_collision_message(sidecar_name, taken_by)
        )

    if overwrite_policy == "skip":
        # Leave any existing sidecar untouched — one on disk before the run, or
        # one written earlier in it. Only a free name is written.
        if os.path.exists(primary_path) or in_run_taken:
            return _SidecarAllocation("skip")
        return _SidecarAllocation("write", path=primary_path)

    # overwrite_policy == "unique": the sidecar stem is pinned to the image
    # stem so image/caption pairing always holds. We therefore never rename a
    # collision to ``{stem}_1{ext}`` — a renamed caption pairs with no image
    # (LoRA trainers match by exact basename), i.e. a silently broken training
    # sample. A taken name is reported so the user can rename the offending
    # image or switch to overwrite/skip.
    if in_run_taken:
        # An earlier image THIS run already claimed the name. In folder mode
        # that means two sources share a stem; in beside_image it can only mean
        # two DB rows point at one file. Either way two images want one caption
        # name — a real data-loss risk → error.
        taken_by = taken_by or "an earlier image in this export"
        return _SidecarAllocation(
            "error", message=_in_run_collision_message(sidecar_name, taken_by)
        )
    if os.path.exists(primary_path):
        # The name is taken by a file already on disk. In beside_image mode a
        # caption already sitting next to the image is the "already exported"
        # case → a benign skip. In folder mode it is a genuine clash to surface.
        if output_mode == "beside_image":
            return _SidecarAllocation("skip")
        return _SidecarAllocation(
            "error", message=_unique_collision_message(sidecar_name, "an existing file on disk")
        )
    return _SidecarAllocation("write", path=primary_path)


def export_tags_batch_request(
    request: Any,
    *,
    id_chunks: Optional[Iterable[List[int]]] = None,
    total: Optional[int] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Export selected image metadata to sidecar files.

    ``cancel_check`` (Debt-22 background job support): when supplied, it is
    polled at each chunk boundary. Returning ``True`` stops the export
    cooperatively and returns the partial result gathered so far. The single
    ``used_output_paths`` de-dup set is preserved because this stays one call.
    """
    output_mode = str(getattr(request, "output_mode", "folder") or "folder").strip().lower()
    if output_mode not in VALID_OUTPUT_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid output_mode: {output_mode}")

    # ``output_folder`` is only required for the legacy ``folder`` mode. In
    # ``beside_image`` mode we write each sidecar next to its source image, so
    # the field is ignored. Validating it would force the user to type a fake
    # path just to satisfy the schema.
    if output_mode == "folder":
        output_folder = normalize_user_path(str(request.output_folder or ""))
        is_valid, error = validate_folder_path(output_folder, allow_create=True)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error or "Invalid output folder")
        output_folder_ready = os.path.isdir(output_folder)
    else:
        output_folder = ""
        output_folder_ready = True  # nothing to create up front in beside_image mode

    blacklist = {str(tag or "").strip().lower() for tag in (request.blacklist or []) if str(tag or "").strip()}
    prefix = str(request.prefix or "")
    content_mode = str(getattr(request, "content_mode", "tags") or "tags").strip().lower()
    if content_mode not in VALID_CONTENT_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid content_mode: {content_mode}")
    overwrite_policy = str(getattr(request, "overwrite_policy", "unique") or "unique").strip().lower()
    if overwrite_policy not in VALID_OVERWRITE_POLICIES:
        raise HTTPException(status_code=400, detail=f"Invalid overwrite_policy: {overwrite_policy}")

    # v3.2.1: template_options for content_mode='template'
    template_options = getattr(request, "template_options", None)
    if template_options is not None and not isinstance(template_options, dict):
        # pydantic may pass a model — convert to dict
        if hasattr(template_options, "model_dump"):
            template_options = template_options.model_dump()
        else:
            template_options = None

    # P0-3: diffusion-pipe style split export — write each image's NL caption
    # to a ``{stem}{suffix}.txt`` twin beside the tag sidecar.
    nl_sidecar_enabled = bool(getattr(request, "nl_sidecar", False))
    nl_sidecar_suffix = str(getattr(request, "nl_sidecar_suffix", "_nl") or "_nl")
    if nl_sidecar_enabled and content_mode not in NL_COMPOSE_MODES:
        raise HTTPException(
            status_code=400,
            detail=(
                "nl_sidecar requires a tag-only content mode ('tags' or 'template'); "
                f"'{content_mode}' already carries the NL caption in the main file."
            ),
        )
    nl_sidecar_trigger = ""
    if nl_sidecar_enabled:
        if isinstance(template_options, dict):
            nl_sidecar_trigger = str(template_options.get("trigger") or "").strip()
        if not nl_sidecar_trigger:
            nl_sidecar_trigger = str(getattr(request, "prefix", "") or "").strip()

    # v3.2.1: image_overrides — per-image manually-edited caption that bypasses the engine
    image_overrides_raw = getattr(request, "image_overrides", None) or {}
    image_overrides: Dict[int, str] = {}
    if isinstance(image_overrides_raw, dict):
        for k, v in image_overrides_raw.items():
            try:
                image_overrides[int(k)] = str(v or "")
            except (TypeError, ValueError):
                continue

    # Aurora #25c: per-image caption type + edited NL sentence (caption editor).
    image_types_map = _coerce_int_str_map(getattr(request, "image_types", None))
    nl_overrides_map = _coerce_int_str_map(getattr(request, "image_nl_overrides", None))

    # v3.2.1 follow-up: LoRA-trainer underscore convention. None == follow
    # per-content-mode default. Explicit True / False is the user's
    # checkbox override from the export modal.
    normalize_tag_underscores_request = getattr(request, "normalize_tag_underscores", None)
    caption_transforms = getattr(request, "caption_transforms", None) or {}
    # P2-19 / P2-18 export-engine filters (both default off).
    training_purpose_request = str(getattr(request, "training_purpose", "") or "")
    dedupe_implications_request = bool(getattr(request, "dedupe_implications", False))

    exported = 0
    skipped = 0
    error_count = 0
    nl_sidecars_written = 0
    error_messages: List[str] = []
    # Maps each allocated sidecar path -> the source image path that claimed it,
    # so an in-run name clash can point at the first owner.
    used_output_paths: Dict[str, str] = {}
    validator = ExportValidator(
        content_mode=content_mode, template_options=template_options
    )

    if id_chunks is None:
        id_chunks = _iter_id_list_chunks(getattr(request, "image_ids", []) or [], EXPORT_DB_CHUNK_SIZE)
    total_count = int(total if total is not None else len(_normalize_export_image_ids(getattr(request, "image_ids", []) or [])))
    processed = 0

    for image_id_list in id_chunks:
        if cancel_check is not None and cancel_check():
            break
        images_map = db.get_images_by_ids(image_id_list)
        tags_map = db.get_image_tags_map(image_id_list)

        for image_id in image_id_list:
            processed += 1
            if progress_callback:
                progress_callback({"processed": processed, "total": total_count, "current_id": image_id})
            try:
                image = images_map.get(image_id)
                if not image:
                    error_count += 1
                    error_messages.append(f"Image {image_id} not found")
                    continue

                tags = tags_map.get(image_id, [])
                # v3.2.1: if user provided a manual override for this image, use it verbatim
                if image_id in image_overrides:
                    file_content = image_overrides[image_id]
                else:
                    file_content = build_sidecar_content(
                        image,
                        tags,
                        content_mode=content_mode,
                        blacklist=blacklist,
                        prefix=prefix,
                        template_options=template_options,
                        normalize_tag_underscores=normalize_tag_underscores_request,
                        training_purpose=training_purpose_request,
                        dedupe_implications=dedupe_implications_request,
                    )
                # Aurora #25c: fold in the per-image NL sentence BEFORE the
                # transforms, matching dataset_export_service's order
                # (override/render -> compose -> transforms).
                file_content = _compose_nl_for_image(
                    file_content,
                    image,
                    image_id,
                    content_mode=content_mode,
                    image_types=image_types_map,
                    nl_overrides=nl_overrides_map,
                )
                file_content = apply_caption_transforms(file_content, caption_transforms)
                # In ``beside_image`` mode each image lands in its own
                # source directory. We do NOT auto-create directories on
                # this path: if the source folder no longer exists (file
                # was moved/deleted out from under us), fail this row
                # with a clear error rather than silently materialising
                # an empty folder somewhere unexpected.
                if output_mode == "beside_image":
                    image_path = str(image.get("path") or "").strip()
                    if not image_path:
                        error_count += 1
                        error_messages.append(
                            f"Image {image_id} has no source path on record; "
                            "cannot write sidecar beside the image."
                        )
                        continue
                    image_dir = os.path.dirname(image_path)
                    if not image_dir or not os.path.isdir(image_dir):
                        error_count += 1
                        error_messages.append(
                            f"Source folder for image {image_id} not found "
                            f"({image_dir!r}); skipping sidecar."
                        )
                        continue
                    target_folder = image_dir
                    # Use the actual file's stem for the sidecar name so it
                    # always matches the image (critical for LoRA training).
                    actual_stem = os.path.splitext(os.path.basename(image_path))[0]
                    if actual_stem:
                        image["_sidecar_stem_override"] = actual_stem
                else:
                    target_folder = output_folder

                allocation = _allocate_output_path(
                    target_folder, image, content_mode, overwrite_policy,
                    used_output_paths, output_mode=output_mode,
                )
                if allocation.outcome == "skip":
                    skipped += 1
                    continue
                if allocation.outcome == "error":
                    error_count += 1
                    if len(error_messages) < 20:
                        error_messages.append(f"Image {image_id}: {allocation.message}")
                    elif len(error_messages) == 20:
                        error_messages.append("... and more errors (total: showing first 20)")
                    continue
                output_path = str(allocation.path)

                # P0-3: resolve the NL twin BEFORE writing the tag sidecar so a
                # unique-policy clash on the twin fails the row atomically —
                # never a tag file without its NL half.
                nl_twin_path: Optional[str] = None
                nl_twin_content = ""
                if nl_sidecar_enabled:
                    nl_twin_content = _build_nl_sidecar_content(
                        _image_nl_source_text(image, image_id, nl_overrides_map),
                        nl_sidecar_trigger,
                    )
                    if nl_twin_content:
                        stem_no_ext, sidecar_ext = os.path.splitext(output_path)
                        candidate = f"{stem_no_ext}{nl_sidecar_suffix}{sidecar_ext}"
                        in_run_taken, taken_by = _find_output_owner(
                            candidate, used_output_paths
                        )
                        overlaps_main = _output_paths_overlap(output_path, candidate)
                        if overlaps_main:
                            error_count += 1
                            if len(error_messages) < 20:
                                error_messages.append(
                                    f"Image {image_id}: main sidecar "
                                    f"'{os.path.basename(output_path)}' and NL sidecar "
                                    f"'{os.path.basename(candidate)}' resolve to the same file."
                                )
                            elif len(error_messages) == 20:
                                error_messages.append("... and more errors (total: showing first 20)")
                            continue
                        if in_run_taken and overwrite_policy != "skip":
                            taken_by = taken_by or "an earlier image in this export"
                            error_count += 1
                            if len(error_messages) < 20:
                                error_messages.append(
                                    f"Image {image_id}: "
                                    f"{_in_run_nl_collision_message(os.path.basename(candidate), taken_by)}"
                                )
                            elif len(error_messages) == 20:
                                error_messages.append("... and more errors (total: showing first 20)")
                            continue
                        twin_exists = os.path.exists(candidate)
                        if overwrite_policy == "unique" and twin_exists:
                            error_count += 1
                            if len(error_messages) < 20:
                                error_messages.append(
                                    f"Image {image_id}: NL sidecar name "
                                    f"'{os.path.basename(candidate)}' already taken; "
                                    "rename the image, or use overwrite/skip."
                                )
                            elif len(error_messages) == 20:
                                error_messages.append("... and more errors (total: showing first 20)")
                            continue
                        if overwrite_policy == "skip" and (in_run_taken or twin_exists):
                            nl_twin_path = None  # leave the existing twin in place
                        else:
                            nl_twin_path = candidate

                if output_mode == "folder" and not output_folder_ready:
                    try:
                        os.makedirs(output_folder, exist_ok=True)
                    except OSError as exc:
                        raise HTTPException(status_code=400, detail=f"Cannot create output folder: {exc}") from exc
                    output_folder_ready = True

                # newline="\n" (P3-14): keep sidecars LF on Windows too —
                # some trainer stacks treat a CRLF caption line as content.
                with open(output_path, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(file_content)

                output_owner = str(image.get("path") or image.get("filename") or "")
                used_output_paths.update(
                    _output_path_claims(output_path, output_owner)
                )
                exported += 1
                validator.add(
                    output_path=output_path,
                    content=file_content,
                    image_path=str(image.get("path") or ""),
                )

                if nl_twin_path:
                    with open(nl_twin_path, "w", encoding="utf-8", newline="\n") as handle:
                        handle.write(nl_twin_content)
                    used_output_paths.update(
                        _output_path_claims(nl_twin_path, output_owner)
                    )
                    nl_sidecars_written += 1
                    validator.add(
                        output_path=nl_twin_path,
                        content=nl_twin_content,
                        image_path=str(image.get("path") or ""),
                        pair_suffix=nl_sidecar_suffix,
                    )
            except HTTPException:
                raise
            except Exception as exc:
                error_count += 1
                if len(error_messages) < 20:
                    error_messages.append(f"Error exporting sidecar for image {image_id}: {exc}")
                elif len(error_messages) == 20:
                    error_messages.append("... and more errors (total: showing first 20)")

    return {
        "exported": exported,
        "skipped": skipped,
        "error_count": error_count,
        "error_messages": error_messages,
        "total": total_count,
        "content_mode": content_mode,
        "overwrite_policy": overwrite_policy,
        "output_mode": output_mode,
        # P0-3 split export: how many {stem}_nl.txt twins were written (0 when
        # the option is off or no image had NL text).
        "nl_sidecars_written": nl_sidecars_written,
        # Trainer-consumability report over every written sidecar (P0 batch):
        # pairing, single-line, trigger presence, rating consistency, emptiness.
        "validation": validator.summary(),
    }


def _get_combined_export_dir() -> Path:
    # Decomposition (2026-07): this function moved one level deeper
    # (services/tag_export_service.py -> services/tag_export/sidecars.py),
    # so the backend-root anchor is parents[2] instead of parent.parent.
    # Target stays backend/data/combined-exports
    target = Path(__file__).resolve().parents[2] / "data" / "combined-exports"
    target.mkdir(parents=True, exist_ok=True)
    return target


def combined_export_path(token: str) -> Path:
    raw = str(token or "")
    if len(raw) != 32 or any(ch not in "0123456789abcdef" for ch in raw):
        raise HTTPException(status_code=404, detail="Combined export not found")
    path = _get_combined_export_dir() / f"{raw}.txt"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Combined export not found")
    return path


def export_tags_combined_request(
    request: Any,
    *,
    id_chunks: Optional[Iterable[List[int]]] = None,
    total: Optional[int] = None,
) -> Dict[str, Any]:
    """Render selected captions to one server-side file.

    This avoids the old v321 path where the browser expanded a selection token
    into a giant ID list, rendered every caption via preview calls, then built a
    huge JS string/Blob. The browser now receives a download URL.
    """
    blacklist = {str(tag or "").strip().lower() for tag in (getattr(request, "blacklist", None) or []) if str(tag or "").strip()}
    prefix = str(getattr(request, "prefix", "") or "")
    content_mode = str(getattr(request, "content_mode", "tags") or "tags").strip().lower()
    if content_mode not in VALID_CONTENT_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid content_mode: {content_mode}")

    template_options = getattr(request, "template_options", None)
    if template_options is not None and not isinstance(template_options, dict):
        if hasattr(template_options, "model_dump"):
            template_options = template_options.model_dump()
        else:
            template_options = None

    image_overrides_raw = getattr(request, "image_overrides", None) or {}
    image_overrides: Dict[int, str] = {}
    if isinstance(image_overrides_raw, dict):
        for key, value in image_overrides_raw.items():
            try:
                image_overrides[int(key)] = str(value or "")
            except (TypeError, ValueError):
                continue

    # Aurora #25c: per-image caption type + edited NL sentence (caption editor).
    image_types_map = _coerce_int_str_map(getattr(request, "image_types", None))
    nl_overrides_map = _coerce_int_str_map(getattr(request, "image_nl_overrides", None))

    normalize_tag_underscores_request = getattr(request, "normalize_tag_underscores", None)
    caption_transforms = getattr(request, "caption_transforms", None) or {}
    # P2-19 / P2-18 export-engine filters (both default off).
    training_purpose_request = str(getattr(request, "training_purpose", "") or "")
    dedupe_implications_request = bool(getattr(request, "dedupe_implications", False))

    if id_chunks is None:
        id_chunks = _iter_id_list_chunks(getattr(request, "image_ids", []) or [], EXPORT_DB_CHUNK_SIZE)
    total_count = int(total if total is not None else len(_normalize_export_image_ids(getattr(request, "image_ids", []) or [])))

    token = uuid.uuid4().hex
    export_dir = _get_combined_export_dir()
    path = export_dir / f"{token}.txt"
    tmp_path = export_dir / f"{token}.tmp"
    filename = f"sd-image-sorter-combined-{time.strftime('%Y%m%d-%H%M%S')}.{_sidecar_extension(content_mode).lstrip('.')}"

    exported = 0
    error_count = 0
    error_messages: List[str] = []
    first_line = True

    try:
        with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
            for image_id_list in id_chunks:
                images_map = db.get_images_by_ids(image_id_list)
                tags_map = db.get_image_tags_map(image_id_list)
                for image_id in image_id_list:
                    try:
                        image = images_map.get(image_id)
                        if not image:
                            error_count += 1
                            if len(error_messages) < COMBINED_EXPORT_RECENT_ERROR_LIMIT:
                                error_messages.append(f"Image {image_id} not found")
                            continue
                        if image_id in image_overrides:
                            rendered = image_overrides[image_id]
                        else:
                            rendered = build_sidecar_content(
                                image,
                                tags_map.get(image_id, []) or [],
                                content_mode=content_mode,
                                blacklist=blacklist,
                                prefix=prefix,
                                template_options=template_options,
                                normalize_tag_underscores=normalize_tag_underscores_request,
                                training_purpose=training_purpose_request,
                                dedupe_implications=dedupe_implications_request,
                            )
                        rendered = _compose_nl_for_image(
                            rendered,
                            image,
                            image_id,
                            content_mode=content_mode,
                            image_types=image_types_map,
                            nl_overrides=nl_overrides_map,
                        )
                        rendered = apply_caption_transforms(rendered, caption_transforms)
                        if not rendered:
                            continue
                        if not first_line:
                            handle.write("\n")
                        handle.write(rendered)
                        first_line = False
                        exported += 1
                    except HTTPException:
                        raise
                    except Exception as exc:
                        error_count += 1
                        if len(error_messages) < COMBINED_EXPORT_RECENT_ERROR_LIMIT:
                            error_messages.append(f"Image {image_id}: {exc}")
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return {
        "status": "ok" if error_count == 0 else ("partial" if exported else "error"),
        "token": token,
        "download_url": f"/api/tags/export-combined/download/{token}",
        "filename": filename,
        "exported": exported,
        "total": total_count,
        "error_count": error_count,
        "error_messages": error_messages,
        "content_mode": content_mode,
    }
