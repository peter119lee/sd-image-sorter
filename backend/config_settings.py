"""Settings-file IO for SD Image Sorter (split from config.py, 2026-07).

Bodies moved VERBATIM from config.py lines 169-280
(split leaf #2):
DEFAULT_THUMBNAIL_CACHE_MAX_MB / MAX_THUMBNAIL_CACHE_MAX_MB, VALID_MIRRORS,
get_download_mirror / save_download_mirror, _read_app_settings /
_write_app_settings, _normalize_thumbnail_cache_max_mb,
get_thumbnail_cache_max_mb / save_thumbnail_cache_max_mb. config.py
re-exports every moved name BY REFERENCE so ``config.<name>`` stays a live
module attribute for the historical consumers (thumbnail_cache.py,
services/disk_service.py, routers/models.py, artist/assets.py,
model_download_sources.py) and the patch seams.

Manifested lines (the ONLY non-verbatim edits): the moved bodies resolve
``DOWNLOAD_MIRROR_CONFIG_PATH`` / ``APP_SETTINGS_CONFIG_PATH`` /
``CONFIG_DIR`` through ``_cfg()`` at CALL time (12 substitutions) because
tests/test_config_env.py, tests/test_disk_service.py and
tests/test_config_pins.py patch those paths on the ``config`` module object
(report hazard H3); a module-top ``from config import <path>`` would freeze
the import-time value, missing those patches, and is the circular shape the
report forbids (config imports this module mid-file).
``DEFAULT_THUMBNAIL_CACHE_MAX_MB`` / ``MAX_THUMBNAIL_CACHE_MAX_MB`` are
OWNED here (not facade-resolved) because
``_normalize_thumbnail_cache_max_mb``'s def-time default argument cannot be
resolved lazily; a full patch-surface census showed nothing patches them
and nothing in config.py's remaining body reads them.
"""
import json
import logging
import os

# NOTE(decomposition): keep the historical logger channel ("config") so log
# routing and output stay identical to the pre-split single-file module.
logger = logging.getLogger("config")


def _cfg():
    """Resolve the patched path constants through the config facade at call
    time.

    tests/test_config_env.py, tests/test_disk_service.py and
    tests/test_config_pins.py monkeypatch APP_SETTINGS_CONFIG_PATH /
    DOWNLOAD_MIRROR_CONFIG_PATH / CONFIG_DIR on the ``config`` module object;
    an import-time ``from config import <path>`` here would miss those
    patches (similarity_vector_cache._svc() precedent). The lazy import also
    avoids the config <-> config_settings cycle (config imports this module
    mid-file).
    """
    import config

    return config


DEFAULT_THUMBNAIL_CACHE_MAX_MB: int = 500
MAX_THUMBNAIL_CACHE_MAX_MB: int = 102400


VALID_MIRRORS = ("auto", "hf-mirror", "modelscope")


def get_download_mirror() -> str:
    """Return the persisted download mirror, defaulting to "auto".

    Reads CONFIG_DIR/download-mirror.json. Logs (rather than swallows) any
    read error so config corruption is surfaced and not silently masked.
    """
    if not _cfg().DOWNLOAD_MIRROR_CONFIG_PATH.exists():
        return "auto"
    import json as _json
    try:
        raw = _cfg().DOWNLOAD_MIRROR_CONFIG_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "Could not read download mirror config %s: %s; defaulting to 'auto'",
            _cfg().DOWNLOAD_MIRROR_CONFIG_PATH,
            exc,
        )
        return "auto"
    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        logger.warning(
            "Download mirror config %s is corrupt (%s); defaulting to 'auto'",
            _cfg().DOWNLOAD_MIRROR_CONFIG_PATH,
            exc,
        )
        return "auto"
    mirror = str(data.get("mirror", "auto")).strip().lower()
    if mirror not in VALID_MIRRORS:
        logger.warning(
            "Download mirror config has unknown value %r; defaulting to 'auto'",
            mirror,
        )
        return "auto"
    return mirror


def save_download_mirror(mirror: str) -> None:
    mirror = str(mirror).strip().lower()
    if mirror not in VALID_MIRRORS:
        mirror = "auto"
    _cfg().CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _cfg().DOWNLOAD_MIRROR_CONFIG_PATH.write_text(
        json.dumps({"mirror": mirror}, indent=2),
        encoding="utf-8",
    )


def _read_app_settings() -> dict:
    if not _cfg().APP_SETTINGS_CONFIG_PATH.exists():
        return {}
    try:
        raw = _cfg().APP_SETTINGS_CONFIG_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read app settings %s: %s", _cfg().APP_SETTINGS_CONFIG_PATH, exc)
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("App settings file %s is corrupt (%s); using defaults", _cfg().APP_SETTINGS_CONFIG_PATH, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write_app_settings(settings: dict) -> None:
    _cfg().CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _cfg().APP_SETTINGS_CONFIG_PATH.write_text(
        json.dumps(settings, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _normalize_thumbnail_cache_max_mb(value: object, *, default: int = DEFAULT_THUMBNAIL_CACHE_MAX_MB) -> int:
    try:
        max_mb = int(value)
    except (TypeError, ValueError):
        return default
    if max_mb < 0:
        return default
    return min(max_mb, MAX_THUMBNAIL_CACHE_MAX_MB)


def get_thumbnail_cache_max_mb() -> int:
    raw_env = os.environ.get("SD_IMAGE_SORTER_THUMBNAIL_CACHE_MAX_MB")
    if raw_env is not None:
        try:
            env_value = int(raw_env)
        except ValueError as exc:
            raise ValueError(
                f"Invalid SD_IMAGE_SORTER_THUMBNAIL_CACHE_MAX_MB: expected integer, got {raw_env!r}"
            ) from exc
        if env_value < 0:
            raise ValueError("Invalid SD_IMAGE_SORTER_THUMBNAIL_CACHE_MAX_MB: expected integer >= 0")
        return min(env_value, MAX_THUMBNAIL_CACHE_MAX_MB)

    settings = _read_app_settings()
    return _normalize_thumbnail_cache_max_mb(settings.get("thumbnail_cache_max_mb"))


def save_thumbnail_cache_max_mb(max_mb: int) -> int:
    normalized = _normalize_thumbnail_cache_max_mb(max_mb)
    settings = _read_app_settings()
    settings["thumbnail_cache_max_mb"] = normalized
    _write_app_settings(settings)
    return normalized
