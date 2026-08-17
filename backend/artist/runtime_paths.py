"""LSNet runtime location/availability for artist identification.

Moved verbatim from backend/artist_identifier.py (decomposition 2026-07) except the
manifest lines: the project-root math reads the FACADE ``__file__``
(``Path(_facade().__file__)``) because the diagnostics suite and the pins patch
``artist_identifier.__file__`` — a local ``__file__`` here would sit one level
deeper (wrong parent.parent) AND miss those patches. All other facade-patched
reads (ARTIST_LSNET_CODE_PATH, get_artist_model_dir, _get_artist_model_root,
_project_root, _copy_existing_tree, _download_and_extract_github_zip,
ARTIST_LSNET_RUNTIME_ZIP_URL, _resolve_lsnet_runtime_path,
_ensure_comfyui_lsnet_runtime) resolve through _facade() at call time. The
sys.path inject + lazy timm/lsnet_model imports are byte-verbatim (pinned).
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

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

def _resolve_lsnet_runtime_path() -> Optional[str]:
    candidates = []
    if _facade().ARTIST_LSNET_CODE_PATH:
        candidates.append(_facade().ARTIST_LSNET_CODE_PATH)

    artist_root = Path(_facade().get_artist_model_dir())
    candidates.extend([
        artist_root / "comfyui-lsnet-runtime",
        artist_root / "comfyui-lsnet",
        artist_root / "lsnet-test",
    ])
    # Legacy pre-migration locations under the repo. Skipped when the env opts
    # out of legacy model locations (the hermetic E2E harness sets
    # SD_IMAGE_SORTER_DISABLE_LEGACY_MODEL_COPY=1) so a developer's real
    # models/artist/ checkout can't shadow the data-dir runtime. Production
    # never sets the flag, so legacy installs keep resolving exactly as before.
    if os.environ.get("SD_IMAGE_SORTER_DISABLE_LEGACY_MODEL_COPY") != "1":
        project_root = Path(_facade().__file__).resolve().parent.parent
        candidates.extend([
            project_root / "models" / "artist" / "comfyui-lsnet",
            project_root / "models" / "artist" / "lsnet-test",
            project_root / "models" / "artist" / "comfyui-lsnet-runtime",
            project_root / "third_party" / "comfyui-lsnet",
            project_root / "third_party" / "lsnet-test",
        ])

    for candidate in candidates:
        candidate_path = Path(candidate).expanduser().resolve()
        if candidate_path.exists() and ((candidate_path / "model").exists() or (candidate_path / "lsnet_model").exists()):
            return str(candidate_path)
    return None


def _get_artist_model_root() -> Path:
    target = Path(_facade().get_artist_model_dir())
    target.mkdir(parents=True, exist_ok=True)
    return target


def _ensure_comfyui_lsnet_runtime() -> str:
    artist_root = _facade()._get_artist_model_root()
    target_dir = artist_root / "comfyui-lsnet-runtime"
    if (target_dir / "lsnet_model").exists():
        return str(target_dir)

    if os.environ.get("SD_IMAGE_SORTER_DISABLE_LEGACY_MODEL_COPY") != "1":
        legacy_dir = _facade()._project_root() / "models" / "artist" / "comfyui-lsnet-runtime"
        if _facade()._copy_existing_tree(legacy_dir, target_dir, "lsnet_model"):
            return str(target_dir.resolve())

    logger.info("Downloading comfyui-lsnet runtime into %s", target_dir)
    _facade()._download_and_extract_github_zip(_facade().ARTIST_LSNET_RUNTIME_ZIP_URL, target_dir)
    return str(target_dir)


def _has_lsnet_runtime() -> bool:
    runtime_path = _facade()._resolve_lsnet_runtime_path()
    if not runtime_path:
        try:
            runtime_path = _facade()._ensure_comfyui_lsnet_runtime()
        except Exception:
            return False

    if runtime_path not in sys.path:
        sys.path.insert(0, runtime_path)

    try:
        import timm  # noqa: F401
        try:
            from lsnet_model import lsnet_artist  # noqa: F401
        except ImportError as inner_exc:
            if isinstance(inner_exc, ModuleNotFoundError) and inner_exc.name != "lsnet_model":
                return False
            from model import lsnet_artist  # noqa: F401
        return True
    except ImportError:
        return False
