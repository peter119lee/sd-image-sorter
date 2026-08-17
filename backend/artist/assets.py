"""Kaloscope asset location + preparation orchestration.

Moved verbatim from backend/artist_identifier.py (decomposition 2026-07) except the
manifest lines: every callee in the dense facade-patch call graph
(_ensure_kaloscope_*_files, _fetch_artist_file, _hf_download_with_fallback,
_locate_existing_kaloscope_files, _get_artist_model_root, _project_root,
_materialize_existing_file, _modelscope_resolve_url,
_resolve_lsnet_runtime_path, _ensure_comfyui_lsnet_runtime) and every config
bind (ARTIST_MODELSCOPE_MODEL_ID, ARTIST_HF_MODEL_ID, ARTIST_KALOSCOPE_*)
resolves through _facade() at call time — a bare re-import would make the ~20
monkeypatch call-sites across the reader suites silently miss. The lazy
``from config import get_download_mirror`` is byte-verbatim (patched at origin).
"""

import logging
import os
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("sd-image-sorter.artist")


def _facade():
    """Resolve facade-owned seams/constants through artist_identifier at call time.

    Tests patch module attributes on the facade: ~10 of these free functions
    are monkeypatched on
    ``artist_identifier`` and called by the others, and the diagnostics/pin
    suites patch the facade ``__file__`` and its config bindings. A from-import
    here would freeze an independent binding those patches — and the
    ``importlib.reload(artist_identifier)`` config re-reads — silently miss.
    The lazy import avoids a facade<->submodule load cycle.
    """
    import artist_identifier

    return artist_identifier

def _locate_existing_kaloscope_files() -> Optional[Tuple[str, str]]:
    """Find an already-present Kaloscope checkpoint + class mapping, tolerantly.

    A user may have the files from a one-click Prepare (canonical
    ``kaloscope2.0/448-90.13/`` layout), a manual download, or a ``git clone``
    of the model repo (which creates a mixed-case ``Kaloscope2.0/`` directory
    that the hardcoded lowercase path misses on case-sensitive Linux
    filesystems). This probes:

      1. the canonical lowercase path (fast path, Windows/macOS),
      2. any direct child of the artist root whose name case-insensitively
         matches ``kaloscope2.0`` / ``kaloscope-2.0``,
      3. a recursive ``best_checkpoint.pth`` search anywhere under the artist
         root, pairing it with the nearest ``class_mapping.csv``.

    Returns ``(checkpoint_path, class_mapping_path)`` or ``None``.
    """
    artist_root = _facade()._get_artist_model_root()
    checkpoint_basename = PurePosixPath(_facade().ARTIST_KALOSCOPE_CHECKPOINT.replace("\\", "/")).name
    mapping_basename = PurePosixPath(_facade().ARTIST_KALOSCOPE_CLASS_MAPPING.replace("\\", "/")).name

    def _pair(checkpoint: Path) -> Optional[Tuple[str, str]]:
        # Prefer a class mapping next to the checkpoint, then one in the
        # kaloscope dir root (HF ships the mapping one level up from 448-90.13/).
        for mapping_candidate in (
            checkpoint.with_name(mapping_basename),
            checkpoint.parent.parent / mapping_basename,
        ):
            if mapping_candidate.is_file():
                return str(checkpoint.resolve()), str(mapping_candidate.resolve())
        return None

    # 1) Canonical lowercase path.
    canonical = artist_root / "kaloscope2.0" / _facade().ARTIST_KALOSCOPE_CHECKPOINT
    paired = _pair(canonical) if canonical.is_file() else None
    if paired:
        return paired

    if not artist_root.exists():
        return None

    # 2) Case-insensitive kaloscope-dir match.
    for child in sorted(artist_root.iterdir()):
        if not child.is_dir():
            continue
        normalized = child.name.lower().replace("-", "").replace("_", "").replace(".", "")
        if normalized.startswith("kaloscope"):
            checkpoint = child / _facade().ARTIST_KALOSCOPE_CHECKPOINT
            paired = _pair(checkpoint) if checkpoint.is_file() else None
            if paired:
                return paired

    # 3) Recursive search by basename (covers any layout the user placed).
    for checkpoint in sorted(artist_root.rglob(checkpoint_basename)):
        if not checkpoint.is_file():
            continue
        paired = _pair(checkpoint)
        if paired:
            return paired
        # Mapping not adjacent — fall back to the closest one under the root.
        mapping_matches = sorted(p for p in artist_root.rglob(mapping_basename) if p.is_file())
        if mapping_matches:
            return str(checkpoint.resolve()), str(mapping_matches[0].resolve())
    return None


def _ensure_kaloscope_hf_files() -> Tuple[str, str]:
    existing = _facade()._locate_existing_kaloscope_files()
    if existing:
        return existing

    local_dir = _facade()._get_artist_model_root() / "kaloscope2.0"
    local_checkpoint = local_dir / _facade().ARTIST_KALOSCOPE_CHECKPOINT
    local_mapping = local_dir / _facade().ARTIST_KALOSCOPE_CLASS_MAPPING

    if local_checkpoint.exists() and local_mapping.exists():
        return str(local_checkpoint.resolve()), str(local_mapping.resolve())

    if os.environ.get("SD_IMAGE_SORTER_DISABLE_LEGACY_MODEL_COPY") != "1":
        legacy_dir = _facade()._project_root() / "models" / "artist" / "kaloscope2.0"
        _facade()._materialize_existing_file(legacy_dir / _facade().ARTIST_KALOSCOPE_CHECKPOINT, local_checkpoint)
        _facade()._materialize_existing_file(legacy_dir / _facade().ARTIST_KALOSCOPE_CLASS_MAPPING, local_mapping)
        if local_checkpoint.exists() and local_mapping.exists():
            return str(local_checkpoint.resolve()), str(local_mapping.resolve())

    if not local_checkpoint.exists():
        _facade()._hf_download_with_fallback(
            _facade().ARTIST_HF_MODEL_ID,
            _facade().ARTIST_KALOSCOPE_CHECKPOINT,
            str(local_dir),
        )
    if not local_mapping.exists():
        _facade()._hf_download_with_fallback(
            _facade().ARTIST_HF_MODEL_ID,
            _facade().ARTIST_KALOSCOPE_CLASS_MAPPING,
            str(local_dir),
        )
    return str(local_checkpoint.resolve()), str(local_mapping.resolve())


