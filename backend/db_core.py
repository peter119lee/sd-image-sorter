"""
Foundation layer for the SQLite database modules.

Holds the datetime adapter registration, prompt-mode constants, schema-version
constants, the in-memory facet/tags cache state, and the shared connection
factory dispatch.

Every other ``db_*`` module imports from here; this module must NOT import from
``database`` or any of the higher-level ``db_*`` modules so the dependency graph
stays acyclic. The concrete connection factory is *injected* by ``database`` at
import time via :func:`set_connection_provider`, which lets the higher layers
keep ``DATABASE_PATH``/``_pragmas_initialized`` patchable on the ``database``
module (the namespace the test suite monkeypatches) without db_core importing
``database``.
"""
import sqlite3
import os
import logging
import threading
from datetime import datetime
from contextlib import contextmanager
from typing import Callable, Optional

from config import DATABASE_PATH


logger = logging.getLogger(__name__)


PROMPT_MATCH_MODE_EXACT = "exact"
PROMPT_MATCH_MODE_CONTAINS = "contains"
VALID_PROMPT_MATCH_MODES = {PROMPT_MATCH_MODE_EXACT, PROMPT_MATCH_MODE_CONTAINS}

# Default for ``update_image_metadata(sidecar_caption=...)``. Distinguishes
# "the caller re-parsed the file and knows the caption (possibly None)" from
# "the caller is rewriting unrelated fields and must leave the caption alone",
# which plain None cannot express. Lives here rather than in db_images_write
# so that module keeps its stateless no-top-level-assignment contract.
KEEP_EXISTING_SIDECAR_CAPTION = object()


def _adapt_datetime_for_sqlite(value: datetime) -> str:
    """Serialize datetimes explicitly; Python 3.12 deprecates sqlite3's default adapter."""
    return value.isoformat(sep=" ")


sqlite3.register_adapter(datetime, _adapt_datetime_for_sqlite)


# ============== Tags Cache ==============
_tags_cache_lock = threading.Lock()
_tags_cache_data = None
_tags_cache_timestamp = 0
_TAGS_CACHE_TTL = 60  # seconds

# ============== Facet Cache (generators) ==============
_generators_cache_lock = threading.Lock()
_generators_cache_data = None
_generators_cache_timestamp = 0

def _invalidate_facet_caches():
    """Clear facet caches when images are added/removed/modified."""
    global _generators_cache_data, _generators_cache_timestamp
    with _generators_cache_lock:
        _generators_cache_data = None
        _generators_cache_timestamp = 0

def _invalidate_tags_cache():
    """Clear the tags cache when tags are modified."""
    global _tags_cache_data, _tags_cache_timestamp
    with _tags_cache_lock:
        _tags_cache_data = None
        _tags_cache_timestamp = 0


_pragmas_initialized: set = set()
_pragmas_lock = threading.Lock()
SCHEMA_VERSION_ROW_ID = 1
STALE_PENDING_METADATA_READ_ERROR = (
    "Scan interrupted before metadata refresh completed. Re-scan the source folder to recover this row."
)


# The concrete connection factory. ``database`` injects its own implementation
# (which reads ``database.DATABASE_PATH`` / ``database._pragmas_initialized``)
# so the test suite can keep patching those names on the ``database`` module.
_connection_provider: Optional[Callable[[], sqlite3.Connection]] = None


def set_connection_provider(provider: Optional[Callable[[], sqlite3.Connection]]) -> None:
    """Register the connection factory used by :func:`get_connection`/:func:`get_db`."""
    global _connection_provider
    _connection_provider = provider


def _default_get_connection() -> sqlite3.Connection:
    """Self-contained connection factory used when no provider is injected."""
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout=30000")  # v3.3.2: wait up to 30s on lock (large-library scan/tag vs browse contention)
        # WAL mode and other persistent PRAGMAs only need to be set once per database path
        db_path = os.path.abspath(DATABASE_PATH)
        if db_path not in _pragmas_initialized:
            with _pragmas_lock:
                if db_path not in _pragmas_initialized:
                    from db_schema import validate_database_schema_version

                    validate_database_schema_version(conn)
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
                    conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
                    _pragmas_initialized.add(db_path)
        return conn
    except Exception:
        conn.close()
        raise


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory and performance optimizations."""
    provider = _connection_provider
    if provider is not None:
        return provider()
    return _default_get_connection()


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
