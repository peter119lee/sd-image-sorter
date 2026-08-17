"""Migration 043: let the scan notice a sidecar that changed after indexing.

The scan's change detector was ``(source_mtime_ns, source_size)`` of the image
alone. A ``.txt`` caption written or edited *after* the image was indexed
changes neither, so ``_is_unchanged_scan_hit`` reported the row as unchanged and
the new text was never read — not by that scan, not by any later one. Measured
on the owner's library: all 5,242 rows whose file still exists have a ``.txt``
beside them, and 5,214 of those sidecars are newer than the image.

``sidecar_fingerprint`` stores a digest of the (name, mtime_ns, size) of the
sidecars that were present the last time this row was parsed, so the scan can
compare it against the current state of the disk.

The column is deliberately additive: this migration only runs
``ALTER TABLE ... ADD COLUMN`` when the column is absent and never reads,
rewrites or clears an existing row, so the first run against a populated
production database leaves every stored value untouched and simply makes the new
column read NULL everywhere. NULL means "never fingerprinted", which
``image_manager_gates`` treats as a re-read trigger only for rows that actually
have a sidecar on disk — an existing library with no sidecars is not re-parsed.

No index: the column is only ever compared for equality against one row already
being fetched by path, never searched or grouped.
"""
from __future__ import annotations

import sqlite3

from migrations._schema_common import table_exists


VERSION = 43
NAME = "sidecar_fingerprint_column"


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def apply(conn: sqlite3.Connection) -> None:
    """Add the sidecar_fingerprint column to images (idempotent, additive)."""
    if not table_exists(conn, "images"):
        return
    if _column_exists(conn, "images", "sidecar_fingerprint"):
        return
    conn.execute("ALTER TABLE images ADD COLUMN sidecar_fingerprint TEXT")