def _ensure_kaloscope_modelscope_files() -> Tuple[str, str]:
    """Download the Kaloscope checkpoint + class mapping from ModelScope.

    Uses direct ``modelscope.cn`` resolve URLs rather than the heavyweight
    ``modelscope`` SDK, which is NOT a project dependency and is absent from
    real user installs (mirroring how SAM3 fetches from ModelScope). The files
    are written into the canonical ``kaloscope2.0/448-90.13/`` layout that
    model-health detection expects, so a ModelScope-sourced checkpoint is
    recognized exactly like a HuggingFace one.
    """
    if not _facade().ARTIST_MODELSCOPE_MODEL_ID:
        raise RuntimeError(
            "No compatible ModelScope artist model is configured. "
            "Use HuggingFace/hf-mirror or set SD_IMAGE_SORTER_ARTIST_MODELSCOPE_MODEL."
        )

    existing = _facade()._locate_existing_kaloscope_files()
    if existing:
        return existing

    local_dir = _facade()._get_artist_model_root() / "kaloscope2.0"
    local_checkpoint = local_dir / _facade().ARTIST_KALOSCOPE_CHECKPOINT
    local_mapping = local_dir / _facade().ARTIST_KALOSCOPE_CLASS_MAPPING

    if local_checkpoint.exists() and local_mapping.exists():
        return str(local_checkpoint.resolve()), str(local_mapping.resolve())

    # ModelScope hosts the checkpoint at the repo root (flat layout); fall back
    # to the HuggingFace-style versioned subpath in case a mirror reproduces it.
    checkpoint_basename = PurePosixPath(_facade().ARTIST_KALOSCOPE_CHECKPOINT.replace("\\", "/")).name
    checkpoint_remote_candidates = [checkpoint_basename, _facade().ARTIST_KALOSCOPE_CHECKPOINT]

    if not local_checkpoint.exists():
        last_error: Optional[Exception] = None
        for remote_name in checkpoint_remote_candidates:
            try:
                url = _facade()._modelscope_resolve_url(_facade().ARTIST_MODELSCOPE_MODEL_ID, remote_name)
                _facade()._fetch_artist_file(url, local_checkpoint, remote_name)
                last_error = None
                break
            except Exception as exc:  # try the next layout candidate
                last_error = exc
                logger.warning("ModelScope checkpoint fetch failed for %s: %s", remote_name, exc)
        if last_error is not None:
            raise RuntimeError(
                f"Could not download the Kaloscope checkpoint from ModelScope "
                f"'{_facade().ARTIST_MODELSCOPE_MODEL_ID}': {last_error}"
            )

    if not local_mapping.exists():
        url = _facade()._modelscope_resolve_url(_facade().ARTIST_MODELSCOPE_MODEL_ID, _facade().ARTIST_KALOSCOPE_CLASS_MAPPING)
        _facade()._fetch_artist_file(url, local_mapping, _facade().ARTIST_KALOSCOPE_CLASS_MAPPING)

    return str(local_checkpoint.resolve()), str(local_mapping.resolve())


def prepare_artist_assets(preferred_source: str = "auto") -> Dict[str, str]:
    """Ensure runtime + artist files exist, trying mirrors/fallbacks when needed."""
    runtime_path = _facade()._resolve_lsnet_runtime_path() or _facade()._ensure_comfyui_lsnet_runtime()
    errors: List[str] = []

    source_order: List[str]
    preferred = str(preferred_source or "auto").strip().lower()
    if preferred == "modelscope":
        if _facade().ARTIST_MODELSCOPE_MODEL_ID:
            source_order = ["modelscope", "huggingface"]
        else:
            logger.warning(
                "ModelScope artist source selected but no compatible ModelScope model id is configured; "
                "using the shared HuggingFace endpoint order instead."
            )
            source_order = ["huggingface"]
    elif preferred == "huggingface":
        source_order = ["huggingface"]
    else:
        try:
            from config import get_download_mirror

            mirror = get_download_mirror()
        except Exception:
            mirror = "auto"
        if mirror == "modelscope" and _facade().ARTIST_MODELSCOPE_MODEL_ID:
            source_order = ["modelscope", "huggingface"]
        else:
            source_order = ["huggingface"]

    for source in source_order:
        try:
            if source == "modelscope":
                checkpoint_path, class_mapping_path = _facade()._ensure_kaloscope_modelscope_files()
            else:
                checkpoint_path, class_mapping_path = _facade()._ensure_kaloscope_hf_files()
            return {
                "runtime_path": runtime_path,
                "checkpoint_path": checkpoint_path,
                "class_mapping_path": class_mapping_path,
                "source": source,
            }
        except Exception as exc:
            errors.append(f"{source}: {exc}")
            logger.warning("Artist asset preparation failed via %s: %s", source, exc)

    raise RuntimeError("Artist assets could not be prepared. " + " | ".join(errors))
